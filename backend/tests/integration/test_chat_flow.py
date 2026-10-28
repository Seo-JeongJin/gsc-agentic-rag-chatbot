"""
사용자(위젯) 쪽 API 통합 테스트 — /api/ask, /api/rate, /api/reset_conversation.

지금까지 curl로 손으로 확인하던 흐름을 그대로 옮긴 것("현재 동작 고정" + 1단계 9번 수정 확인).
"""

from db.memory_service import fetch_recent_history


async def _ask(client, text: str, session_id: str = "s1") -> dict:
    """/api/ask 호출 후 JSON 응답을 반환하는 도우미 — 매번 같은 형식으로 부르기 위함."""
    res = await client.post("/api/ask", json={"question_text": text, "session_id": session_id})
    assert res.status_code == 200
    return res.json()


async def test_ask_saves_log_and_tags_category(client, db):
    """질문 하나가 끝에서 끝까지 처리되는지: 응답에 question_id가 오고, questions 테이블에
    세션/도구호출횟수/가드레일 결과가 저장되고, 백그라운드 카테고리 태깅(가짜 LLM이 "기숙사"
    키워드로 분류)까지 반영되는지 확인. 백그라운드 작업은 테스트 클라이언트가 응답을 돌려주기 전에
    끝까지 실행되므로 바로 DB를 조회해도 결과가 있음."""
    data = await _ask(client, "기숙사 신청 방법 알려주세요")

    assert data["question_id"] is not None
    row = await db.fetchrow(
        """
        SELECT q.session_id, q.tool_call_count, q.guardrail_passed, q.needs_review,
               c.major, c.minor
        FROM questions q LEFT JOIN categories c ON c.id = q.category_id
        WHERE q.id = $1
        """,
        data["question_id"],
    )
    assert row["session_id"] == "s1"
    assert row["tool_call_count"] == 1
    assert row["guardrail_passed"] is not None  # 출력 가드레일이 실제로 실행됐다는 뜻(통과/실패 무관)
    assert (row["major"], row["minor"]) == ("캠퍼스생활·비용", "기숙사")


async def test_guardrail_blocked_question_is_not_logged(client, db):
    """입력 가드레일에 막힌 질문(PII)은 LLM도 안 부르고 로그도 안 남음 — question_id가 null이고
    questions 테이블에 행이 생기지 않아야 함."""
    data = await _ask(client, "제 번호 010-1234-5678 이에요")

    assert data["question_id"] is None
    assert "개인정보" in data["answer_text"]
    assert await db.fetchval("SELECT COUNT(*) FROM questions") == 0


async def test_rate_own_question_is_saved(client, db):
    """본인 세션으로 평가하면 200 + DB에 평점 저장(위젯의 👍 = 5점)."""
    qid = (await _ask(client, "전형료는 얼마인가요?"))["question_id"]

    res = await client.post("/api/rate", json={"question_id": qid, "rating": 5, "session_id": "s1"})

    assert res.status_code == 200
    assert await db.fetchval("SELECT user_rating FROM questions WHERE id=$1", qid) == 5


async def test_rate_other_sessions_question_is_rejected(client, db):
    """[1단계 9번 수정 확인] 다른 세션이 남의 질문 번호로 평가하면 404, 평점도 안 바뀌어야 함
    — 순차 증가하는 question_id만으로 평점을 조작하는 걸 막는 소유 확인."""
    qid = (await _ask(client, "전형료는 얼마인가요?"))["question_id"]

    res = await client.post("/api/rate", json={"question_id": qid, "rating": 1, "session_id": "someone-else"})

    assert res.status_code == 404
    assert await db.fetchval("SELECT user_rating FROM questions WHERE id=$1", qid) is None


async def test_rate_unknown_question_returns_404(client, db):
    """[1단계 9번 수정 확인] 존재하지 않는 질문 번호면 성공(ok)인 척하지 않고 404."""
    res = await client.post("/api/rate", json={"question_id": 999999, "rating": 5, "session_id": "s1"})
    assert res.status_code == 404


async def test_reset_conversation_clears_short_term_memory(client, db):
    """"새 대화 시작"을 누르면 그 이전 대화는 숏텀메모리(다음 질문 때 LLM에 같이 보내는 과거 대화)에서
    빠져야 함. 리셋 전엔 질문/답변 1쌍(메시지 2개)이 조회되고, 리셋 후엔 0개."""
    await _ask(client, "기숙사 있나요?")
    assert len(await fetch_recent_history("s1")) == 2

    res = await client.post("/api/reset_conversation", json={"session_id": "s1"})

    assert res.status_code == 200
    assert await fetch_recent_history("s1") == []
