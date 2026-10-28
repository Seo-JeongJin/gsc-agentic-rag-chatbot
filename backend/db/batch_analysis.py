"""
카테고리 추천(T6) — "기타"로 쌓인 질문들을 보고 새 카테고리가 필요한지 LLM에게 판단시켜서
category_proposals에 "제안"만 쌓아두는 기능. 실제 categories에 반영되는 건 관리자가
대시보드에서 승인했을 때뿐 — LLM이 카테고리 체계를 직접 바꾸지 못하게 막는 구조
("판단은 LLM, 실행은 코드+관리자승인" 원칙, category_tagging.py와 같은 맥락).

관리자가 "카테고리 제안 확인하기"를 누를 때마다 호출되지만, 아래 두 겹의 안전장치로
"의미없는 제안이 매번 쏟아지는" 상황을 막음:
1. 임계치(코드): 기타로 분류된 질문이 MIN_FOR_SUGGESTION건 미달이면 LLM 호출 자체를 안 함.
2. 노이즈 필터링(LLM): 쓰레기/무관 질문뿐이면 LLM이 "제안 없음"으로 응답하도록 프롬프트에 명시.
추가로 같은 이름의 제안이 이미 대기 중이면 중복 저장하지 않음.
"""

from db.connection import get_pool

MIN_FOR_SUGGESTION = 5


async def _get_unclassified_category_id() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT id FROM categories WHERE major='기타' AND minor='기타'")


async def fetch_unclassified_questions(category_id: int, limit: int = 50) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, question_text FROM questions WHERE category_id = $1 ORDER BY created_at DESC LIMIT $2",
            category_id,
            limit,
        )
        return [dict(row) for row in rows]


def _build_cluster_prompt(questions: list[dict]) -> str:
    lines = "\n".join(f"{i}. {q['question_text']}" for i, q in enumerate(questions, start=1))
    return f"""다음은 챗봇이 "기타"로 분류한(기존 카테고리 어디에도 맞지 않는) 질문들입니다.
이 중에서 실제로 입학상담과 관련이 있고 반복되는 진짜 주제가 있는지 찾아주세요. 의미없는
문자열, 테스트성 입력, 욕설, 완전히 무관한 질문(날씨 등)은 패턴을 찾는 근거로 쓰지 마세요.

[질문 목록]
{lines}

진짜 반복되는 주제가 있으면 다음 형식으로만 답하세요:
제안: 있음
카테고리: <대분류> > <소분류>
이유: <한 줄 설명>

반복되는 진짜 주제가 없으면 다음 한 줄만 답하세요:
제안: 없음
"""


async def generate_cluster_llm_call(prompt: str) -> str:
    """
    ⚠️ 가짜 LLM 호출 — agent_loop.py와 같은 이유로 가짜(GPU서버가 교내망 전용이라
    집에서 연결 불가). 실제로는 위 프롬프트를 vLLM에 단발성으로 보내면 됨.

    지금은 질문 목록에 "포트폴리오" 키워드가 과반 이상 나오면 패턴이 있는 척, 아니면
    (쓰레기/제각각이면) "패턴 없음"으로 응답 — 실제 분류 판단이 아니라 "LLM 응답 파싱 →
    중복확인 → category_proposals 저장"으로 이어지는 코드 쪽 로직을 테스트하기 위한 용도.
    """
    _, _, after = prompt.partition("[질문 목록]\n")
    question_block, _, _ = after.partition("\n\n진짜 반복되는")
    lines = [line for line in question_block.splitlines() if line.strip()]
    if lines and sum("포트폴리오" in line for line in lines) >= len(lines) / 2:
        return "제안: 있음\n카테고리: 학과소개 > 포트폴리오 제출\n이유: 포트폴리오 제출 방법 관련 질문이 반복됨"
    return "제안: 없음"


def _parse_cluster_response(raw: str) -> dict | None:
    lines = [line.strip() for line in raw.strip().splitlines() if line.strip()]
    if not lines or lines[0] != "제안: 있음":
        return None

    major, minor, reason = None, None, ""
    for line in lines[1:]:
        if line.startswith("카테고리:") and ">" in line:
            value = line.split(":", 1)[1].strip()
            major, _, minor = value.partition(">")
            major, minor = major.strip(), minor.strip()
        elif line.startswith("이유:"):
            reason = line.split(":", 1)[1].strip()

    if not major or not minor:
        return None
    return {"major": major, "minor": minor, "reason": reason}


async def _save_proposal(major: str, minor: str, reason: str, sample_questions: list[str]) -> dict:
    """
    저장 전에 두 가지를 확인 — 둘 중 하나라도 걸리면 저장 안 함:
    1. 이미 승인되어 categories에 실제로 존재하는 대/소분류인지. (2026-10-27 버그 수정:
       승인해도 그 패턴을 만든 원래 질문들이 자동으로 재분류되진 않아서, 안 걸러주면
       버튼을 다시 누를 때마다 "이미 승인된 걸 또 제안"하는 무한반복이 생김 — 실측 중 발견.)
    2. 같은 이름의 제안이 이미 pending 상태로 대기 중인지.

    반환: {"saved": True, "proposal": {...}} 또는 {"saved": False, "reason": "already_category"|"already_pending"}
    """
    proposed_name = f"{major} > {minor}"
    pool = await get_pool()
    async with pool.acquire() as conn:
        already_category = await conn.fetchval(
            "SELECT 1 FROM categories WHERE major = $1 AND minor = $2", major, minor
        )
        if already_category:
            return {"saved": False, "reason": "already_category"}

        already_pending = await conn.fetchval(
            "SELECT 1 FROM category_proposals WHERE proposed_name = $1 AND status = 'pending'",
            proposed_name,
        )
        if already_pending:
            return {"saved": False, "reason": "already_pending"}

        row = await conn.fetchrow(
            """
            INSERT INTO category_proposals (proposed_name, related_question_count, sample_questions)
            VALUES ($1, $2, $3)
            RETURNING id, proposed_name, related_question_count, sample_questions, status, created_at
            """,
            proposed_name,
            len(sample_questions),
            {"reason": reason, "questions": sample_questions},
        )
        return {"saved": True, "proposal": dict(row)}


async def run_category_suggestion() -> dict:
    """관리자가 "카테고리 제안 확인하기"를 누르면 호출되는 메인 함수."""
    category_id = await _get_unclassified_category_id()

    pool = await get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM questions WHERE category_id = $1", category_id)

    if total < MIN_FOR_SUGGESTION:
        return {"status": "insufficient_data", "count": total, "required": MIN_FOR_SUGGESTION}

    questions = await fetch_unclassified_questions(category_id)
    prompt = _build_cluster_prompt(questions)
    raw_response = await generate_cluster_llm_call(prompt)
    parsed = _parse_cluster_response(raw_response)

    if parsed is None:
        return {"status": "no_pattern", "checked_count": total}

    sample_texts = [q["question_text"] for q in questions[:5]]
    result = await _save_proposal(parsed["major"], parsed["minor"], parsed["reason"], sample_texts)
    if not result["saved"]:
        status = "already_category" if result["reason"] == "already_category" else "already_proposed"
        return {"status": status, "proposed_name": f"{parsed['major']} > {parsed['minor']}"}
    return {"status": "proposed", "proposal": result["proposal"]}


async def get_pending_proposals() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, proposed_name, related_question_count, sample_questions, status, created_at
            FROM category_proposals
            WHERE status = 'pending'
            ORDER BY created_at DESC
            """
        )
        return [dict(row) for row in rows]


async def approve_proposal(proposal_id: int) -> dict:
    """제안을 승인 — 이 순간에만 실제 categories 테이블에 반영됨(LLM은 제안만, 실행은 여기서)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        proposal = await conn.fetchrow(
            "SELECT id, proposed_name, status FROM category_proposals WHERE id = $1", proposal_id
        )
        if proposal is None:
            raise ValueError("존재하지 않는 제안입니다.")
        if proposal["status"] != "pending":
            raise ValueError("이미 처리된 제안입니다.")

        major, _, minor = proposal["proposed_name"].partition(">")
        await conn.execute(
            "INSERT INTO categories (major, minor) VALUES ($1, $2) ON CONFLICT (major, minor) DO NOTHING",
            major.strip(),
            minor.strip(),
        )
        row = await conn.fetchrow(
            "UPDATE category_proposals SET status = 'approved' WHERE id = $1 "
            "RETURNING id, proposed_name, related_question_count, sample_questions, status, created_at",
            proposal_id,
        )
        return dict(row)


async def reject_proposal(proposal_id: int) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE category_proposals SET status = 'rejected' WHERE id = $1 AND status = 'pending' "
            "RETURNING id, proposed_name, related_question_count, sample_questions, status, created_at",
            proposal_id,
        )
        if row is None:
            raise ValueError("존재하지 않거나 이미 처리된 제안입니다.")
        return dict(row)
