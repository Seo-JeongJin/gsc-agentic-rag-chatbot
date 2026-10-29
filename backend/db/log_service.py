"""
로그 저장 — main.py의 /api/ask 14번 단계(응답 보내기 전, 동기 호출)에서 사용.

questions 테이블에 이번 질문/답변 한 건을 기록. category_id(카테고리 분류)와
user_rating(사용자 평가)은 이 시점엔 아직 모르므로 NULL로 저장 — category_id는
나중에 category_tagging.py가 비동기로 채우고, user_rating은 사용자가 평가를 남길 때 별도 API로 채움.
(2026-10-29) 출력 검토 결과(guardrail_passed 등)도 마찬가지로 저장 시점엔 모름 → 백그라운드 검토가 update_review_result로 채움.
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


async def update_review_result(
    question_id: int, guardrail_passed: bool | None, needs_review: bool, review_reason: str | None
) -> None:
    """출력 검토 결과를 나중에 채움 (2026-10-29) — 검토가 생각모드로 2~10초 걸려서 응답 뒤 백그라운드로 옮겼기 때문에,
    save_log 시점엔 guardrail_passed=NULL("아직 검토 안 됨")로 저장되고 검토가 끝나면 여기서 갱신됨.
    needs_review/review_reason은 schema.sql의 CHECK 제약(둘이 함께 바뀌어야 함)을 지키도록 항상 같이 씀."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE questions SET guardrail_passed=$2, needs_review=$3, review_reason=$4 WHERE id=$1",
            question_id, guardrail_passed, needs_review, review_reason,
        )


async def save_question_embedding(question_id: int, embedding: list[float]) -> None:
    """질문 임베딩(1024차원)을 questions.embedding에 저장 — 이게 있어야 search_past_answers의 벡터 검색
    (롱텀 메모리)에 이 질문이 걸림. main.py가 정상 답변된 질문에 대해서만 백그라운드로 호출함(2026-10-28, 리뷰 1번).
    (pgvector 타입 변환은 connection.py의 register_vector가 처리해서 파이썬 리스트를 그대로 넣을 수 있음)"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE questions SET embedding = $1 WHERE id = $2", embedding, question_id)


async def set_rating(question_id: int, session_id: str, rating: int) -> bool:
    """사용자가 답변에 평가(👍/👎 등)를 남겼을 때 호출 — questions.user_rating을 갱신.
    위젯에서는 1(안좋음)~5(좋음) 중 썸업=5, 썸다운=1로 매핑해서 보냄.

    WHERE에 session_id까지 거는 이유(소유 확인, 리뷰 9번): 질문 id만으로 UPDATE하면 순차 증가하는
    id를 돌려가며 남의 질문 평점을 조작할 수 있음. 질문한 본인 세션이 보낸 평가만 반영함.

    반환값: 실제로 갱신된 행이 있으면 True, 없으면(없는 질문 or 남의 질문) False.
    수정 전에는 갱신된 행이 0개여도 조용히 성공처럼 넘어갔음."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # asyncpg의 execute()는 "UPDATE 1"처럼 "명령어 + 영향받은 행 수" 문자열을 돌려줌
        result = await conn.execute(
            "UPDATE questions SET user_rating = $1 WHERE id = $2 AND session_id = $3",
            rating,
            question_id,
            session_id,
        )
    return result != "UPDATE 0"
