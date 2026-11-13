"""
숏텀 메모리 조회 — main.py의 /api/ask 4번 단계에서, Agent 루프 시작 전 딱 1회만 호출.

같은 세션의 최근 대화 몇 개를 가져와서, LLM에게 보낼 대화기록 맨 앞에 깔아줄 형태로 반환.
루프가 반복되는 동안에는 재조회하지 않음(새로 생긴 대화가 아니라 "이전 대화"만 다루는 용도).

2026-10-26 추가: 대화 이어가기를 무제한으로 허용하면, 한참 전(예: 지난 전형 시기)에 나눈
대화가 뒤늦게 다시 맥락으로 섞여 들어가 틀린 정보로 이어질 위험이 있음 — 그래서
(1) 24시간 지난 대화는 자동으로 숏텀메모리에서 제외하고,
(2) 사용자가 "새 대화 시작"을 누르면(conversation_reset_at 갱신) 그 시점 이전 대화도 전부 제외.
"""

import logging

from db.connection import get_pool
from syllable_check import odd_syllables

logger = logging.getLogger("uvicorn.error")

DEFAULT_HISTORY_LIMIT = 5         # 최근 몇 개의 질문/답변 턴을 그대로(질문+답) 넘길지
MEMORY_RETENTION_HOURS = 24       # 이 시간이 지난 대화는 더 이상 맥락으로 안 가져옴

# (2026-11-13 B26) 최근 턴보다 앞의 질문은 "앞서 받은 질문 목록" 한 메시지로 넘김 — 길이를 묶어 두려고 개수·글자 수에 상한.
# 20개 × 100자 = 최대 약 2,000자(검색 1번 결과 2,500자와 비슷한 양). 한 대화에서 25개 넘게 묻는 경우는 드묾(B26 세션도 16개).
MAX_LISTED_QUESTIONS = 20
MAX_LISTED_QUESTION_CHARS = 100
# 목록 메시지의 첫 줄 — agent_loop(길이 자르기)·question_router(바로 앞 대화 찾기)가 이 줄로 목록 메시지를 알아봄
QUESTION_LIST_HEADER = "[이 대화에서 앞서 받은 질문 — 오래된 순서, 참고용]"


def _answer_to_keep(row) -> str | None:
    """이 턴의 답을 이전 대화에 넣어도 되면 답을, 빼야 하면 None을 돌려줌(질문은 어느 경우든 남김).

    (2026-10-29 학교 실측) "답을 못 준 답변"(needs_review=TRUE — "확인해드리기 어렵습니다", 루프 상한 폴백)과
    "출력 검토 실패 답변"(guardrail_passed=FALSE)은 뺌. 같은 세션에 "모른다" 답이 쌓이면 실제 Qwen3가 그걸 따라
    다음 질문(입학 정원)까지 검색 없이 "답변드리기 어렵습니다"로 답했음(새 세션에선 3/3 정상).
    검토가 안 된 답변(NULL — 검토 호출 실패, 검토 도입 전 기록)은 남김: 판정이 없을 뿐 나쁜 답이라는 근거가 없음.
    (2026-11-12 B25) 글자가 바뀐 답("졸업"→"졡업")도 뺌 — 남겨 두면 모델이 다음 답부터 그 글자를 계속 따라 씀.
    "목록에 없는 글자" 검사는 DB가 못 해서 SQL이 아니라 여기서 봄."""
    if row["answer_text"] is None or row["needs_review"] or row["guardrail_passed"] is False:
        return None
    if odd := odd_syllables(row["answer_text"]):
        logger.info("숏텀 메모리에서 답을 뺌 — 목록에 없는 글자 %s (B25)", odd)
        return None
    return row["answer_text"]


def _question_list_message(older: list, omitted: int) -> dict:
    """최근 턴보다 앞 질문들을 번호 붙인 목록 한 메시지로 만듦. 번호는 대화 전체에서의 실제 순서(생략된 개수 다음부터).
    생략이 있으면 맨 위에 "(이전 질문 N개 생략)" — 표시가 없으면 모델이 목록 맨 위를 "첫 질문"이라고 믿음(B26 재생 실험의 81번)."""
    lines = [QUESTION_LIST_HEADER]
    if omitted:
        lines.append(f"(이전 질문 {omitted}개 생략)")
    for number, row in enumerate(older, start=omitted + 1):
        text = row["question_text"]
        if len(text) > MAX_LISTED_QUESTION_CHARS:
            text = text[:MAX_LISTED_QUESTION_CHARS] + "…"
        lines.append(f"{number}. {text}")
    return {"role": "user", "content": "\n".join(lines)}


async def fetch_recent_history(session_id: str, limit: int = DEFAULT_HISTORY_LIMIT) -> list[dict]:
    """
    같은 세션의 이전 대화 중 (24시간 이내) AND (마지막 리셋 이후)인 것을 시간순(오래된 것 → 최신 것)으로 반환.
    - 최근 limit개 턴: 질문 + 답. 답을 빼야 하는 턴(`_answer_to_keep`)은 질문만.
    - 그보다 앞 질문: 맨 앞에 "앞서 받은 질문 목록" 한 메시지(최대 MAX_LISTED_QUESTIONS개, 넘치면 생략 개수 표시).

    (2026-11-13 배포 후 버그 B26) 전엔 검토에 걸린 턴을 질문까지 통째로 빼고 최근 5개만 넘겼음. 실제 세션에서
    "JLPT 다들 땄나요?"(검토 실패) 다음의 "총 몇 명이 달성했나요"가 앞 맥락을 잃어 범위 밖 거절, "비율이 궁금한데요"는 전형 비율로 답함,
    "내가 처음 했던 질문은?"엔 남은 것 중 가장 오래된 질문을 댐. 그 세션 재생 실험(질문마다 10번): 지금 방식 0/10 → 이 방식
    75·76·81번 10/10, 79번 8/10, 걸린 답 따라 하기 0. 답은 빼도 질문은 남겨야 대화 흐름이 이어짐.
    반환 형식은 OpenAI 호환 chat 메시지 리스트(role/content) — agent_loop가 그대로 대화기록 맨 앞에 이어붙여 LLM에게 보냄.
    질문만 있는 턴 때문에 user 메시지가 연달아 올 수 있음(agent_loop._trim_history가 질문 단위로 턴을 묶음).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # COUNT(*) OVER () = LIMIT을 걸기 전의 전체 턴 수 — 목록에서 생략된 개수를 알려주려고 같이 받음
        rows = await conn.fetch(
            """
            SELECT q.question_text, q.answer_text, q.needs_review, q.guardrail_passed, COUNT(*) OVER () AS total
            FROM questions q
            JOIN sessions s ON s.session_id = q.session_id
            WHERE q.session_id = $1
              AND q.created_at > now() - interval '1 hour' * $2
              AND q.created_at > COALESCE(s.conversation_reset_at, '-infinity'::timestamptz)
            ORDER BY q.created_at DESC
            LIMIT $3
            """,
            session_id,
            MEMORY_RETENTION_HOURS,
            limit + MAX_LISTED_QUESTIONS,
        )
    if not rows:
        return []

    # DB에서는 최신순으로 가져왔으니, 대화 순서(오래된 것 먼저)로 다시 뒤집음
    omitted = rows[0]["total"] - len(rows)
    rows = list(reversed(rows))
    older, recent = rows[:-limit], rows[-limit:]

    messages: list[dict] = [_question_list_message(older, omitted)] if older else []
    for row in recent:
        messages.append({"role": "user", "content": row["question_text"]})
        if (answer := _answer_to_keep(row)) is not None:
            messages.append({"role": "assistant", "content": answer})
    return messages
