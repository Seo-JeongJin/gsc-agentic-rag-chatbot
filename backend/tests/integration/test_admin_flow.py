"""
관리자 쪽 API 통합 테스트 — 인증(로그인/로그아웃), 질문 로그 조회, "로그 분석하기".

curl로 확인하던 6단계 인증 흐름 + 분석 캐시 동작을 그대로 옮긴 것("현재 동작 고정").
"""

import pytest

from db.admin_service import create_admin
from tests.integration.conftest import insert_question


@pytest.fixture
async def admin_client(client):
    """관리자 계정을 만들고 로그인까지 마친 클라이언트 — 이후 요청에 세션 쿠키가 자동으로 실림."""
    await create_admin("admin", "test-password")
    res = await client.post("/admin/login", json={"username": "admin", "password": "test-password"})
    assert res.status_code == 200
    return client


async def test_admin_endpoints_require_login(client):
    """로그인 안 한 상태로 관리자 API를 부르면 401 — require_admin 의존성이 막아주는지."""
    assert (await client.get("/admin/me")).status_code == 401
    assert (await client.get("/admin/questions")).status_code == 401


async def test_wrong_password_is_rejected(client):
    """비밀번호가 틀리면 401, 쿠키도 발급되지 않아서 이후 요청도 계속 401."""
    await create_admin("admin", "test-password")

    res = await client.post("/admin/login", json={"username": "admin", "password": "wrong"})

    assert res.status_code == 401
    assert (await client.get("/admin/me")).status_code == 401


async def test_login_then_logout(admin_client):
    """로그인 후엔 200(쿠키가 같은 클라이언트로 자동 전달됨), 로그아웃 후엔 같은 쿠키가 있어도
    DB에서 세션이 지워져서 다시 401이어야 함(만료를 기다리지 않고 즉시 무효화)."""
    assert (await admin_client.get("/admin/me")).status_code == 200

    await admin_client.post("/admin/logout")

    assert (await admin_client.get("/admin/me")).status_code == 401


async def test_questions_filter_by_needs_review(admin_client, db):
    """질문 로그의 needs_review 필터: true면 답변 못한 질문만, false면 정상 답변만, 없으면 전부."""
    await insert_question(db, "정상 질문")
    await insert_question(db, "답 못한 질문", needs_review=True, review_reason="insufficient_info")

    all_rows = (await admin_client.get("/admin/questions")).json()
    failed = (await admin_client.get("/admin/questions", params={"needs_review": "true"})).json()
    ok = (await admin_client.get("/admin/questions", params={"needs_review": "false"})).json()

    assert len(all_rows) == 2
    assert [r["question_text"] for r in failed] == ["답 못한 질문"]
    assert [r["question_text"] for r in ok] == ["정상 질문"]


async def test_analyze_skips_when_no_new_review_questions(admin_client, db):
    """"로그 분석하기": 처음엔 분석해서 저장(cached=false), 신규 답변못한질문이 없으면 LLM을 다시
    부르지 않고 기존 결과를 cached=true로 돌려주고, 새 질문이 생기면 그것만 신규로 세서 다시 분석."""
    await insert_question(db, "실패1", needs_review=True, review_reason="insufficient_info")

    first = (await admin_client.post("/admin/analyze")).json()
    assert first["cached"] is False
    assert first["content"]["new_since_last_count"] == 1

    second = (await admin_client.post("/admin/analyze")).json()
    assert second["cached"] is True
    assert second["id"] == first["id"]  # 새 행을 만들지 않고 같은 결과 재사용

    await insert_question(db, "실패2", needs_review=True, review_reason="guardrail_failed")
    third = (await admin_client.post("/admin/analyze")).json()
    assert third["cached"] is False
    assert third["content"]["new_since_last_count"] == 1
    assert third["content"]["based_on_count"] == 2


async def test_analyze_processes_backlog_oldest_first_without_skipping(admin_client, db):
    """[13번 버그 재현] 신규 답변못한질문이 한 번에 분석할 수 있는 양(50건)보다 많으면, 오래된 것부터 50건만
    분석하고 "실제로 본 마지막 번호"까지만 기록해서 다음 클릭 때 나머지를 이어서 분석해야 함.
    수정 전엔 최신 50건만 보고 "마지막 번호"는 전체 최대값으로 저장해서, 나머지 10건이 영원히 분석 안 됐음."""
    for i in range(60):
        await insert_question(db, f"실패 {i}", needs_review=True, review_reason="insufficient_info")

    first = (await admin_client.post("/admin/analyze")).json()
    assert first["content"]["new_since_last_count"] == 50
    assert first["content"]["remaining_count"] == 10

    second = (await admin_client.post("/admin/analyze")).json()
    assert second["cached"] is False                    # 남은 10건이 있으니 건너뛰면 안 됨
    assert second["content"]["new_since_last_count"] == 10
    assert second["content"]["remaining_count"] == 0

    third = (await admin_client.post("/admin/analyze")).json()
    assert third["cached"] is True                      # 이제 정말 다 봤음
