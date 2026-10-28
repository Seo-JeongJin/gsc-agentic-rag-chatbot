"""
로그 저장 — agent_loop의 14번 단계(응답 보내기 전, 동기 호출)에서 사용.

questions 테이블에 이번 질문/답변 한 건을 기록. category_id(카테고리 분류)와
user_rating(사용자 평가)은 이 시점엔 아직 모르므로 NULL로 저장 — category_id는
나중에 category_tagging.py가 비동기로 채우고, user_rating은 사용자가 평가를 남길 때 별도 API로 채움.
"""

from db.connection import get_pool


async def save_log(
    session_id: str,
    question_text: str,
    answer_text: str,
    tool_call_count: int,
    guardrail_passed: bool | None,
    needs_review: bool,
    review_reason: str | None,
) -> int:
    """로그 한 건을 저장하고, 생성된 questions.id를 반환
    (이 id를 나중에 category_tagging.py가 UPDATE할 때 사용)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO questions (
                session_id, question_text, answer_text,
                tool_call_count, guardrail_passed, needs_review, review_reason
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            session_id,
            question_text,
            answer_text,
            tool_call_count,
            guardrail_passed,
            needs_review,
            review_reason,
        )
        return row["id"]


async def set_rating(question_id: int, rating: int) -> None:
    """사용자가 답변에 평가(👍/👎 등)를 남겼을 때 호출 — questions.user_rating을 갱신.
    위젯에서는 1(안좋음)~5(좋음) 중 썸업=5, 썸다운=1로 매핑해서 보냄."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE questions SET user_rating = $1 WHERE id = $2",
            rating,
            question_id,
        )
