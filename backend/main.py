"""
FastAPI 진입점 — HTTP 요청을 받아 가드레일→세션처리→메모리조회→Agent루프→로그저장
순서로 이어주는 "창구" 역할. 실제 판단(도구 호출 여부 등)은 agent_loop.py가 하고,
여기는 그 앞뒤로 필요한 단계들을 순서대로 호출만 함.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import agent_loop
import guardrail
import mcp_client
from admin_api import router as admin_router
from db.category_tagging import tag_question
from db.log_service import save_log, save_question_embedding, set_rating
from db.memory_service import fetch_recent_history
from db.session_service import process_session, reset_conversation
from llm_client import LLMError
from output_guardrail import build_grounded_context, check_output
from system_prompt import ESCALATION_CONTACT_MESSAGE

# uvicorn 로그에 같이 찍히도록 uvicorn의 로거를 씀
logger = logging.getLogger("uvicorn.error")


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

# 관리자 라우터 등록 — admin_api.py에 있는 /admin/* 엔드포인트 전부(로그인/로그아웃,
# 대시보드 조회, 로그 분석, 카테고리 제안)가 이 한 줄로 main.py에 연결됨.
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
    # 1~5만 허용 — 범위 밖이면 FastAPI가 엔드포인트 실행 전에 422로 거절함.
    # (수정 전에는 아무 정수나 받아서 DB의 CHECK 제약에 걸려 500이 났음, 리뷰 9번)
    # 위젯은 썸업=5, 썸다운=1로 매핑해서 보냄.
    rating: int = Field(ge=1, le=5)
    # 소유 확인용 — "이 질문을 한 세션"이 보낸 평가만 반영하기 위해 질문할 때와 같은 session_id를 받음.
    # 이게 없으면 순차 증가하는 question_id만으로 남의 질문 평점을 조작할 수 있었음(리뷰 9번).
    session_id: str


@app.post("/api/reset_conversation")
async def reset_conversation_endpoint(payload: ResetRequest) -> dict:
    """프론트의 "새 대화 시작" 버튼이 호출. session_id(재방문자 기록)는 그대로 두고,
    대화 리셋 시각만 갱신 — 이후 fetch_recent_history가 이 시점 이전 대화를 숏텀메모리로 안 가져옴."""
    await reset_conversation(payload.session_id)
    return {"status": "ok"}


@app.post("/api/rate")
async def rate_endpoint(payload: RateRequest) -> dict:
    """프론트의 평가(👍/👎) 버튼이 호출. questions.user_rating을 갱신.

    질문 id와 session_id가 둘 다 맞는 행이 없으면 404 — "없는 질문"과 "남의 질문"을 구분하지
    않고 같은 응답을 주는 이유: 구분해주면 "이 번호의 질문은 존재한다"는 정보가 새어나감."""
    updated = await set_rating(payload.question_id, payload.session_id, payload.rating)
    if not updated:
        raise HTTPException(status_code=404, detail="평가할 질문을 찾을 수 없습니다.")
    return {"status": "ok"}


async def store_question_embedding(question_id: int, question_text: str) -> None:
    """백그라운드 작업: 질문 임베딩을 MCP 서버에서 받아 저장 → 이후 search_past_answers(롱텀 메모리)에 걸리게 됨.
    실패해도 예외를 밖으로 던지지 않음 — 이미 답변은 나간 뒤라, 실패해도 "이 질문이 과거답변 검색에 안 걸림"뿐."""
    try:
        embedding = await mcp_client.embed_text(question_text)
        await save_question_embedding(question_id, embedding)
    except Exception as e:  # noqa: BLE001 — 백그라운드 실패가 서비스에 영향 주면 안 됨
        logger.warning("질문 임베딩 저장 실패(question_id=%s): %s", question_id, e)


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

    # 5~12번: Agent 루프 (LLM_MODE=mock이면 가짜 LLM, vllm이면 실제 vLLM — llm_client.py)
    # LLM 서버가 재시도까지 다 실패하면 503 — 답변 자체가 없으니 로그도 안 남김(위젯은 "잠시 후 다시 시도" 표시).
    # 500이 아니라 503인 이유: 우리 코드 버그가 아니라 "GPU 서버가 일시적으로 응답 없음"이라는 뜻을 정확히 전달.
    try:
        result = await agent_loop.run_agent_loop(payload.question_text, history)
    except LLMError as e:
        logger.error("LLM 서버 호출 실패 — 503 응답: %s", e)
        raise HTTPException(status_code=503, detail="답변 서버가 잠시 응답하지 않습니다. 잠시 후 다시 시도해 주세요.") from e

    answer_text = result["answer_text"]
    needs_review = result["needs_review"]
    review_reason = result["review_reason"]
    guardrail_passed = None

    # 13번: 출력 가드레일 — 루프가 이미 상한도달로 폴백 문구를 낸 경우는 검사할 "진짜 답변"
    # 자체가 없어서 스킵(needs_review가 이미 True). 진짜 답변이 나온 경우만 근거비교.
    # 근거 = 고정 사실(연락처·취업실적) + 이전 턴 + 이번 질문 + 이번 루프의 검색 결과 본문(build_grounded_context 참고).
    # (2026-10-28 리뷰 4번: 전엔 SYSTEM_PROMPT 전체를 근거로 넣어서 판별력이 거의 없었음)
    # 도구를 한 번도 안 쓴 답변(거절·안내 등)만 키워드 겹침 검사를 생략하고 숫자만 검사(2차 리뷰 1번 — check_output 참고).
    # 기준이 "검색 결과가 비었는지"가 아니라 "도구를 썼는지"인 이유(3차 리뷰 1번): 검색했는데 0건/실패한 뒤 LLM이 지어낸
    # 답이 환각이 가장 잘 나는 경우라 이건 반드시 검사해야 함 — 전엔 결과가 비었다는 이유로 그대로 통과했음.
    # 근거의 이전 대화는 LLM에 실제로 보낸(줄인) 것으로(3차 리뷰 5번).
    if not needs_review:
        grounded_context = build_grounded_context(
            result["retrieved_context"], result["history_used"], payload.question_text
        )
        guardrail_passed, fail_reason = check_output(
            answer_text, grounded_context, check_overlap=result["tool_call_count"] > 0
        )
        if not guardrail_passed:
            # 실패 사유를 로그로 남김 — 전엔 버려져서 나중에 가드레일을 튜닝(하네스)할 재료가 없었음
            logger.warning("출력 가드레일 실패 — %s | 질문: %s | 원래 답변: %s", fail_reason, payload.question_text, answer_text)
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

    # 15-2번: 과거답변 검색용 질문 임베딩 저장 — 정상 답변(needs_review=False) + 출력 가드레일 통과(True)인
    # 질문만(2026-10-28 결정). 에스컬레이션/가드레일 실패 답변은 어차피 search_past_answers의 검색 조건에서
    # 빠지므로 임베딩을 만들 필요가 없음. 임베딩이 느릴 수 있어서 응답을 기다리게 하지 않고 백그라운드로.
    if not needs_review and guardrail_passed:
        background_tasks.add_task(store_question_embedding, question_id, payload.question_text)

    # 16번: 응답 반환 (지금은 비스트리밍 단순 응답 — SSE 스트리밍은 실제 vLLM 연결 시 추가)
    return AskResponse(answer_text=answer_text, question_id=question_id)


# 대시보드 빌드 결과물 위치 — 실행 위치(cwd)와 상관없이 항상 이 파일(main.py) 옆의 폴더를 가리키게
# 절대경로로 계산(전엔 "static_dashboard" 상대경로라 backend/ 밖에서 실행하면 못 찾았음).
DASHBOARD_DIR = Path(__file__).resolve().parent / "static_dashboard"
# 위젯 테스트 페이지(widget-test.html → index.html) + 위젯 번들(widget.js) — Docker 빌드 때 backend/Dockerfile이 채움
WIDGET_TEST_DIR = Path(__file__).resolve().parent / "static_widget"

def mount_dashboard(target_app: FastAPI, directory: Path = DASHBOARD_DIR) -> bool:
    """관리자 대시보드(React 빌드 결과물)를 "/"에 연결. 연결했으면 True, 폴더가 없어서 건너뛰었으면 False.

    폴더가 없을 때 에러 대신 경고만 남기는 이유(2026-10-28 리뷰 4번): 빌드 결과물은 git에 안 올리기
    때문에 GitHub에서 새로 받은 컴퓨터(학과 컴퓨터 등)엔 이 폴더가 없음. 전엔 이 경우 StaticFiles가
    서버 시작 단계에서 에러를 내서, 대시보드 "화면" 하나 때문에 챗봇 API(/api/*)와 관리자 API(/admin/*)까지
    통째로 안 켜졌음. 화면이 없어도 API는 동작해야 하므로 연결만 건너뜀.
    (근본 해결 — Docker 빌드 때 대시보드도 자동 빌드 — 은 4단계 L7)
    """
    if not directory.is_dir():
        logger.warning(
            "대시보드 빌드 폴더(%s)가 없어서 관리자 화면은 연결하지 않음 — API는 정상 동작. "
            "화면이 필요하면 frontend/dashboard에서 npm install && npm run build",
            directory,
        )
        return False
    target_app.mount("/", StaticFiles(directory=directory, html=True), name="dashboard")
    return True


def mount_widget_test(target_app: FastAPI, directory: Path = WIDGET_TEST_DIR) -> bool:
    """위젯 테스트 페이지를 /widget-test/에 연결(2026-10-28 추가). 연결했으면 True, 폴더가 없으면 False.

    학과 컴퓨터에서 Node.js 없이 위젯 화면을 확인하기 위함 — 위젯 번들(widget.js)은 git에 안 올리므로
    Docker 빌드 때 만들어서 이 폴더에 넣고, backend가 같은 주소(8080)에서 내려줌. 페이지가 backend와 같은
    출처에서 열리니 위젯의 API 주소도 자동으로 이 서버가 됨. 폴더가 없으면(로컬에서 uvicorn 직접 실행 등)
    대시보드와 같은 이유로 경고만 남기고 건너뜀 — 테스트 페이지 하나 때문에 API가 안 켜지면 안 되니까."""
    if not directory.is_dir():
        logger.warning("위젯 테스트 폴더(%s)가 없어서 /widget-test는 연결하지 않음 — API는 정상 동작", directory)
        return False

    # 끝에 "/" 없이 /widget-test로 들어와도 열리게 — StaticFiles는 "/widget-test/"만 받아서 그냥 두면 404가 남.
    # 페이지 안의 상대경로(widget.js)가 맞게 풀리려면 주소가 "/"로 끝나야 해서, 내용을 바로 주지 않고 리다이렉트.
    @target_app.get("/widget-test", include_in_schema=False)
    async def widget_test_redirect() -> RedirectResponse:
        return RedirectResponse(url="/widget-test/")

    target_app.mount("/widget-test", StaticFiles(directory=directory, html=True), name="widget_test")
    return True


# 관리자 대시보드 서빙 — 반드시 다른 라우트들보다 "뒤에" 등록해야 함.
# Starlette은 라우트를 등록된 순서대로 매칭하는데, 이 mount("/")는 "그 외 모든 경로"를
# 받아버리는 캐치올이라 위에 있으면 /admin/*, /api/*가 전부 여기 걸려버림.
# 같은 서버(8080)에서 화면과 API를 같이 내려주므로 CORS/쿠키 문제 자체가 생기지 않음
# (프론트 React 코드가 fetch("/admin/...")로 호출해도 "같은 출처" 요청이 됨).
# 위젯 테스트 페이지(/widget-test)도 "/" 캐치올보다 먼저 등록해야 대시보드에 가로채이지 않음.
mount_widget_test(app)
mount_dashboard(app)
