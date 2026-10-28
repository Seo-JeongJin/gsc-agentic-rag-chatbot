"""
숏텀 메모리 조회 — main.py의 /api/ask 4번 단계에서, Agent 루프 시작 전 딱 1회만 호출.

같은 세션의 최근 대화 몇 개를 가져와서, LLM에게 보낼 대화기록 맨 앞에 깔아줄 형태로 반환.
루프가 반복되는 동안에는 재조회하지 않음(새로 생긴 대화가 아니라 "이전 대화"만 다루는 용도).

2026-10-26 추가: 대화 이어가기를 무제한으로 허용하면, 한참 전(예: 지난 전형 시기)에 나눈
대화가 뒤늦게 다시 맥락으로 섞여 들어가 틀린 정보로 이어질 위험이 있음 — 그래서
(1) 24시간 지난 대화는 자동으로 숏텀메모리에서 제외하고,
(2) 사용자가 "새 대화 시작"을 누르면(conversation_reset_at 갱신) 그 시점 이전 대화도 전부 제외.
"""

from db.connection import get_pool

DEFAULT_HISTORY_LIMIT = 5         # 최근 몇 개의 질문/답변 쌍까지 가져올지
MEMORY_RETENTION_HOURS = 24       # 이 시간이 지난 대화는 더 이상 맥락으로 안 가져옴


async def fetch_recent_history(session_id: str, limit: int = DEFAULT_HISTORY_LIMIT) -> list[dict]:
    """
    같은 세션의 최근 질문/답변을, (24시간 이내) AND (마지막 리셋 이후)인 것만
    시간순(오래된 것 → 최신 것)으로 정렬해 반환.
    반환 형식은 OpenAI 호환 chat 메시지 리스트(role/content) — agent_loop가 그대로
    대화기록 맨 앞에 이어붙여 LLM에게 보낼 수 있게.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT q.question_text, q.answer_text
            FROM questions q
            JOIN sessions s ON s.session_id = q.session_id
            WHERE q.session_id = $1
              AND q.answer_text IS NOT NULL
              AND q.created_at > now() - interval '1 hour' * $2
              AND q.created_at > COALESCE(s.conversation_reset_at, '-infinity'::timestamptz)
            ORDER BY q.created_at DESC
            LIMIT $3
            """,
            session_id,
            MEMORY_RETENTION_HOURS,
            limit,
        )

    # DB에서는 최신순으로 가져왔으니, 대화 순서(오래된 것 먼저)로 다시 뒤집음
    rows = list(reversed(rows))

    messages: list[dict] = []
    for row in rows:
        messages.append({"role": "user", "content": row["question_text"]})
        messages.append({"role": "assistant", "content": row["answer_text"]})

    return messages
