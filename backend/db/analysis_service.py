"""
"로그 분석하기" 버튼 — 답변 못한 질문들을 모아 LLM에게 패턴 요약을 요청하고,
결과를 dashboard_insights에 저장. 평소 대시보드 로딩 시에는 저장된 결과만 읽음(LLM 재호출 없음).

2026-10-27 재설계: "시간이 지났는지"가 아니라 "이전 분석 이후 새 데이터가 생겼는지"로
재분석 여부를 판단하고, 새 데이터가 있으면 "이전 요약과 비교해서 뭐가 바뀌었는지"를
LLM에게 설명하게 함 — 매번 전체를 처음부터 다시 요약하는 대신, 트렌드(계속되는 주제 /
새로 등장한 주제)를 알 수 있게. 자세한 배경은 261021_구현스펙.md 참고.

여기서 쓰는 LLM 호출은 agent_loop.py의 도구호출 루프와 다름 — 도구를 쓸지 판단하는 게 아니라
"프롬프트 하나 주고 요약 텍스트 하나 받는" 단발성 호출이라, agent_loop.py의 함수를
재사용하지 않고 이 파일 안에 따로 가벼운 버전을 둠.
"""

import llm_client
from db.connection import get_pool

INSIGHT_TYPE = "failed_answers_summary"

# 한 번의 분석(LLM 호출 1번)에 넣는 질문 수 상한 — 너무 많이 넣으면 프롬프트가 길어져서
# LLM 컨텍스트(vLLM --max-model-len 8192)를 넘거나 요약 품질이 떨어짐. 넘는 만큼은 다음 클릭 때 이어서 분석.
ANALYSIS_BATCH_SIZE = 50

REVIEW_REASON_LABELS = {
    "insufficient_info": "정보 부족(검색해도 답을 못 찾음)",
    "guardrail_failed": "출력 가드레일 실패(근거 불충분)",
}


async def _get_current_review_state() -> tuple[int, int]:
    """지금 시점의 (답변못한질문 전체 개수, 그 중 최대 id)를 한 번에 조회.
    max_id는 "이번 분석이 어디까지 봤는지"를 다음 분석과 비교할 기준점으로 저장됨."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) AS total, COALESCE(MAX(id), 0) AS max_id
            FROM questions
            WHERE needs_review = TRUE
            """
        )
        return row["total"], row["max_id"]


async def fetch_review_questions(after_id: int = 0, limit: int = ANALYSIS_BATCH_SIZE) -> list[dict]:
    """답변못한 질문 조회. after_id보다 큰(= 그 이후에 생긴) 것만, **오래된 것부터** limit개.
    after_id=0이면 처음부터(최초 분석용).

    오래된 것부터인 이유(2026-10-28 리뷰 13번): 전엔 최신순으로 50개만 가져오면서 "어디까지 봤는지"는
    전체 최대 id로 저장해서, 51개 이상 쌓이면 오래된 쪽이 영원히 분석되지 않았음. 오래된 것부터 가져오고
    "실제로 가져온 것 중 마지막 id"까지만 기록하면, 다음 분석이 정확히 그 다음부터 이어서 봄."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, question_text, review_reason, created_at
            FROM questions
            WHERE needs_review = TRUE AND id > $1
            ORDER BY id ASC
            LIMIT $2
            """,
            after_id,
            limit,
        )
        return [dict(row) for row in rows]


async def _count_review_questions_after(after_id: int) -> int:
    """after_id 이후에 생긴 답변못한질문 개수 — 이번 분석 후 "아직 남은 개수"를 계산하는 데 씀."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM questions WHERE needs_review = TRUE AND id > $1", after_id
        )


def _format_question_list(questions: list[dict]) -> str:
    """질문 목록을 프롬프트에 넣을 "1. (이유: ...) 질문내용" 형태 텍스트로 변환."""
    if not questions:
        return "(없음)"
    lines = []
    for i, q in enumerate(questions, start=1):
        reason_label = REVIEW_REASON_LABELS.get(q["review_reason"], q["review_reason"] or "알수없음")
        lines.append(f"{i}. (이유: {reason_label}) {q['question_text']}")
    return "\n".join(lines)


def _build_full_summary_prompt(questions: list[dict]) -> str:
    """최초 분석(이전 결과가 아예 없을 때)용 — 지금 있는 질문 전체를 요약."""
    return f"""다음은 입학상담 챗봇이 충분히 답변하지 못했던 질문들입니다. 각 질문과 실패 이유를
참고해서, 자주 반복되는 주제나 패턴이 있는지 관리자가 알기 쉽게 요약해 주세요.
이 요약은 "어떤 문서를 보강해야 하는지" 판단하는 데 쓰입니다.

[질문 목록]
{_format_question_list(questions)}

300자 이내로 핵심 패턴만 요약해 주세요. 질문이 하나도 없으면 "최근 답변 못한 질문이 없습니다"라고만 답하세요."""


def _build_trend_prompt(previous_summary: str, new_questions: list[dict]) -> str:
    """이전 분석 결과가 있을 때용 — 이전 요약과 "새로 생긴 질문들"을 비교해서 트렌드를 설명."""
    return f"""아래는 입학상담 챗봇의 "답변 실패 질문" 분석 이력입니다.

[이전 분석 결과]
{previous_summary}

[이전 분석 이후 새로 추가된 답변못한 질문들]
{_format_question_list(new_questions)}

위 내용을 보고, 이전부터 계속되고 있는 주제와 새롭게 등장한 주제를 구분해서 설명해 주세요.
새로 추가된 질문이 없으면 "지난 분석 이후 특별한 변화가 없습니다"라고만 답하세요.
300자 이내로 작성하세요."""


async def generate_summary_llm_call(prompt: str) -> str:
    """요약 LLM 호출 — llm_client.complete(단발성, 도구 없음, 생각모드 끔)로 보냄. LLM_MODE=mock이면 아래 가짜 응답을 씀."""
    return await llm_client.complete(prompt, mock=_mock_summary_response)


async def _mock_summary_response(prompt: str) -> str:
    """⚠️ 가짜 LLM 응답 — LLM_MODE=mock(집 개발용)일 때만 씀. 고정 텍스트라 요약 품질이 아니라
    "조회 → 프롬프트 → 저장 → 캐시 판단" 흐름을 확인하는 용도."""
    return "(가짜 분석 결과) LLM_MODE=mock이라 고정 텍스트입니다. 실제 요약은 LLM_MODE=vllm에서 나옵니다."


async def _save_insight(
    summary: str, based_on_count: int, new_since_last_count: int, last_question_id: int, remaining_count: int
) -> dict:
    """분석 결과를 dashboard_insights에 새 행으로 저장. content 필드 의미:
    - based_on_count: 지금까지 쌓인 답변못한질문 전체 개수
    - new_since_last_count: 이번 분석에서 실제로 LLM에 넣은 질문 개수
    - last_question_id: 이번 분석이 실제로 본 마지막 질문 id(다음 분석은 이 다음부터)
    - remaining_count: 한 번에 다 못 봐서 아직 분석 안 된 개수(0이 아니면 대시보드가 "다시 눌러 이어서 분석" 안내)"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO dashboard_insights (insight_type, content)
            VALUES ($1, $2)
            RETURNING id, insight_type, content, generated_at
            """,
            INSIGHT_TYPE,
            {
                "summary": summary,
                "based_on_count": based_on_count,
                "new_since_last_count": new_since_last_count,
                "last_question_id": last_question_id,
                "remaining_count": remaining_count,
            },
        )
        return {**dict(row), "cached": False}


async def run_analysis() -> dict:
    """
    버튼이 호출하는 메인 함수.

    1) 이전 분석 결과가 있으면 그때의 last_question_id보다 새 질문이 있는지 확인
       - 없으면: 재분석 안 하고 기존 결과를 cached=True로 그대로 반환(LLM 호출 없음)
    2) 새 질문(최초 실행이면 처음부터)을 오래된 것부터 ANALYSIS_BATCH_SIZE개까지 가져와서
       - 최초 실행: 전체요약 프롬프트 / 이후: "이전 요약 + 새 질문" 트렌드 비교 프롬프트
    3) "실제로 본 마지막 id"와 "아직 남은 개수"를 같이 저장 — 남은 게 있으면 다음 클릭 때 이어서 분석
    """
    existing = await get_latest_insight()
    total_count, current_max_id = await _get_current_review_state()
    last_question_id = existing["content"].get("last_question_id", 0) if existing else 0

    if existing is not None and current_max_id <= last_question_id:
        # 지난 분석 이후 새로 생긴(또는 아직 못 본) 답변못한 질문이 없음 — 재분석 없이 기존 결과 재사용
        return {**existing, "cached": True}

    questions = await fetch_review_questions(after_id=last_question_id)
    if existing is None:
        prompt = _build_full_summary_prompt(questions)
    else:
        prompt = _build_trend_prompt(existing["content"]["summary"], questions)
    summary = await generate_summary_llm_call(prompt)

    # 질문이 0개(최초 실행인데 답변못한 질문이 아예 없음)면 기준점은 그대로 유지
    analyzed_until_id = questions[-1]["id"] if questions else last_question_id
    return await _save_insight(
        summary,
        based_on_count=total_count,
        new_since_last_count=len(questions),
        last_question_id=analyzed_until_id,
        remaining_count=await _count_review_questions_after(analyzed_until_id),
    )


async def get_latest_insight() -> dict | None:
    """기본 화면용 — 저장된 분석 결과 중 가장 최근 것 하나만. LLM 호출 없음, 순수 조회."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, insight_type, content, generated_at
            FROM dashboard_insights
            WHERE insight_type = $1
            ORDER BY generated_at DESC
            LIMIT 1
            """,
            INSIGHT_TYPE,
        )
        return dict(row) if row else None


async def get_insight_history(limit: int = 20) -> list[dict]:
    """과거 분석 결과들을 최신순으로 리스트로 — "지난 분석 더보기"용. LLM 호출 없음."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, insight_type, content, generated_at
            FROM dashboard_insights
            WHERE insight_type = $1
            ORDER BY generated_at DESC
            LIMIT $2
            """,
            INSIGHT_TYPE,
            limit,
        )
        return [dict(row) for row in rows]
