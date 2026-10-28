"""
카테고리 추천(T6) — "기타"로 쌓인 질문들을 보고 새 카테고리가 필요한지 LLM에게 판단시켜서
category_proposals에 "제안"만 쌓아두는 기능. 실제 categories에 반영되는 건 관리자가
대시보드에서 승인했을 때뿐 — LLM이 카테고리 체계를 직접 바꾸지 못하게 막는 구조
("판단은 LLM, 실행은 코드+관리자승인" 원칙, category_tagging.py와 같은 맥락).

관리자가 "카테고리 제안 확인하기"를 누를 때마다 호출되지만, 아래 두 겹의 안전장치로
"의미없는 제안이 매번 쏟아지는" 상황을 막음:
1. 임계치(코드): 기타로 분류된 질문이 MIN_FOR_SUGGESTION건 미달이면 LLM 호출 자체를 안 함.
2. 노이즈 필터링(LLM): 쓰레기/무관 질문뿐이면 LLM이 "제안 없음"으로 응답하도록 프롬프트에 명시.
추가로 같은 이름이 이미 카테고리로 있거나, 대기 중이거나, 관리자가 거절한 적 있으면 저장하지 않음.

승인하면 기타>기타 질문들을 새 카테고리 목록으로 다시 분류함(category_tagging.retag_etc_questions,
admin_api.py가 백그라운드로 호출) — 원래 질문들이 계속 기타에 남아서 같은 제안이 반복되는 원인을 없앰.
"""

import re

import llm_client
from db.connection import get_pool

MIN_FOR_SUGGESTION = 5
SAMPLE_QUESTION_COUNT = 5  # 관리자 화면에 보여줄 샘플 질문 개수(관련 질문 중 앞에서부터)
# 프롬프트에 넣는 질문 한 건의 최대 글자 수(2026-10-28 2차 리뷰 5번) — 50건 × 최대 1,000자를 그대로 넣으면
# vLLM 길이 제한을 넘어 매번 502. 주제를 묶는 판단엔 질문 앞부분이면 충분함. (샘플 질문 저장은 원문 그대로)
MAX_QUESTION_CHARS_IN_PROMPT = 150


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
    def shorten(text: str) -> str:
        return text if len(text) <= MAX_QUESTION_CHARS_IN_PROMPT else text[:MAX_QUESTION_CHARS_IN_PROMPT] + "…"

    lines = "\n".join(f"{i}. {shorten(q['question_text'])}" for i, q in enumerate(questions, start=1))
    return f"""다음은 챗봇이 "기타"로 분류한(기존 카테고리 어디에도 맞지 않는) 질문들입니다.
이 중에서 실제로 입학상담과 관련이 있고 반복되는 진짜 주제가 있는지 찾아주세요. 의미없는
문자열, 테스트성 입력, 욕설, 완전히 무관한 질문(날씨 등)은 패턴을 찾는 근거로 쓰지 마세요.

[질문 목록]
{lines}

진짜 반복되는 주제가 있으면 다음 형식으로만 답하세요:
제안: 있음
카테고리: <대분류> > <소분류>
관련 질문: <위 목록에서 이 주제에 해당하는 질문 번호를 쉼표로 전부>
이유: <한 줄 설명>

반복되는 진짜 주제가 없으면 다음 한 줄만 답하세요:
제안: 없음
"""


async def generate_cluster_llm_call(prompt: str) -> str:
    """카테고리 제안 LLM 호출 — llm_client.complete(단발성, 생각모드 끔)로 보냄. LLM_MODE=mock이면 아래 가짜 응답을 씀."""
    return await llm_client.complete(prompt, mock=_mock_cluster_response)


async def _mock_cluster_response(prompt: str) -> str:
    """
    ⚠️ 가짜 LLM 응답 — LLM_MODE=mock(집 개발용)일 때만 씀.

    질문 목록에 "포트폴리오" 키워드가 과반 이상 나오면 패턴이 있는 척(그 질문들의 번호를
    "관련 질문"으로 같이 돌려줌), 아니면(쓰레기/제각각이면) "패턴 없음"으로 응답 — 실제 분류 판단이
    아니라 "LLM 응답 파싱 → 번호 검증 → 중복확인 → category_proposals 저장"으로 이어지는 코드 쪽
    로직을 테스트하기 위한 용도.
    """
    _, _, after = prompt.partition("[질문 목록]\n")
    question_block, _, _ = after.partition("\n\n진짜 반복되는")
    lines = [line for line in question_block.splitlines() if line.strip()]
    related = [i for i, line in enumerate(lines, start=1) if "포트폴리오" in line]
    if lines and len(related) >= len(lines) / 2:
        numbers = ", ".join(str(i) for i in related)
        return (
            "제안: 있음\n카테고리: 학과소개 > 포트폴리오 제출\n"
            f"관련 질문: {numbers}\n이유: 포트폴리오 제출 방법 관련 질문이 반복됨"
        )
    return "제안: 없음"


def _normalize_line(line: str) -> str:
    """LLM 응답 한 줄의 장식을 걷어냄: 굵게/기울임(*), 백틱, 제목 기호(#), 앞의 목록 기호("- "),
    "키 : 값"의 콜론 앞뒤 공백 → "키: 값". 내용(카테고리 이름, 번호)은 건드리지 않음."""
    line = line.replace("*", "").replace("`", "").strip().lstrip("#").strip()
    if line.startswith("- "):
        line = line[2:]
    return re.sub(r"^([가-힣 ]+?)\s*:\s*", r"\1: ", line).strip()


def _parse_cluster_response(raw: str, question_count: int) -> dict | None:
    """LLM 응답에서 제안 내용을 뽑아냄. 제안할 게 없거나 형식이 불완전하면 None.

    "관련 질문" 번호를 받는 이유(2026-10-28 리뷰 11번): 전엔 이름/이유만 받아서, 코드가 "어느 질문이
    이 주제인지" 몰랐음 — 그래서 관련 질문 수 = 샘플 개수(항상 5 이하), 샘플 = 기타 질문 최근 5개
    (주제와 무관한 "날씨" 질문이 섞임)로 저장됐음. 이제 LLM이 고른 번호로 실제 건수와 샘플을 만듦.

    번호 검증(코드 담당): 목록 범위(1~question_count) 밖이거나 중복된 번호는 버림. 유효한 번호가
    하나도 없으면 LLM의 판단 근거를 확인할 수 없으므로 제안하지 않음(None).

    실제 LLM의 흔한 변형 허용(2026-10-28 리뷰 2번): "**제안**: 있음"(굵게 표시), "카테고리 : …"(콜론 앞 공백) 등을
    _normalize_line으로 정리한 뒤 읽음. 전엔 첫 줄이 정확히 "제안: 있음"이어야만 인식해서 이런 응답은 전부
    조용히 "패턴 없음"이 됐음."""
    lines = [_normalize_line(line) for line in raw.strip().splitlines() if line.strip()]
    lines = [line for line in lines if line]
    if not lines or lines[0] != "제안: 있음":
        return None

    major, minor, reason = None, None, ""
    question_numbers: list[int] = []
    for line in lines[1:]:
        if line.startswith("카테고리:") and ">" in line:
            value = line.split(":", 1)[1].strip()
            major, _, minor = value.partition(">")
            major, minor = major.strip(), minor.strip()
        elif line.startswith("관련 질문:"):
            for number in map(int, re.findall(r"\d+", line)):
                if 1 <= number <= question_count and number not in question_numbers:
                    question_numbers.append(number)
        elif line.startswith("이유:"):
            reason = line.split(":", 1)[1].strip()

    if not major or not minor or not question_numbers:
        return None
    return {"major": major, "minor": minor, "reason": reason, "question_numbers": question_numbers}


async def _save_proposal(
    major: str, minor: str, reason: str, related_question_count: int, sample_questions: list[str]
) -> dict:
    """
    저장 전에 두 가지를 확인 — 둘 중 하나라도 걸리면 저장 안 함:
    1. 이미 categories에 실제로 존재하는 대/소분류인지. (2026-10-27 버그 수정: 안 걸러주면
       버튼을 다시 누를 때마다 "이미 승인된 걸 또 제안"하는 무한반복이 생김 — 실측 중 발견.)
    2. 같은 이름의 제안이 대기 중(pending)이거나, 관리자가 이미 거절(rejected)했는지.
       (2026-10-28 리뷰 5번: 전엔 pending만 확인해서, 거절한 제안이 다음 클릭에 다시 생겼음.
       결정: 한 번 거절한 이름은 영구히 다시 제안하지 않음 — 관리자의 거절 의사를 존중하고, 로직을 단순하게.)

    반환: {"saved": True, "proposal": {...}} 또는
         {"saved": False, "reason": "already_category" | "already_pending" | "already_rejected"}
    """
    proposed_name = f"{major} > {minor}"
    pool = await get_pool()
    async with pool.acquire() as conn:
        already_category = await conn.fetchval(
            "SELECT 1 FROM categories WHERE major = $1 AND minor = $2", major, minor
        )
        if already_category:
            return {"saved": False, "reason": "already_category"}

        # 같은 이름의 pending/rejected 제안이 있는지 — 있으면 그 상태를 그대로 사유로 씀
        existing_status = await conn.fetchval(
            """
            SELECT status FROM category_proposals
            WHERE proposed_name = $1 AND status IN ('pending', 'rejected')
            ORDER BY (status = 'pending') DESC
            LIMIT 1
            """,
            proposed_name,
        )
        if existing_status is not None:
            return {"saved": False, "reason": f"already_{existing_status}"}

        row = await conn.fetchrow(
            """
            INSERT INTO category_proposals (proposed_name, related_question_count, sample_questions)
            VALUES ($1, $2, $3)
            RETURNING id, proposed_name, related_question_count, sample_questions, status, created_at
            """,
            proposed_name,
            related_question_count,
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
    parsed = _parse_cluster_response(raw_response, question_count=len(questions))

    if parsed is None:
        return {"status": "no_pattern", "checked_count": total}

    # LLM이 고른 번호(1부터 시작, 프롬프트 목록 순서)로 실제 관련 질문만 골라냄 — 건수와 샘플을 여기서 만듦
    related = [questions[n - 1]["question_text"] for n in parsed["question_numbers"]]
    result = await _save_proposal(
        parsed["major"], parsed["minor"], parsed["reason"],
        related_question_count=len(related),
        sample_questions=related[:SAMPLE_QUESTION_COUNT],
    )
    if not result["saved"]:
        # 프론트(CategorySuggestions.jsx)의 상태별 안내 문구 키와 맞춤
        status = {
            "already_category": "already_category",
            "already_pending": "already_proposed",
            "already_rejected": "already_rejected",
        }[result["reason"]]
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
    """제안을 승인 — 이 순간에만 실제 categories 테이블에 반영됨(LLM은 제안만, 실행은 여기서).

    동시 승인 방지(2026-10-28 리뷰 12번): 전엔 "pending인지 SELECT로 확인 → 카테고리 INSERT →
    approved로 UPDATE"를 트랜잭션 없이 따로 실행해서, 승인 요청 2개가 동시에 오면 둘 다 "아직 pending"으로
    보고 둘 다 성공할 수 있었음. 이제는:
    1. 트랜잭션으로 묶음 — 중간에 실패하면 둘 다 취소(카테고리만 생기고 제안은 pending으로 남는 어긋남 방지)
    2. 확인과 변경을 "WHERE status='pending'인 행만 UPDATE" 한 문장으로 — 동시에 온 두 번째 요청은 첫 번째가
       끝날 때까지 DB가 기다리게 했다가, 이미 approved라 조건에 안 맞아서 아무것도 못 바꿈 → 400
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "UPDATE category_proposals SET status = 'approved' WHERE id = $1 AND status = 'pending' "
                "RETURNING id, proposed_name, related_question_count, sample_questions, status, created_at",
                proposal_id,
            )
            if row is None:
                # 바뀐 행이 없음 = 없는 제안이거나 이미 처리된 제안 — 어느 쪽인지 구분해서 안내
                exists = await conn.fetchval("SELECT 1 FROM category_proposals WHERE id = $1", proposal_id)
                raise ValueError("이미 처리된 제안입니다." if exists else "존재하지 않는 제안입니다.")

            major, _, minor = row["proposed_name"].partition(">")
            await conn.execute(
                "INSERT INTO categories (major, minor) VALUES ($1, $2) ON CONFLICT (major, minor) DO NOTHING",
                major.strip(),
                minor.strip(),
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
