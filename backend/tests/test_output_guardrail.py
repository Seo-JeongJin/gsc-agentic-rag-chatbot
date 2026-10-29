"""
출력 가드레일(output_guardrail.py) 단위 테스트.

2026-10-29 학교 실측으로 구조가 바뀜: 주 검사가 "코드의 키워드 겹침 검사"에서 "LLM 검토(review_answer)"로 바뀌고,
숫자 검사는 감시용 기록(find_unsupported_numbers)으로만 남음. 여기서는 LLM 응답을 직접 넣어서
"프롬프트 조립 → 응답 파싱 → 실패 처리"가 코드 쪽에서 맞게 동작하는지만 확인함(실제 판정 품질은 학교 실측으로 확인).
"""

import llm_client
import output_guardrail
from output_guardrail import (_build_review_prompt, _parse_review_response, build_grounded_context,
                              find_unsupported_numbers, review_answer)

# 오늘(2026-10-29) 실측에서 키워드 겹침 검사(27%)에 걸려 연락처 문구로 바뀌었던 실제 정답
CAPACITY_ANSWER = (
    "2027학년도 글로벌시스템융합과의 입학 정원은 총 30명입니다. 이 중 수시 1차 모집은 25명, "
    "수시 2차 모집은 5명으로 이루어져 있어요. 궁금한 점이 더 있으시면 언제든지 문의해주세요! 😊"
)
CAPACITY_CONTEXT = "| 정원 (1차 25 · 2차 5) | 30명 |\n2027학년도 입학 정원은 30명(수시 1차 25명, 수시 2차 5명)"


# ----- 숫자 검사 (감시용 기록 — 답변을 막지 않음) -----


def test_number_not_in_context_is_reported():
    """근거에는 "2일"인데 답변이 "7일"이라고 하면 근거 없는 숫자로 보고돼야 함 — 날짜·인원 같은 숫자 환각이
    가장 위험해서, LLM 검토와 나란히 기록해 두고 실측 후 비교하기 위함."""
    assert find_unsupported_numbers("면접은 3월 7일이에요", "면접은 3월 2일이에요") == {"7"}


def test_correct_capacity_answer_has_no_unsupported_numbers():
    """오늘 막혔던 정답(30·25·5명)은 숫자 검사로는 문제가 없었음 — 막힌 원인은 키워드 겹침 검사였다는 것을 고정."""
    context = build_grounded_context(CAPACITY_CONTEXT, "입학 정원이 몇명이냐")
    assert find_unsupported_numbers(CAPACITY_ANSWER, context) == set()


def test_number_formatting_differences_are_tolerated():
    """같은 숫자를 다르게 표기한 건 문제없음 — 문서엔 "053-940-5441", 답변엔 "053) 940-5441"."""
    assert find_unsupported_numbers("053) 940-5441로 연락 주세요", "학과 전화 053-940-5441") == set()


def test_fixed_contact_facts_and_current_question_are_grounding():
    """검색 없이도 나올 수 있는 고정 사실(연락처)과, 사용자가 이번 질문에서 말한 숫자("5등급")는 근거로 인정."""
    context = build_grounded_context("", "내신 5등급인데 지원돼요?")
    assert find_unsupported_numbers("5등급이셔도 053-940-5441로 문의하시면 돼요", context) == set()


def test_system_prompt_numbers_do_not_count_as_grounding():
    """근거에 시스템 프롬프트 지침 번호("7. …")가 섞이면 "7일" 같은 지어낸 숫자가 통과함(리뷰 4번) — 근거는 고정 사실뿐."""
    context = build_grounded_context("면접은 3월 2일이에요", "")
    assert find_unsupported_numbers("면접은 3월 7일이에요", context) == {"7"}


# ----- LLM 검토: 프롬프트 조립 -----


def test_review_prompt_contains_this_turn_evidence_only():
    """검토 프롬프트에는 고정 사실 + 이번 턴 검색 결과 + 질문 + 답변이 들어감(2026-10-29 결정: 이전 대화는 근거가 아님).
    이전 대화를 받는 인자 자체가 없다는 것까지 함수 시그니처로 고정."""
    prompt = _build_review_prompt("입학 정원이 몇명이냐", CAPACITY_ANSWER, CAPACITY_CONTEXT)

    assert "053-940-5441" in prompt          # 고정 사실(연락처)
    assert CAPACITY_CONTEXT in prompt         # 이번 턴 검색 결과
    assert "입학 정원이 몇명이냐" in prompt
    assert CAPACITY_ANSWER in prompt


def test_review_prompt_marks_missing_search():
    """검색을 안 한 답변이면 근거 칸에 "검색 결과 없음"을 명시 — 그래야 검토 LLM이 "근거 없이 사실을 단정했다"를 판단할 수 있음
    (실측 id15: 검색 없이 "일반적인 대학 기숙사 절차는…"이라고 지어낸 답을 코드 검사는 못 잡았음)."""
    prompt = _build_review_prompt("기숙사 신청은 어떻게 하나요?", "일반적인 대학 기숙사는 입학 후 신청해요.", "")
    assert "(이번 턴에 검색 결과 없음)" in prompt


def test_review_prompt_asks_info_question_separately():
    """[2026-10-29 실측 버그 재현] 실제 Qwen3가 "반가워요"·인젝션 거절·날씨 질문에 answered=false를 줘서 대시보드 "답변 못한 질문"에
    올라갔음(2차 리뷰 R1 재발). "정보 질문이 아니면 answered=true"처럼 조건을 붙인 한 줄로는 "안녕하세요" 하나만 고쳐졌음 →
    "정보를 묻는 질문인가"(info_question)를 따로 묻고, 조합은 코드(main.py)가 함. 판정 품질 자체는 학교 실측으로 확인."""
    prompt = _build_review_prompt("반가워요", "안녕하세요! 무엇이든 물어보세요.", "")
    assert '"info_question"' in prompt
    assert "애초에 정보를 묻는 질문이 아니었으면 true로 표시하세요" not in prompt  # 효과 없던 조건부 문구는 제거


# ----- LLM 검토: 응답 파싱 -----


def test_parse_valid_review():
    """정상 JSON 한 줄 → verdict/info_question/answered/reason을 그대로 꺼냄."""
    raw = '{"verdict": "fail", "info_question": true, "answered": true, "reason": "근거에 없는 금액(60만 원)"}'
    assert _parse_review_response(raw) == {
        "verdict": "fail", "info_question": True, "answered": True, "reason": "근거에 없는 금액(60만 원)"}


def test_parse_review_wrapped_in_code_fence_or_text():
    """실제 LLM은 JSON을 ```json 코드블록으로 감싸거나 앞뒤에 말을 붙이기 쉬움 — 중괄호 부분만 찾아서 읽어야 함."""
    raw = '판정 결과입니다.\n```json\n{"verdict": "pass", "info_question": false, "answered": false, "reason": ""}\n```'
    assert _parse_review_response(raw) == {"verdict": "pass", "info_question": False, "answered": False, "reason": ""}


def test_parse_broken_review_returns_none():
    """형식이 깨졌거나 값이 이상하면 None(=판정 없음) — 추측해서 pass/fail을 정하면 기록이 오염됨.
    info_question이 빠진 것도 깨진 것으로 봄 — 빠졌을 때 true로 가정하면 인사까지 "답변 못한 질문"에 올라감."""
    for raw in ["통과입니다", '{"verdict": "maybe", "info_question": true, "answered": true}', '{"verdict": "pass"}',
                '{"verdict": "pass", "info_question": true, "answered": "yes"}',
                '{"verdict": "pass", "answered": true}',
                '{"verdict": "pass", "info_question": "no", "answered": true}',
                '{"verdict": "pass", "info_question": true, "answered": true']:
        assert _parse_review_response(raw) is None, raw


def test_parse_review_without_reason_defaults_to_empty():
    """reason이 빠져도 나머지가 정상이면 판정은 유효 — reason은 사람이 읽는 참고용이라 필수가 아님."""
    assert _parse_review_response('{"verdict": "pass", "info_question": true, "answered": true}') == {
        "verdict": "pass", "info_question": True, "answered": True, "reason": ""}


# ----- LLM 검토: 호출 실패 -----


async def test_review_returns_none_when_llm_fails(monkeypatch):
    """검토 LLM 호출이 실패해도 예외를 밖으로 던지지 않고 None — 검토는 감시용이라, 검토 실패 때문에
    이미 만들어진 답변까지 503으로 날리면 안 됨."""
    async def failing_call(prompt):
        raise llm_client.LLMError("GPU 서버 응답 없음")

    monkeypatch.setattr(output_guardrail, "generate_review_llm_call", failing_call)

    assert await review_answer("입학 정원이 몇명이냐", CAPACITY_ANSWER, CAPACITY_CONTEXT) is None


async def test_review_parses_llm_output(monkeypatch):
    """review_answer가 LLM 응답을 파싱해서 돌려주는지 — 끝에서 끝으로 한 번."""
    async def fake_call(prompt):
        return '{"verdict": "pass", "info_question": true, "answered": true, "reason": ""}'

    monkeypatch.setattr(output_guardrail, "generate_review_llm_call", fake_call)

    assert await review_answer("입학 정원이 몇명이냐", CAPACITY_ANSWER, CAPACITY_CONTEXT) == {
        "verdict": "pass", "info_question": True, "answered": True, "reason": ""}
