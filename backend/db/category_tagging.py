"""
카테고리(대/소분류) 태깅 — questions.category_id를 채워주는 백그라운드 작업.

main.py의 POST /api/ask가 사용자에게 응답을 반환한 "다음에" background_tasks로 호출됨 —
분류 LLM 호출이 끝나길 기다리지 않아서 사용자 응답 속도에 영향 없음.

여기서 쓰는 LLM 호출도 agent_loop.py의 도구호출 루프와 다름 — 도구 없이 "질문+답변+카테고리
전체목록 주고 그 중 하나 고르게" 하는 단발성 호출(analysis_service.py와 같은 패턴).

"판단은 LLM, 매핑/실행은 코드" 원칙: LLM은 categories.id를 몰라도 되고 "대분류 > 소분류"
문자열만 사람이 읽는 이름으로 고르면, 코드가 실제 FK값으로 변환해서 저장함.

실패를 두 종류로 구분해서 다르게 처리함 (2026-10-28 리뷰 14번 — 전엔 이 설명이 실제 동작과 반대로 적혀 있었음):
- LLM이 "답은 했는데" 목록에 없는 값/깨진 형식을 낸 경우 → 기타>기타로 폴백
  (LLM이 판단은 했지만 맞는 칸이 없다고 본 것 = 진짜 "기타" 질문으로 취급)
- 호출 자체가 실패(네트워크/DB 오류 등)한 경우 → 아무것도 안 쓰고 category_id를 NULL로 둠
  (판단 자체가 없었으므로 기타로 넣으면 안 됨 — 넣으면 ① 나중에 실패 건만 골라 다시 분류할 수
  없고 ② 카테고리 제안(batch_analysis.py)이 실패 건까지 "기타 질문"으로 세서 엉뚱한 제안을 함)
그래서 대시보드의 unclassified_count(NULL 개수) = "태깅 기능 도입 전 과거 질문 + 분류 호출 실패 건".
"""

from db.connection import get_pool

FALLBACK_MAJOR = "기타"
FALLBACK_MINOR = "기타"


async def _fetch_all_categories() -> list[tuple[str, str]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT major, minor FROM categories ORDER BY major, minor")
        return [(row["major"], row["minor"]) for row in rows]


def _build_prompt(question_text: str, answer_text: str, categories: list[tuple[str, str]]) -> str:
    category_lines = "\n".join(f"- {major} > {minor}" for major, minor in categories)
    return f"""다음 질문과 답변을 보고, 아래 카테고리 목록 중 가장 알맞은 것을 정확히 하나만 골라
"대분류 > 소분류" 형식 그대로만 답하세요(다른 말 덧붙이지 마세요). 목록에 없는 카테고리를
만들어내지 마세요. 뭘 골라야 할지 애매하면 해당 대분류의 "일반"을 고르세요.

[질문]
{question_text}

[답변]
{answer_text}

[카테고리 목록]
{category_lines}
"""


def _extract_question_section(prompt: str) -> str:
    """mock 전용 — 프롬프트에서 "[질문]" 섹션만 떼어냄. 프롬프트 전체에는 카테고리 목록(예:
    "캠퍼스생활·비용 > 기숙사" 같은 줄)도 같이 들어있어서, 거기까지 통째로 키워드 매칭하면
    질문 내용과 무관하게 항상 같은 카테고리명이 먼저 걸려버림 — 그래서 질문 부분만 분리."""
    _, _, after_q = prompt.partition("[질문]\n")
    question_section, _, _ = after_q.partition("\n\n[답변]")
    return question_section


async def generate_category_llm_call(prompt: str) -> str:
    """
    ⚠️ 가짜 LLM 호출 — agent_loop.py/analysis_service.py와 같은 이유로 가짜(GPU서버가 교내망
    전용이라 집에서 연결 불가). 실제로는 위 프롬프트 하나를 vLLM에 단발성으로 보내면 됨.

    지금은 질문 텍스트에 포함된 키워드로 비슷한 카테고리를 대충 골라서, "LLM 응답 파싱 →
    유효성 검증 → categories.id 매핑 → questions 업데이트"로 이어지는 코드 쪽 로직이
    실제로 맞게 동작하는지 테스트할 수 있게만 해둠(실제 분류 판단 로직 아님).
    키워드가 하나도 안 걸리면 일부러 목록에 없는 값을 반환해서 기타>기타 폴백 경로를 테스트.
    """
    question_section = _extract_question_section(prompt)
    keyword_to_category = [
        ("기숙사", ("캠퍼스생활·비용", "기숙사")),
        ("학비", ("캠퍼스생활·비용", "학비·장학금")),
        ("장학", ("캠퍼스생활·비용", "학비·장학금")),
        ("면접", ("면접", "일반")),
        ("전형료", ("입학전형", "원서접수·전형료")),
        ("지원 자격", ("입학전형", "지원자격")),
        ("전형", ("입학전형", "일반")),
        ("일본어 교재", ("교육과정", "일본어 수업")),
        ("일본", ("일본취업", "일반")),
        ("취업", ("일본취업", "일반")),
    ]
    for keyword, (major, minor) in keyword_to_category:
        if keyword in question_section:
            return f"{major} > {minor}"
    return "존재하지않는대분류 > 존재하지않는소분류"


def _parse_category_response(raw: str, categories: list[tuple[str, str]]) -> tuple[str, str]:
    """"대분류 > 소분류" 형식을 파싱해서 실제 categories 목록에 있는 값인지 확인.
    형식이 깨졌거나 목록에 없는 값이면 기타>기타로 폴백."""
    if ">" in raw:
        major, _, minor = raw.partition(">")
        major, minor = major.strip(), minor.strip()
        if (major, minor) in categories:
            return major, minor
    return FALLBACK_MAJOR, FALLBACK_MINOR


async def tag_question(question_id: int, question_text: str, answer_text: str) -> None:
    """main.py가 백그라운드 태스크로 호출하는 진입점.

    실패해도 예외를 밖으로 던지지 않음 — 이미 questions 로그 자체는 저장이 끝난 뒤라,
    분류만 실패해도 category_id가 NULL로 남을 뿐(=미분류 집계에 잡힘) 서비스에는 영향 없음."""
    try:
        categories = await _fetch_all_categories()
        prompt = _build_prompt(question_text, answer_text, categories)
        raw_response = await generate_category_llm_call(prompt)
        major, minor = _parse_category_response(raw_response, categories)

        pool = await get_pool()
        async with pool.acquire() as conn:
            category_id = await conn.fetchval(
                "SELECT id FROM categories WHERE major=$1 AND minor=$2", major, minor
            )
            await conn.execute(
                "UPDATE questions SET category_id=$1 WHERE id=$2", category_id, question_id
            )
    except Exception as e:  # noqa: BLE001 — 백그라운드 태스크 실패가 메인 요청에 전파되면 안 됨
        print(f"[category_tagging] question_id={question_id} 분류 실패: {e}")


async def retag_etc_questions() -> int:
    """기타>기타로 분류돼 있는 질문 전부를 지금의 카테고리 목록으로 다시 분류. 다시 분류한 질문 수를 반환.

    카테고리 제안을 승인한 직후 admin_api.py가 백그라운드로 호출함(2026-10-28 결정). 이유:
    승인으로 새 카테고리가 생겨도, 그 제안을 만들게 한 원래 질문들은 계속 기타에 남아 있었음 —
    그래서 ① 새 카테고리 질문 수가 0으로 보이고 ② 다음 "카테고리 제안 확인하기"에서 같은 질문들로
    또 같은 주제를 판단하게 됨. 각 질문은 tag_question과 똑같이(LLM이 고르고 코드가 검증) 처리되므로,
    새 카테고리에 안 맞는 질문은 그대로 기타에 남음.

    질문마다 LLM을 1번씩 부르므로 질문이 많으면 오래 걸림 — 그래서 승인 응답을 기다리게 하지 않고 백그라운드로 돌림."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT q.id, q.question_text, q.answer_text
            FROM questions q
            JOIN categories c ON c.id = q.category_id
            WHERE c.major = $1 AND c.minor = $2
            ORDER BY q.id
            """,
            FALLBACK_MAJOR,
            FALLBACK_MINOR,
        )
    for row in rows:
        await tag_question(row["id"], row["question_text"], row["answer_text"] or "")
    return len(rows)
