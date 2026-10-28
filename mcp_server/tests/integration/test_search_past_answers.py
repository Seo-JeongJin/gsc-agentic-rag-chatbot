"""
과거답변 검색(search_past_answers.py) 통합 테스트 — 리뷰 1번.

검색 대상 조건(2026-10-28 결정): 정상 답변(needs_review=FALSE) + 출력 가드레일 통과(TRUE) +
👎 받지 않음(user_rating이 없거나 1보다 큼) + 임베딩 있음.
"""

import search_past_answers as spa
from tests.integration.conftest import async_fake_embed, fake_vector


async def _insert(db, text: str, *, needs_review=False, review_reason=None, guardrail_passed=True,
                  user_rating=None, embedded=True) -> int:
    """상태를 마음대로 정한 과거 질문 한 행을 넣고 id 반환(세션 행도 같이)."""
    await db.execute("INSERT INTO sessions (session_id) VALUES ('s') ON CONFLICT DO NOTHING")
    return await db.fetchval(
        """
        INSERT INTO questions (session_id, question_text, answer_text, needs_review, review_reason,
                               guardrail_passed, user_rating, embedding)
        VALUES ('s', $1, '답변', $2, $3, $4, $5, $6) RETURNING id
        """,
        text, needs_review, review_reason, guardrail_passed, user_rating,
        fake_vector() if embedded else None,
    )


async def test_only_trustworthy_answered_questions_are_searched(db, monkeypatch):
    """[1번 버그 재현] 에스컬레이션(답변 못함)/가드레일 실패/👎 받은 답변/임베딩 없는 질문은 과거답변으로
    검색되면 안 됨 — 틀린 답이 "과거 답변"이라는 근거로 재활용되는 자기강화 루프를 막기 위함.
    수정 전엔 answer_text가 있기만 하면(= 사실상 전부) 검색 대상이었음."""
    monkeypatch.setattr(spa, "embed", async_fake_embed)
    good = await _insert(db, "기숙사 신청")
    good_rated = await _insert(db, "기숙사 비용", user_rating=5)
    await _insert(db, "답 못한 질문", needs_review=True, review_reason="insufficient_info", guardrail_passed=None)
    await _insert(db, "가드레일 실패", needs_review=True, review_reason="guardrail_failed", guardrail_passed=False)
    await _insert(db, "👎 받은 답변", user_rating=1)
    await _insert(db, "임베딩 없음", embedded=False)

    results = await spa.search_past_answers("기숙사")

    assert {r["id"] for r in results} == {good, good_rated}


async def test_results_have_real_similarity(db, monkeypatch):
    """[1번] 임베딩이 있는 질문만 검색되므로 similarity가 null이 아닌 실제 숫자여야 함
    (수정 전엔 임베딩이 전부 비어 있어서 similarity가 전부 null이었음 — 실측 25건 중 0건)."""
    monkeypatch.setattr(spa, "embed", async_fake_embed)
    await _insert(db, "기숙사 신청")

    [result] = await spa.search_past_answers("기숙사")

    assert result["similarity"] is not None
    assert abs(result["similarity"] - 1.0) < 1e-6  # 같은 벡터 → 코사인 유사도 1
