"""
카테고리 제안(T6) 통합 테스트 — "카테고리 제안 확인하기" → 승인/거절.

curl로 확인하던 5단계 흐름(임계치 미달 → 제안 → 중복 방지 → 승인 → 재승인 차단)을 옮긴 것("현재 동작 고정").
"""

import asyncio

import pytest

import db.category_tagging as category_tagging
from tests.integration.conftest import insert_question

ETC = ("기타", "기타")


@pytest.fixture
async def admin_client(client):
    """로그인된 관리자 클라이언트(카테고리 제안 API는 전부 관리자 전용)."""
    from db.admin_service import create_admin

    await create_admin("admin", "test-password")
    await client.post("/admin/login", json={"username": "admin", "password": "test-password"})
    return client


async def _add_portfolio_etc_questions(db, count: int) -> None:
    """가짜 클러스터 LLM이 "패턴 있음"으로 판단하는 포트폴리오 질문을 기타>기타로 count개 넣음."""
    for i in range(count):
        await insert_question(db, f"포트폴리오 제출해야 하나요 {i}", category=ETC)


async def test_below_threshold_does_not_call_llm(admin_client, db):
    """기타 질문이 5건 미만이면 LLM 호출 없이 insufficient_data — 1~2건으로 새 카테고리를 제안하는
    이상한 상황을 코드가 먼저 막는 지점."""
    await _add_portfolio_etc_questions(db, 4)

    result = (await admin_client.post("/admin/category-suggestions/analyze")).json()

    assert result == {"status": "insufficient_data", "count": 4, "required": 5}


async def test_proposal_is_created_once_and_not_duplicated(admin_client, db):
    """5건 이상이면 제안이 pending으로 생기고, 다시 눌러도 같은 이름의 제안이 또 생기지 않음."""
    await _add_portfolio_etc_questions(db, 5)

    first = (await admin_client.post("/admin/category-suggestions/analyze")).json()
    second = (await admin_client.post("/admin/category-suggestions/analyze")).json()

    assert first["status"] == "proposed"
    assert first["proposal"]["proposed_name"] == "학과소개 > 포트폴리오 제출"
    assert second["status"] == "already_proposed"
    assert len((await admin_client.get("/admin/category-suggestions")).json()) == 1


async def test_approve_adds_category_and_cannot_be_approved_twice(admin_client, db):
    """승인하는 순간에만 categories에 실제 행이 추가되고, pending 목록에서 빠지며,
    이미 처리된 제안을 다시 승인하면 400."""
    await _add_portfolio_etc_questions(db, 5)
    proposal_id = (await admin_client.post("/admin/category-suggestions/analyze")).json()["proposal"]["id"]

    res = await admin_client.post(f"/admin/category-suggestions/{proposal_id}/approve")

    assert res.status_code == 200
    assert await db.fetchval(
        "SELECT COUNT(*) FROM categories WHERE major='학과소개' AND minor='포트폴리오 제출'"
    ) == 1
    assert (await admin_client.get("/admin/category-suggestions")).json() == []
    assert (await admin_client.post(f"/admin/category-suggestions/{proposal_id}/approve")).status_code == 400


async def test_after_approval_same_name_is_reported_as_existing_category(admin_client, db):
    """승인된 뒤 다시 눌러도 같은 제안이 또 생기지 않고 already_category로 응답(2026-10-27 버그 수정분)."""
    await _add_portfolio_etc_questions(db, 5)
    proposal_id = (await admin_client.post("/admin/category-suggestions/analyze")).json()["proposal"]["id"]
    await admin_client.post(f"/admin/category-suggestions/{proposal_id}/approve")

    result = (await admin_client.post("/admin/category-suggestions/analyze")).json()

    assert result["status"] == "already_category"


# ----- 2단계에서 고친 버그들 (리뷰 5, 11, 12번 + 승인 시 재분류) -----


async def test_rejected_proposal_is_not_proposed_again(admin_client, db):
    """[5번 버그 재현] 관리자가 거절한 제안은 다시 누르면 또 생기면 안 됨(2026-10-28 결정: 거절한 이름은
    영구히 다시 제안하지 않음). 수정 전엔 pending만 중복 체크해서, 거절 직후 같은 제안이 다시 pending으로 생겼음."""
    await _add_portfolio_etc_questions(db, 5)
    proposal_id = (await admin_client.post("/admin/category-suggestions/analyze")).json()["proposal"]["id"]
    await admin_client.post(f"/admin/category-suggestions/{proposal_id}/reject")

    result = (await admin_client.post("/admin/category-suggestions/analyze")).json()

    assert result["status"] == "already_rejected"
    assert (await admin_client.get("/admin/category-suggestions")).json() == []


async def test_proposal_counts_and_samples_only_related_questions(admin_client, db):
    """[11번 버그 재현] 제안의 "관련 질문 수"와 "샘플 질문"은 LLM이 그 주제에 해당한다고 고른 질문만으로
    만들어야 함. 수정 전엔 관련 질문 수 = 샘플 개수(항상 5 이하), 샘플 = 기타 질문 최근 5개(주제와 무관한
    "날씨" 같은 질문이 섞임)였음. 포트폴리오 6개 + 무관한 질문 3개(나중에 넣어서 최근 순으로는 맨 앞)로 확인."""
    await _add_portfolio_etc_questions(db, 6)
    for text in ["오늘 날씨 어때요", "점심 뭐 먹지", "ㅁㄴㅇㄹ"]:
        await insert_question(db, text, category=ETC)

    proposal = (await admin_client.post("/admin/category-suggestions/analyze")).json()["proposal"]

    assert proposal["related_question_count"] == 6
    samples = proposal["sample_questions"]["questions"]
    assert samples and all("포트폴리오" in q for q in samples)


async def test_concurrent_approvals_apply_only_once(admin_client, db):
    """[12번 버그 재현] 같은 제안에 승인 요청이 동시에 2번 오면(더블클릭 등) 한 번만 처리돼야 함.
    수정 전엔 "pending인지 확인 → 카테고리 추가 → approved로 변경"이 트랜잭션 없이 따로 실행돼서,
    둘 다 "아직 pending"으로 보고 둘 다 성공(200)할 수 있었음."""
    await _add_portfolio_etc_questions(db, 5)
    proposal_id = (await admin_client.post("/admin/category-suggestions/analyze")).json()["proposal"]["id"]

    responses = await asyncio.gather(
        admin_client.post(f"/admin/category-suggestions/{proposal_id}/approve"),
        admin_client.post(f"/admin/category-suggestions/{proposal_id}/approve"),
    )

    assert sorted(r.status_code for r in responses) == [200, 400]


async def test_approval_reclassifies_etc_questions(admin_client, db, monkeypatch):
    """[승인 시 재분류, 2026-10-28 결정] 승인하면 기타>기타에 있던 질문들을 새 카테고리 목록으로 다시 분류해야 함
    — 안 하면 승인한 카테고리의 질문 수가 0으로 보이고, 원래 질문들이 계속 기타에 남음.
    가짜 분류 LLM은 "포트폴리오"를 모르기 때문에, 이 테스트에서만 새 카테고리를 고르는 가짜로 바꿔 끼움."""

    async def fake_category_llm(prompt):
        question = category_tagging._extract_question_section(prompt)
        return "학과소개 > 포트폴리오 제출" if "포트폴리오" in question else "기타 > 기타"

    monkeypatch.setattr(category_tagging, "generate_category_llm_call", fake_category_llm)
    await _add_portfolio_etc_questions(db, 5)
    await insert_question(db, "오늘 날씨 어때요", category=ETC)
    proposal_id = (await admin_client.post("/admin/category-suggestions/analyze")).json()["proposal"]["id"]

    await admin_client.post(f"/admin/category-suggestions/{proposal_id}/approve")

    rows = await db.fetch(
        "SELECT q.question_text, c.major, c.minor FROM questions q JOIN categories c ON c.id = q.category_id"
    )
    by_text = {r["question_text"]: (r["major"], r["minor"]) for r in rows}
    assert by_text["오늘 날씨 어때요"] == ETC
    assert all(cat == ("학과소개", "포트폴리오 제출") for text, cat in by_text.items() if "포트폴리오" in text)


async def test_same_question_repeated_is_not_a_topic(admin_client, db):
    """[2026-10-29 실측 B16 재현] 같은 문장("전형료 5만원 맞죠?") 2개만 근거로 "반복되는 주제"라며 새 카테고리를 제안했음.
    같은 문장은 하나로 세고, 서로 다른 질문이 MIN_DISTINCT_RELATED(3)개 미만이면 제안하지 않음(no_pattern) — 코드가 막는 규칙.
    (가짜 LLM은 "포트폴리오"가 들어간 질문을 전부 관련 질문으로 고름)"""
    for _ in range(5):
        await insert_question(db, "포트폴리오 제출해야 하나요", category=ETC)
    for text in ["오늘 날씨 어때요", "안녕하세요"]:
        await insert_question(db, text, category=ETC)

    result = (await admin_client.post("/admin/category-suggestions/analyze")).json()

    assert result["status"] == "no_pattern"
    assert (await admin_client.get("/admin/category-suggestions")).json() == []


async def test_samples_do_not_repeat_the_same_question(admin_client, db):
    """서로 다른 질문이 3개 이상이면 제안은 되지만, 관리자에게 보여줄 샘플에는 같은 문장이 한 번만 나와야 함."""
    for text in ["포트폴리오 제출해야 하나요", "포트폴리오 제출해야 하나요", "포트폴리오 양식 있나요", "포트폴리오 분량은요"]:
        await insert_question(db, text, category=ETC)
    await insert_question(db, "오늘 날씨 어때요", category=ETC)

    proposal = (await admin_client.post("/admin/category-suggestions/analyze")).json()["proposal"]

    samples = proposal["sample_questions"]["questions"]
    assert sorted(samples) == sorted(set(samples)) and len(samples) == 3
