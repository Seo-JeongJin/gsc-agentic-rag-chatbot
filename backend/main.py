"""
FastAPI 진입점 — HTTP 요청을 받아 가드레일→세션처리→메모리조회→Agent루프→로그저장
순서로 이어주는 "창구" 역할. 실제 판단(도구 호출 여부 등)은 agent_loop.py가 하고,
여기는 그 앞뒤로 필요한 단계들을 순서대로 호출만 함.
"""

from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agent_loop
import guardrail
import mcp_client
from admin_api import router as admin_router
from db.category_tagging import tag_question
from db.log_service import save_log, set_rating
from db.memory_service import fetch_recent_history
from db.session_service import process_session, reset_conversation
from output_guardrail import check_output
from system_prompt import ESCALATION_CONTACT_MESSAGE, SYSTEM_PROMPT


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 0번 단계: 앱 시작 시 MCP 서버에 연결하고 도구 목록을 캐싱
    await mcp_client.connect_and_cache_tools()
    yield
    await mcp_client.close_connection()


app = FastAPI(lifespan=lifespan)

# 위젯은 학과 홈페이지(다른 출처)에 삽입되는 코드라, 거기서 보내는 요청을 브라우저가
# 막지 않도록 CORS 허용이 필요함. 로컬 테스트 단계라 전체 허용 — 실제 배포 시
# allow_origins를 학과 홈페이지 실제 도메인으로 좁혀야 함(지금은 미확정이라 전체 허용).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 관리자 라우터 등록 — /admin/login, /admin/logout, /admin/me (그리고 앞으로 추가될
# 대시보드 조회 엔드포인트들)가 전부 이 한 줄로 main.py에 연결됨.
app.include_router(admin_router)


class AskRequest(BaseModel):
    question_text: str
    session_id: str


class AskResponse(BaseModel):
    answer_text: str
    question_id: int | None = None  # 가드레일에서 막힌 경우는 로그 자체가 없어서 None


class ResetRequest(BaseModel):
    session_id: str


class RateRequest(BaseModel):
    question_id: int
    rating: int  # 1~5. 위젯은 썸업=5, 썸다운=1로 매핑해서 보냄.


@app.post("/api/reset_conversation")
async def reset_conversation_endpoint(payload: ResetRequest) -> dict:
    """프론트의 "새 대화 시작" 버튼이 호출. session_id(재방문자 기록)는 그대로 두고,
    대화 리셋 시각만 갱신 — 이후 fetch_recent_history가 이 시점 이전 대화를 숏텀메모리로 안 가져옴."""
    await reset_conversation(payload.session_id)
    return {"status": "ok"}


@app.post("/api/rate")
async def rate_endpoint(payload: RateRequest) -> dict:
    """프론트의 평가(👍/👎) 버튼이 호출. questions.user_rating을 갱신."""
    await set_rating(payload.question_id, payload.rating)
    return {"status": "ok"}


@app.post("/api/ask", response_model=AskResponse)
async def ask(payload: AskRequest, background_tasks: BackgroundTasks) -> AskResponse:
    # 1번: 요청 수신 (FastAPI+Pydantic이 이미 question_text/session_id 형식 검증을 끝낸 상태)

    # 3번: 입력 가드레일 — 통과 못 하면 LLM 호출 자체를 안 함(자원 절약)
    passed, reason = guardrail.check_input(payload.question_text, payload.session_id)
    if not passed:
        return AskResponse(answer_text=reason, question_id=None)

    # 2번: 세션 처리 (재방문 여부 upsert)
    await process_session(payload.session_id)

    # 4번: 숏텀 메모리 조회 (루프 시작 전 1회만)
    history = await fetch_recent_history(payload.session_id)

    # 5~12번: Agent 루프 (지금은 가짜 LLM, 다음 주 실제 vLLM으로 교체 예정)
    result = await agent_loop.run_agent_loop(payload.question_text, history)

    answer_text = result["answer_text"]
    needs_review = result["needs_review"]
    review_reason = result["review_reason"]
    guardrail_passed = None

    # 13번: 출력 가드레일 — 루프가 이미 상한도달로 폴백 문구를 낸 경우는 검사할 "진짜 답변"
    # 자체가 없어서 스킵(needs_review가 이미 True). 진짜 답변이 나온 경우만 근거비교.
    # 근거 = system_prompt.py의 고정 지침(전화번호 등 검색 없이도 쓸 수 있는 사실) + 루프 중
    # 실제로 검색된 문서/과거답변 텍스트 — 둘 중 하나라도 근거가 되면 통과시켜야 오탐이 줄어듦.
    if not needs_review:
        grounded_context = SYSTEM_PROMPT + "\n" + result["retrieved_context"]
        guardrail_passed, fail_reason = check_output(answer_text, grounded_context)
        if not guardrail_passed:
            answer_text = ESCALATION_CONTACT_MESSAGE
            needs_review = True
            review_reason = "guardrail_failed"

    # 14번: 로그 저장 — 반환된 id를 프론트에 같이 내려줘야 나중에 /api/rate가 "어느 질문"인지 알 수 있음
    question_id = await save_log(
        session_id=payload.session_id,
        question_text=payload.question_text,
        answer_text=answer_text,
        tool_call_count=result["tool_call_count"],
        guardrail_passed=guardrail_passed,
        needs_review=needs_review,
        review_reason=review_reason,
    )

    # 15번: 카테고리 태깅 백그라운드 호출 — 분류 LLM 호출이 끝나길 기다리지 않고 바로 16번(응답)으로 넘어감.
    background_tasks.add_task(tag_question, question_id, payload.question_text, answer_text)

    # 16번: 응답 반환 (지금은 비스트리밍 단순 응답 — SSE 스트리밍은 실제 vLLM 연결 시 추가)
    return AskResponse(answer_text=answer_text, question_id=question_id)


# 관리자 대시보드(React 빌드 결과물) 서빙 — 반드시 다른 라우트들보다 "뒤에" 등록해야 함.
# Starlette은 라우트를 등록된 순서대로 매칭하는데, 이 mount("/")는 "그 외 모든 경로"를
# 받아버리는 캐치올이라 위에 있으면 /admin/*, /api/*가 전부 여기 걸려버림.
# 같은 서버(8080)에서 화면과 API를 같이 내려주므로 CORS/쿠키 문제 자체가 생기지 않음
# (프론트 React 코드가 fetch("/admin/...")로 호출해도 "같은 출처" 요청이 됨).
app.mount("/", StaticFiles(directory="static_dashboard", html=True), name="dashboard")
