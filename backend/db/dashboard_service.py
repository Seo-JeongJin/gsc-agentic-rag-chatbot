"""
관리자 대시보드용 조회 전용 함수들 — 전부 순수 DB 집계/조회이고 LLM 호출이 전혀 없음.
(LLM을 부르는 기능은 이 파일이 아니라 따로 있음: "로그 분석하기"는 analysis_service.py,
"카테고리 제안"은 batch_analysis.py)
"""

from db.connection import get_pool

DEFAULT_QUESTIONS_LIMIT = 50


async def get_questions(
    limit: int = DEFAULT_QUESTIONS_LIMIT,
    offset: int = 0,
    needs_review: bool | None = None,
) -> list[dict]:
    """질문 로그 목록 — 최신순, 페이지네이션(limit/offset). categories를 LEFT JOIN하는 이유는
    아직 분류 안 된 질문(category_id가 NULL)도 빠짐없이 나와야 하기 때문(INNER JOIN이면 빠짐).

    needs_review=True/False를 주면 "답변 못한 질문" 전용 화면처럼 해당 값만 필터링,
    None(기본값)이면 전체 질문(답변 성공/실패 상관없이) 그대로 반환."""
    pool = await get_pool()
    base_select = """
        SELECT
            q.id, q.session_id, q.question_text, q.answer_text,
            q.tool_call_count, q.guardrail_passed, q.needs_review, q.review_reason,
            q.user_rating, q.created_at,
            c.major AS category_major, c.minor AS category_minor
        FROM questions q
        LEFT JOIN categories c ON c.id = q.category_id
    """
    async with pool.acquire() as conn:
        if needs_review is None:
            rows = await conn.fetch(
                base_select + "ORDER BY q.created_at DESC LIMIT $1 OFFSET $2",
                limit,
                offset,
            )
        else:
            rows = await conn.fetch(
                base_select + "WHERE q.needs_review = $3 ORDER BY q.created_at DESC LIMIT $1 OFFSET $2",
                limit,
                offset,
                needs_review,
            )
        return [dict(row) for row in rows]


async def get_category_stats() -> list[dict]:
    """카테고리별 질문 수 집계. categories 기준으로 LEFT JOIN해서, 질문이 0건인 카테고리도
    빠짐없이 나옴(2026-10-27 수정) — 관리자가 "지금 어떤 카테고리가 있는지" 전체를 보고
    카테고리 제안 승인/거절을 판단할 수 있어야 해서, 질문 있는 것만 보여주던 이전 방식(INNER
    JOIN)에서 바꿈. 미분류 개수는 get_overview_stats()의 unclassified_count에서 별도로 다룸."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.major AS category_major, c.minor AS category_minor, COUNT(q.id) AS question_count
            FROM categories c
            LEFT JOIN questions q ON q.category_id = c.id
            GROUP BY c.major, c.minor
            ORDER BY question_count DESC, c.major, c.minor
            """
        )
        return [dict(row) for row in rows]


async def get_overview_stats() -> dict:
    """
    요약 통계 한 번에 — 세션/재방문율, 사용자평가, 미분류 질문, 답변못한질문(이유별).

    참고: unclassified_count(미분류 = category_id가 NULL인 질문 수)는 "카테고리 태깅 기능이
    생기기 전(2026-10-27 이전)에 쌓인 과거 질문 + 분류 LLM 호출이 실패한 질문"을 뜻함.
    분류는 됐는데 맞는 칸이 없는 질문은 NULL이 아니라 기타>기타로 들어가므로 여기 안 잡힘
    (category_tagging.py 상단 설명 참고).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        total_sessions = await conn.fetchval("SELECT COUNT(*) FROM sessions")
        returning_sessions = await conn.fetchval(
            "SELECT COUNT(*) FROM sessions WHERE is_returning = TRUE"
        )
        total_questions = await conn.fetchval("SELECT COUNT(*) FROM questions")
        rated_count = await conn.fetchval(
            "SELECT COUNT(*) FROM questions WHERE user_rating IS NOT NULL"
        )
        avg_rating = await conn.fetchval(
            "SELECT AVG(user_rating) FROM questions WHERE user_rating IS NOT NULL"
        )
        unclassified_count = await conn.fetchval(
            "SELECT COUNT(*) FROM questions WHERE category_id IS NULL"
        )
        needs_review_count = await conn.fetchval(
            "SELECT COUNT(*) FROM questions WHERE needs_review = TRUE"
        )
        needs_review_by_reason_rows = await conn.fetch(
            """
            SELECT review_reason, COUNT(*) AS count
            FROM questions
            WHERE needs_review = TRUE
            GROUP BY review_reason
            """
        )

    returning_rate = (
        round(returning_sessions / total_sessions * 100, 1) if total_sessions > 0 else 0.0
    )

    return {
        "total_sessions": total_sessions,
        "returning_sessions": returning_sessions,
        "returning_rate_percent": returning_rate,
        "total_questions": total_questions,
        "rated_count": rated_count,
        "average_rating": round(float(avg_rating), 2) if avg_rating is not None else None,
        "unclassified_count": unclassified_count,
        "needs_review_count": needs_review_count,
        "needs_review_by_reason": {
            row["review_reason"]: row["count"] for row in needs_review_by_reason_rows
        },
    }
