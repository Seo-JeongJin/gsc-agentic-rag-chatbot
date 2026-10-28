"""
관리자 대시보드용 API 라우터 — 로그인/로그아웃, 대시보드 조회, 로그 분석, 카테고리 제안 엔드포인트.

main.py에 직접 다 적지 않고 별도 파일로 분리한 이유: main.py는 "사용자용 챗봇 요청 처리"라는
하나의 책임만 갖고, 관리자 쪽은 완전히 다른 책임(인증, 통계조회)이라 섞으면 파일이 커지고
역할 구분이 흐려짐. FastAPI의 APIRouter로 만들어서 main.py가 한 줄로 등록(include_router)만 하면 됨.
"""

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel

from db.admin_service import login as admin_login
from db.admin_service import logout as admin_logout
from db.admin_service import verify_session
from db.analysis_service import get_insight_history, get_latest_insight, run_analysis
from db.batch_analysis import (
    approve_proposal,
    get_pending_proposals,
    reject_proposal,
    run_category_suggestion,
)
from db.dashboard_service import get_category_stats, get_overview_stats, get_questions

router = APIRouter(prefix="/admin")

ADMIN_COOKIE_NAME = "admin_session_token"


class LoginRequest(BaseModel):
    username: str
    password: str


async def require_admin(admin_session_token: str | None = Cookie(default=None)) -> int:
    """
    FastAPI 의존성(Dependency) — 이후 만들 모든 관리자 전용 엔드포인트가
    `admin_id: int = Depends(require_admin)` 형태로 이 함수를 가져다 쓰면,
    로그인 안 된 요청은 이 함수 단계에서 401로 자동 차단되고, 로그인 된 요청만
    실제 엔드포인트 코드까지 도달함 — 매 엔드포인트마다 인증 체크를 따로 안 써도 됨.
    """
    admin_id = await verify_session(admin_session_token)
    if admin_id is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return admin_id


@router.post("/login")
async def login_endpoint(payload: LoginRequest, response: Response) -> dict:
    """아이디/비밀번호 확인 후, 성공하면 세션 토큰을 쿠키로 내려줌(HttpOnly — JS에서 못 읽게).
    실패하면 401 — 아이디가 없는 경우와 비밀번호가 틀린 경우를 구분하지 않음(정보 유출 방지)."""
    token = await admin_login(payload.username, payload.password)
    if token is None:
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")

    response.set_cookie(
        key=ADMIN_COOKIE_NAME,
        value=token,
        httponly=True,  # 자바스크립트가 document.cookie로 이 값을 읽지 못하게 — XSS로 토큰 탈취 방지
        samesite="lax",
        max_age=24 * 60 * 60,
    )
    return {"status": "ok"}


@router.post("/logout")
async def logout_endpoint(response: Response, admin_session_token: str | None = Cookie(default=None)) -> dict:
    """현재 세션 토큰을 DB에서 즉시 삭제하고, 브라우저 쿠키도 같이 지움."""
    if admin_session_token:
        await admin_logout(admin_session_token)
    response.delete_cookie(ADMIN_COOKIE_NAME)
    return {"status": "ok"}


@router.get("/me")
async def me_endpoint(admin_id: int = Depends(require_admin)) -> dict:
    """로그인 상태 확인용 테스트 엔드포인트 — require_admin이 제대로 막아주는지 검증하는 목적.
    실제 대시보드 데이터 엔드포인트들도 전부 이 require_admin을 이렇게 가져다 쓰면 됨."""
    return {"admin_id": admin_id}


# ===== 아래부터는 조회 전용 대시보드 데이터 — 전부 LLM 호출 없이 순수 DB 조회/집계만 함 =====


@router.get("/questions")
async def questions_endpoint(
    limit: int = 50,
    offset: int = 0,
    needs_review: bool | None = None,
    _admin_id: int = Depends(require_admin),
) -> list[dict]:
    """질문 로그 목록(최신순, 페이지네이션). 프론트에서 ?limit=20&offset=40 같은 식으로 페이지 넘김.
    ?needs_review=true를 붙이면 "답변 못한 질문" 전용 화면처럼 그 질문들만 필터링."""
    return await get_questions(limit=limit, offset=offset, needs_review=needs_review)


@router.get("/stats/categories")
async def category_stats_endpoint(_admin_id: int = Depends(require_admin)) -> list[dict]:
    """카테고리(대/소분류)별 질문 수 집계 — categories 테이블 전체 기준이라 질문이 0건인
    카테고리도 빠짐없이 나옴(관리자가 카테고리 제안 승인/거절 시 지금 어떤 카테고리가 있는지
    전체를 봐야 해서). 미분류(NULL) 질문 수는 여기가 아니라 /stats/overview에 있음."""
    return await get_category_stats()


@router.get("/stats/overview")
async def overview_stats_endpoint(_admin_id: int = Depends(require_admin)) -> dict:
    """세션/재방문율, 사용자평가, 미분류질문, 답변못한질문 — 요약 통계 한 번에.
    미분류 질문 수의 의미는 db/dashboard_service.py의 get_overview_stats 설명 참고."""
    return await get_overview_stats()


# ===== "로그 분석하기" — 여기서부터는 LLM을 호출함(분석 버튼 눌렀을 때만, 평소엔 저장된 것만 읽음) =====


@router.post("/analyze")
async def analyze_endpoint(_admin_id: int = Depends(require_admin)) -> dict:
    """"로그 분석하기" 버튼이 호출. 지난 분석 이후 새로 생긴 "답변 못한 질문"이 있을 때만
    LLM을 호출해서 결과를 dashboard_insights에 새 행으로 저장하고, 없으면 LLM을 부르지 않고
    기존 결과를 cached=true로 그대로 돌려줌. 과거 분석 결과는 지워지지 않고 이력으로 남음."""
    return await run_analysis()


@router.get("/insights")
async def latest_insight_endpoint(_admin_id: int = Depends(require_admin)) -> dict | None:
    """평소 대시보드 로딩 시 호출 — 저장된 분석 결과 중 최신 것만 읽음. LLM 호출 없음.
    아직 한 번도 분석 버튼을 안 눌렀으면 null 반환."""
    return await get_latest_insight()


@router.get("/insights/history")
async def insight_history_endpoint(
    limit: int = 20,
    _admin_id: int = Depends(require_admin),
) -> list[dict]:
    """과거 분석 결과들을 최신순으로 — "지난 분석 더보기"용. LLM 호출 없음."""
    return await get_insight_history(limit=limit)


# ===== 카테고리 제안(T6) — "로그 분석하기"와 별개 버튼/엔드포인트. 데이터 소스(기타 분류
# 질문)와 "신규 데이터 있는지" 판단 기준이 서로 달라서 하나로 합치지 않음(261021_구현스펙.md 참고) =====


@router.post("/category-suggestions/analyze")
async def category_suggestions_analyze_endpoint(_admin_id: int = Depends(require_admin)) -> dict:
    """"카테고리 제안 확인하기" 버튼이 호출. 기타로 분류된 질문이 너무 적으면 LLM 호출
    자체를 안 하고 바로 반환(run_category_suggestion 내부의 임계치 체크)."""
    return await run_category_suggestion()


@router.get("/category-suggestions")
async def category_suggestions_endpoint(_admin_id: int = Depends(require_admin)) -> list[dict]:
    """대기 중(pending)인 카테고리 제안 목록 — 승인/거절 전까지 여기 남아있음. LLM 호출 없음."""
    return await get_pending_proposals()


@router.post("/category-suggestions/{proposal_id}/approve")
async def approve_category_suggestion_endpoint(
    proposal_id: int, _admin_id: int = Depends(require_admin)
) -> dict:
    """승인 — 이 순간에만 실제 categories 테이블에 새 행이 추가됨."""
    try:
        return await approve_proposal(proposal_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/category-suggestions/{proposal_id}/reject")
async def reject_category_suggestion_endpoint(
    proposal_id: int, _admin_id: int = Depends(require_admin)
) -> dict:
    """거절 — categories에는 아무 변화 없음, 제안 상태만 'rejected'로 남음."""
    try:
        return await reject_proposal(proposal_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
