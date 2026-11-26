"""
API 응답 모델 — "이 API는 이런 칸을 돌려준다"를 적어 둔 설계도(Pydantic 모델). (2026-11-26 추가)

왜 따로 두나: 전엔 /api/ask 말고는 응답이 그냥 dict라서 Swagger(/docs)에 응답이 빈 객체({})로만 보였음.
API에 response_model로 붙이면 Swagger가 칸 이름·타입을 보여주고(팀원 실습용 API 명세서), 나가기 전에 모양도 검사함.

⚠️ 모델은 설명서이면서 "응답 필터"이기도 함 — 여기 적지 않은 칸은 응답에서 지워지고, 타입이 안 맞으면 500.
그래서 칸 이름·NULL 허용(`| None`)은 db/ 쪽 SELECT 문과 schema.sql에 맞춰 적었고, 빠진 칸이 없는지는
tests/integration/test_response_shapes.py가 확인함. 서비스 함수가 돌려주는 칸을 바꾸면 여기도 같이 고칠 것.
요청 모델(AskRequest 등)과 /api/ask의 AskResponse는 원래 자리인 main.py에 그대로 둠.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class StatusResponse(BaseModel):
    """처리만 하고 돌려줄 데이터가 없는 API의 공통 응답 — 평가·대화 초기화·로그인·로그아웃."""
    status: str  # 항상 "ok" (실패는 4xx 오류로 따로 나감)


class MeResponse(BaseModel):
    """GET /admin/me — 로그인 상태 확인."""
    admin_id: int


class QuestionLog(BaseModel):
    """GET /admin/questions의 한 줄 — dashboard_service.get_questions의 SELECT 칸 그대로."""
    id: int
    session_id: str
    question_text: str
    answer_text: str | None
    tool_call_count: int
    guardrail_passed: bool | None    # NULL = 검토 전·검토 실패·검토 안 하는 경로
    needs_review: bool
    review_reason: str | None        # needs_review가 TRUE일 때만 값이 있음(schema.sql CHECK 제약)
    user_rating: int | None          # 위젯 👍=5, 👎=1, 평가 안 하면 NULL
    created_at: datetime
    category_major: str | None       # 아직 분류 안 된 질문은 NULL(LEFT JOIN)
    category_minor: str | None


class CategoryStat(BaseModel):
    """GET /admin/stats/categories의 한 줄 — 카테고리 하나와 그 질문 수(0건인 카테고리도 나옴)."""
    category_major: str
    category_minor: str
    question_count: int


class OverviewStats(BaseModel):
    """GET /admin/stats/overview — 요약 통계 한 번에(dashboard_service.get_overview_stats)."""
    total_sessions: int
    returning_sessions: int
    returning_rate_percent: float
    total_questions: int
    rated_count: int
    average_rating: float | None     # 평가가 하나도 없으면 NULL
    unclassified_count: int
    needs_review_count: int
    # {"insufficient_info": 3, …} — 키가 비는 일은 없음(needs_review=TRUE면 review_reason이 반드시 있음, schema.sql CHECK 제약)
    needs_review_by_reason: dict[str, int]


class InsightContent(BaseModel):
    """로그 분석 결과의 content 칸(JSONB) — analysis_service._save_insight가 저장하는 칸.
    extra="allow": content는 "구조가 바뀌어도 받을 수 있게" JSONB로 둔 칸이라(schema.sql), 여기 없는 칸이 생겨도 지우지 않고 그대로 내보냄."""
    model_config = ConfigDict(extra="allow")

    summary: str
    based_on_count: int              # 지금까지 쌓인 답변 못한 질문 전체 수
    new_since_last_count: int        # 이번 분석에서 LLM에 넣은 질문 수
    last_question_id: int            # 이번 분석이 본 마지막 질문 id(다음 분석은 이 다음부터)
    remaining_count: int             # 한 번에 다 못 봐서 남은 수(0이 아니면 "다시 눌러 이어서 분석")


class Insight(BaseModel):
    """저장된 로그 분석 결과 한 건 — GET /admin/insights, /admin/insights/history."""
    id: int
    insight_type: str
    content: InsightContent
    generated_at: datetime


class AnalyzeResponse(Insight):
    """POST /admin/analyze — 분석 결과 한 건 + 이번에 새로 분석했는지 여부."""
    cached: bool                     # True = 새 질문이 없어서 LLM을 부르지 않고 지난 결과를 그대로 돌려줌


class ProposalSamples(BaseModel):
    """카테고리 제안의 sample_questions 칸(JSONB) — batch_analysis._save_proposal이 {이유, 질문 예시}로 저장."""
    reason: str
    questions: list[str]


class Proposal(BaseModel):
    """카테고리 제안 한 건 — 제안 목록·승인·거절, 제안 분석 결과(proposed) 안에서 같이 씀."""
    id: int
    proposed_name: str               # "대분류 > 소분류"
    related_question_count: int
    sample_questions: ProposalSamples | None   # 컬럼이 NULL 허용(JSONB)이라 None도 받음
    status: str                      # pending / approved / rejected
    created_at: datetime


class SuggestionResult(BaseModel):
    """POST /admin/category-suggestions/analyze — status에 따라 같이 오는 칸이 다름:
    - insufficient_data: count, required (기타 질문이 너무 적어 LLM을 안 부름)
    - no_pattern: checked_count
    - already_category / already_proposed / already_rejected: proposed_name
    - proposed: proposal
    모델 하나에 전부 선택 칸으로 두고, API 쪽에서 response_model_exclude_none=True로 해당 없는 칸(None)을 빼서
    지금과 같은 모양 그대로 내보냄(대시보드 CategorySuggestions.jsx가 이 모양을 씀)."""
    status: str
    count: int | None = None
    required: int | None = None
    checked_count: int | None = None
    proposed_name: str | None = None
    proposal: Proposal | None = None
