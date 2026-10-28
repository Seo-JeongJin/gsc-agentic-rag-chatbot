"""
출력 가드레일(output_guardrail.py) — "현재 동작 고정" 테스트.

check_output() 함수 단독 동작만 확인(근거 텍스트를 직접 넣어줌). main.py가 근거에
SYSTEM_PROMPT를 섞는 문제(리뷰 4번)는 여기가 아니라 4단계에서 별도 테스트로 다룸.
"""

from output_guardrail import build_grounded_context, check_output

# 여러 테스트가 공통으로 쓰는 "검색된 문서" 역할의 근거 텍스트
DORM_CONTEXT = "기숙사 신청은 입학 후 학생생활관 홈페이지에서 할 수 있어요. 2인실 기준으로 운영됩니다."


def test_grounded_answer_passes():
    """근거 문서에 있는 내용 그대로 답한 경우 통과해야 함 — 정상 답변을 막으면(오탐)
    멀쩡한 답변이 전부 에스컬레이션 문구로 바뀌어버리므로 가장 기본이 되는 확인."""
    passed, reason = check_output("기숙사 신청은 입학 후 학생생활관 홈페이지에서 할 수 있어요", DORM_CONTEXT)
    assert passed is True
    assert reason == ""


def test_number_not_in_context_fails():
    """근거에는 "2일"인데 답변이 "7일"이라고 하면 실패해야 함 — 날짜·인원 같은 숫자 환각은
    입학 상담에서 실제 지원 피해로 이어질 수 있어 가장 먼저 잡아야 하는 오류.
    실패 사유에 문제의 숫자(7)가 표시되는지도 확인."""
    passed, reason = check_output("면접은 3월 7일이에요", "면접은 3월 2일이에요")
    assert passed is False
    assert "7" in reason


def test_unrelated_answer_fails():
    """근거(기숙사)와 전혀 관계없는 답변(점심 메뉴)은 키워드 겹침비율이 낮아서 실패해야 함."""
    passed, reason = check_output("오늘 점심 메뉴는 김치찌개와 된장찌개를 추천드려요", DORM_CONTEXT)
    assert passed is False
    assert "겹침비율" in reason


def test_short_greeting_skips_overlap_check():
    """키워드가 3개 미만인 짧은 인사말은 근거와 비교할 의미가 없어 겹침검사를 생략하고 통과.
    이걸 엄격하게 검사하면 "안녕하세요" 같은 응답까지 전부 실패 처리됨."""
    assert check_output("안녕하세요!", DORM_CONTEXT) == (True, "")


# ----- 리뷰 4번: 근거 구성 + 숫자 표기 차이 (2026-10-28 4단계) -----


def test_system_prompt_numbers_do_not_count_as_grounding():
    """[4번 버그 재현] 수정 전엔 근거에 SYSTEM_PROMPT 전체가 들어가서, 지침 번호("7. 당신이 AI이고…")의 7 때문에
    문서엔 "2일"인데 답변이 "7일"이라고 해도 통과했음. 이제 근거는 검색 결과 본문 + 고정 사실 + 대화기록뿐이라 걸러져야 함."""
    context = build_grounded_context(retrieved_context="면접은 3월 2일이에요", history=[])

    passed, reason = check_output("면접은 3월 7일이에요", context)

    assert passed is False
    assert "7" in reason


def test_fixed_contact_facts_are_grounding_without_search():
    """검색 없이도 답할 수 있는 고정 사실(학과 연락처)은 근거로 인정 — 연락처 안내 답변이 "근거 없음"으로 오탐되면 안 됨."""
    context = build_grounded_context(retrieved_context="", history=[])

    passed, _ = check_output("학과 사무실 053-940-5441로 전화해 주세요", context)

    assert passed is True


def test_previous_turn_is_grounding():
    """이전 턴에서 이미 안내한 내용을 다시 말하는 건 근거 있음 — 대화기록(숏텀메모리)도 근거에 포함."""
    history = [
        {"role": "user", "content": "전형료 얼마예요?"},
        {"role": "assistant", "content": "전형료는 30000원이에요."},
    ]
    context = build_grounded_context(retrieved_context="", history=history)

    assert check_output("앞서 말씀드린 대로 전형료는 30000원이에요", context)[0] is True


def test_number_formatting_differences_are_tolerated():
    """같은 숫자를 다르게 표기한 건 통과해야 함 — 문서엔 "053-940-5441", 답변엔 "053) 940-5441"처럼.
    수정 전엔 토큰이 정확히 같아야만 해서 "940-5441"이 근거에 없다고 오탐했음."""
    passed, _ = check_output("053) 940-5441로 연락 주세요", "학과 전화 053-940-5441")
    assert passed is True
