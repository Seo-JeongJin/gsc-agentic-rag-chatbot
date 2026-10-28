"""
세션 처리 — agent_loop의 2번 단계에서 호출.

session_id가 sessions 테이블에 이미 있으면 재방문(is_returning=TRUE)으로 갱신,
없으면 새로 추가. session_id당 정확히 1행만 존재하게 유지(upsert).
"""

from db.connection import get_pool


async def process_session(session_id: str) -> bool:
    """session_id를 upsert하고, 이번 요청이 재방문인지 여부를 반환."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT session_id FROM sessions WHERE session_id = $1", session_id
        )
        if row is None:
            # 처음 보는 세션 — 새 행 추가
            await conn.execute(
                "INSERT INTO sessions (session_id, is_returning) VALUES ($1, FALSE)",
                session_id,
            )
            return False
        else:
            # 이미 있던 세션 — 재방문으로 갱신, 최근 활동 시각도 갱신
            await conn.execute(
                "UPDATE sessions SET is_returning = TRUE, last_seen_at = now() WHERE session_id = $1",
                session_id,
            )
            return True


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
