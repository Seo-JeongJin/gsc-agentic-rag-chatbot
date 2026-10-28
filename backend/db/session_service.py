"""
세션 처리 — main.py의 /api/ask 2번 단계에서 호출.

session_id가 sessions 테이블에 이미 있으면 활동 시각을 갱신하고(필요하면 재방문으로 표시),
없으면 새로 추가. session_id당 정확히 1행만 존재하게 유지(upsert).
"""

from db.connection import get_pool

# "재방문" 기준(2026-10-28 결정): 마지막 활동 후 이 시간이 지나서 다시 질문하면 재방문으로 봄.
# Google Analytics 등 웹 분석의 표준 세션 기준(30분 무활동 = 방문 종료)을 따름.
# 날짜 기준(다른 날 다시 오면 재방문)은 밤 11시 59분 → 0시 1분처럼 2분 차이도 재방문이 되는 문제가 있어서 안 씀.
RETURNING_VISIT_GAP_MINUTES = 30


async def process_session(session_id: str) -> bool:
    """session_id를 upsert하고, 이 세션의 재방문 여부(is_returning)를 반환.

    한 문장(INSERT ... ON CONFLICT DO UPDATE)으로 처리하는 이유(리뷰 10번): 전에는 "SELECT로 있는지 확인 →
    없으면 INSERT"를 따로 실행해서, 새 세션으로 요청 2개가 동시에 오면 둘 다 "없음"으로 보고 INSERT해서
    하나가 중복키 에러(500)로 실패했음. 한 문장으로 하면 DB가 알아서 순서를 맞춰줌.

    재방문 판정: 이미 있는 세션이면, 마지막 활동(last_seen_at)이 30분보다 오래됐을 때만 is_returning을 TRUE로.
    전에는 같은 대화창에서 두 번째 질문만 해도 바로 TRUE가 돼서 "재방문율"이 실제 의미와 달랐음.
    한 번 TRUE가 되면 계속 TRUE(OR로 누적) — "재방문한 적 있는 사람인지"를 나타내는 플래그라서."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # DO UPDATE 안의 sessions.xxx = "이미 저장돼 있던 값"(갱신 전 값), now() = 이번 요청 시각
        return await conn.fetchval(
            """
            INSERT INTO sessions (session_id) VALUES ($1)
            ON CONFLICT (session_id) DO UPDATE SET
                is_returning = sessions.is_returning
                               OR (now() - sessions.last_seen_at > make_interval(mins => $2)),
                last_seen_at = now()
            RETURNING is_returning
            """,
            session_id,
            RETURNING_VISIT_GAP_MINUTES,
        )


async def reset_conversation(session_id: str) -> None:
    """"새 대화 시작" 버튼을 눌렀을 때 호출 — session_id(및 재방문자 기록)는 그대로 두고,
    "대화 리셋 시각"만 갱신. 이후 memory_service.fetch_recent_history가 이 시각 이전 대화는
    더 이상 숏텀메모리로 가져오지 않음."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE sessions SET conversation_reset_at = now() WHERE session_id = $1",
            session_id,
        )
