"""
API 응답의 칸(키) 목록을 고정하는 통합 테스트 — 응답 모델을 붙이기 전/후에 같은 칸이 나오는지 확인.

왜 필요한가(2026-11-26): 응답 모델은 Swagger 설명서이면서 동시에 "응답 필터"라서, 모델에 적지 않은 칸은
응답에서 지워지고, 타입이 안 맞으면 500이 남. 대시보드가 쓰는 칸이 하나라도 빠지면 화면이 조용히 비므로,
API마다 "지금 나오는 칸이 정확히 이것"을 적어 두고 모델 추가 전에도 후에도 통과하는지 봄.
"""

import pytest

from db.admin_service import create_admin
from tests.integration.conftest import insert_question

ETC = ("기타", "기타")

QUESTION_LOG_KEYS = {
    "id", "session_id", "question_text", "answer_text", "tool_call_count", "guardrail_passed",
    "needs_review", "review_reason", "user_rating", "created_at", "category_major", "category_minor",
}
INSIGHT_KEYS = {"id", "insight_type", "content", "generated_at"}
INSIGHT_CONTENT_KEYS = {"summary", "based_on_count", "new_since_last_count", "last_question_id", "remaining_count"}
PROPOSAL_KEYS = {"id", "proposed_name", "related_question_count", "sample_questions", "status", "created_at"}


@pytest.fixture
async def admin_client(client):
    """로그인까지 마친 관리자 클라이언트."""
    await create_admin("admin", "test-password")
    res = await client.post("/admin/login", json={"username": "admin", "password": "test-password"})
    assert res.json() == {"status": "ok"}
    return client


async def test_chat_api_shapes(client, db):
    """지원자용 3개: 질문 → 평가 → 대화 초기화."""
    asked = (await client.post("/api/ask", json={"question_text": "기숙사 신청은 어떻게 해요?", "session_id": "s1"})).json()
    assert set(asked) == {"answer_text", "question_id"}

    rated = await client.post("/api/rate", json={"question_id": asked["question_id"], "rating": 5, "session_id": "s1"})
    assert rated.json() == {"status": "ok"}

    reset = await client.post("/api/reset_conversation", json={"session_id": "s1"})
    assert reset.json() == {"status": "ok"}


async def test_admin_read_api_shapes(admin_client, db):
    """관리자 조회 3개 + 로그인 확인. 비어 있을 수 있는 칸(분류 전 카테고리, 평가 없음, 검토 전)이 null로 그대로 나와야 함."""
    await insert_question(db, "분류 안 된 질문")   # category_major/minor, user_rating, guardrail_passed가 전부 null
    await insert_question(db, "답 못한 질문", needs_review=True, review_reason="insufficient_info", category=ETC)

    assert (await admin_client.get("/admin/me")).json() == {"admin_id": 1}

    rows = (await admin_client.get("/admin/questions")).json()
    assert [set(r) for r in rows] == [QUESTION_LOG_KEYS, QUESTION_LOG_KEYS]
    unclassified = next(r for r in rows if r["question_text"] == "분류 안 된 질문")
    assert (unclassified["category_major"], unclassified["user_rating"], unclassified["guardrail_passed"]) == (None, None, None)

    stats = (await admin_client.get("/admin/stats/categories")).json()
    assert all(set(s) == {"category_major", "category_minor", "question_count"} for s in stats)

    overview = (await admin_client.get("/admin/stats/overview")).json()
    assert set(overview) == {
        "total_sessions", "returning_sessions", "returning_rate_percent", "total_questions", "rated_count",
        "average_rating", "unclassified_count", "needs_review_count", "needs_review_by_reason",
    }
    assert overview["average_rating"] is None   # 평가가 하나도 없으면 null
    assert overview["needs_review_by_reason"] == {"insufficient_info": 1}

    assert (await admin_client.post("/admin/logout")).json() == {"status": "ok"}


async def test_insight_api_shapes(admin_client, db):
    """로그 분석 3개: 분석 전엔 최신 결과가 null, 분석 결과는 cached 칸이 하나 더 붙음."""
    assert (await admin_client.get("/admin/insights")).json() is None

    await insert_question(db, "실패1", needs_review=True, review_reason="insufficient_info")
    analyzed = (await admin_client.post("/admin/analyze")).json()
    assert set(analyzed) == INSIGHT_KEYS | {"cached"}
    assert set(analyzed["content"]) == INSIGHT_CONTENT_KEYS

    latest = (await admin_client.get("/admin/insights")).json()
    assert set(latest) == INSIGHT_KEYS and set(latest["content"]) == INSIGHT_CONTENT_KEYS

    history = (await admin_client.get("/admin/insights/history")).json()
    assert [set(h) for h in history] == [INSIGHT_KEYS]


async def test_category_suggestion_api_shapes(admin_client, db):
    """카테고리 제안 4개. 분석 결과는 status마다 같이 오는 칸이 다름 — 해당 없는 칸(null)이 끼어들면 안 됨
    (대시보드 CategorySuggestions.jsx와 기존 테스트가 이 모양 그대로를 씀)."""
    for i in range(5):
        await insert_question(db, f"날씨 어때요 {i}", category=ETC)   # 가짜 LLM이 "패턴 없음"으로 답하는 질문
    no_pattern = (await admin_client.post("/admin/category-suggestions/analyze")).json()
    assert no_pattern == {"status": "no_pattern", "checked_count": 5}

    for i in range(10):
        await insert_question(db, f"포트폴리오 제출해야 하나요 {i}", category=ETC)   # 과반이 포트폴리오 → 제안 생김
    proposed = (await admin_client.post("/admin/category-suggestions/analyze")).json()
    assert set(proposed) == {"status", "proposal"} and proposed["status"] == "proposed"
    assert set(proposed["proposal"]) == PROPOSAL_KEYS
    # 질문 예시는 목록이 아니라 {이유, 질문들} — batch_analysis._save_proposal이 이렇게 저장하고 대시보드도 이 모양을 읽음
    assert set(proposed["proposal"]["sample_questions"]) == {"reason", "questions"}

    again = (await admin_client.post("/admin/category-suggestions/analyze")).json()
    assert again == {"status": "already_proposed", "proposed_name": "학과소개 > 포트폴리오 제출"}

    pending = (await admin_client.get("/admin/category-suggestions")).json()
    assert [set(p) for p in pending] == [PROPOSAL_KEYS]

    proposal_id = proposed["proposal"]["id"]
    approved = (await admin_client.post(f"/admin/category-suggestions/{proposal_id}/approve")).json()
    assert set(approved) == PROPOSAL_KEYS and approved["status"] == "approved"


async def test_reject_api_shape(admin_client, db):
    """거절도 승인과 같은 제안 한 건 모양."""
    for i in range(5):
        await insert_question(db, f"포트폴리오 제출해야 하나요 {i}", category=ETC)
    proposal_id = (await admin_client.post("/admin/category-suggestions/analyze")).json()["proposal"]["id"]

    rejected = (await admin_client.post(f"/admin/category-suggestions/{proposal_id}/reject")).json()
    assert set(rejected) == PROPOSAL_KEYS and rejected["status"] == "rejected"
