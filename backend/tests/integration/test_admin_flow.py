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


async def test_analyze_returns_502_when_llm_fails(admin_client, db, monkeypatch):
    """LLM 서버가 실패하면 "로그 분석하기"는 500이 아니라 502(LLM 서버 문제)로 응답하고, 아무것도 저장하지 않아야 함
    — 그래야 다음에 다시 눌렀을 때 같은 질문들로 정상 재시도됨(리뷰 2번, 4단계)."""
    import db.analysis_service as analysis_service
    import llm_client

    async def failing_llm(prompt):
        raise llm_client.LLMError("GPU 서버 응답 없음")

    monkeypatch.setattr(analysis_service, "generate_summary_llm_call", failing_llm)
    await insert_question(db, "실패1", needs_review=True, review_reason="insufficient_info")

    res = await admin_client.post("/admin/analyze")

    assert res.status_code == 502
    assert await db.fetchval("SELECT COUNT(*) FROM dashboard_insights") == 0


# ----- 2026-11-11 배포 0-6: 관리자 로그인 보안 (L5) -----


async def test_login_locked_after_five_failures_from_same_ip(client):
    """[L5 재현] 전엔 로그인을 무제한으로 시도할 수 있어서 비밀번호를 프로그램으로 계속 대입할 수 있었음.
    이제 같은 IP에서 15분 안에 5번 틀리면 6번째부터는 맞는 비밀번호여도 429(잠시 후 다시). 다른 IP는 영향 없음.
    IP는 nginx가 넣어주는 X-Real-IP로 구분(8080은 127.0.0.1에만 열려 nginx를 거쳐야만 들어오므로 꾸며낼 수 없음 — 배포 0-3)."""
    await create_admin("admin", "test-password")
    attacker = {"X-Real-IP": "203.0.113.7"}

    for _ in range(5):
        res = await client.post("/admin/login", json={"username": "admin", "password": "wrong"}, headers=attacker)
        assert res.status_code == 401
    locked = await client.post("/admin/login", json={"username": "admin", "password": "test-password"}, headers=attacker)
    other_ip = await client.post("/admin/login", json={"username": "admin", "password": "test-password"},
                                 headers={"X-Real-IP": "198.51.100.9"})

    assert locked.status_code == 429
    assert other_ip.status_code == 200


async def test_successful_login_clears_failure_count(client):
    """맞게 로그인하면 그 IP의 실패 기록을 지움 — 관리자가 오타 몇 번 낸 뒤 로그인했는데, 다음에 한두 번 틀려서 바로 잠기지 않게."""
    await create_admin("admin", "test-password")
    me = {"X-Real-IP": "203.0.113.8"}

    for _ in range(4):
        await client.post("/admin/login", json={"username": "admin", "password": "wrong"}, headers=me)
    assert (await client.post("/admin/login", json={"username": "admin", "password": "test-password"}, headers=me)).status_code == 200
    for _ in range(4):
        res = await client.post("/admin/login", json={"username": "admin", "password": "wrong"}, headers=me)

    assert res.status_code == 401   # 실패가 다시 0부터 세어져서 아직 안 잠김


async def test_login_cookie_is_secure_only_over_https(client):
    """[L5] 로그인 쿠키에 secure가 없어서 http로도 쿠키가 오갈 수 있었음. 이제 https로 들어온 요청(nginx가 X-Forwarded-Proto: https)이면 secure를 켬.
    http(학과 PC 개발·실측)에선 끔 — 켜면 브라우저가 http에서 쿠키를 안 보내서 로그인이 안 됨."""
    await create_admin("admin", "test-password")

    over_https = await client.post("/admin/login", json={"username": "admin", "password": "test-password"},
                                   headers={"X-Forwarded-Proto": "https"})
    over_http = await client.post("/admin/login", json={"username": "admin", "password": "test-password"})

    assert "secure" in over_https.headers["set-cookie"].lower()
    assert "secure" not in over_http.headers["set-cookie"].lower()


async def test_expired_sessions_are_removed_on_login(client, db):
    """[L5] 만료된 로그인 기록(admin_sessions)이 지워지지 않고 계속 쌓였음 — 로그인할 때마다 만료된 것을 같이 정리."""
    await create_admin("admin", "test-password")
    admin_id = await db.fetchval("SELECT id FROM admins WHERE username='admin'")
    await db.execute(
        "INSERT INTO admin_sessions (session_token, admin_id, expires_at) VALUES ('old-token', $1, now() - interval '1 day')", admin_id)

    await client.post("/admin/login", json={"username": "admin", "password": "test-password"})

    assert await db.fetchval("SELECT count(*) FROM admin_sessions WHERE session_token='old-token'") == 0
    assert await db.fetchval("SELECT count(*) FROM admin_sessions") == 1   # 방금 로그인한 세션만 남음
