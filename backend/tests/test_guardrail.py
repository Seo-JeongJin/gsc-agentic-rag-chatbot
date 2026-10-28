"""
입력 가드레일(guardrail.py) — "현재 동작 고정" 테스트.

규칙 하나하나가 막아야 할 걸 막고, 정상 질문은 통과시키는지 확인.
rate limit/중복요청 체크는 모듈 전역 dict에 상태를 쌓기 때문에, 테스트끼리 서로 영향을
주지 않도록 매 테스트 전후로 비워줌(reset_guardrail_state).
"""

import pytest

import guardrail
from guardrail import check_input


@pytest.fixture(autouse=True)
def reset_guardrail_state():
    """매 테스트마다 rate limit 기록·마지막 질문 기록을 초기화.
    이게 없으면 앞 테스트에서 보낸 요청이 뒤 테스트의 rate limit 횟수에 섞여서,
    테스트 실행 순서에 따라 결과가 달라지는 불안정한 테스트가 됨."""
    guardrail._request_timestamps.clear()
    guardrail._last_question.clear()
    yield
    guardrail._request_timestamps.clear()
    guardrail._last_question.clear()


def test_normal_question_passes():
    """평범한 입학 질문은 아무 규칙에도 안 걸리고 통과해야 함.
    가드레일이 정상 질문까지 막으면(오탐) 서비스 자체가 안 되므로 가장 기본이 되는 확인."""
    assert check_input("기숙사 신청은 어떻게 하나요?", "s1") == (True, None)


def test_newline_and_tab_are_allowed():
    """줄바꿈/탭은 제어문자지만 사용자가 자연스럽게 입력할 수 있는 문자라 예외로 허용됨.
    여러 줄로 질문을 쓰는 사용자가 차단되지 않는지 확인."""
    passed, _ = check_input("기숙사\n신청\t방법", "s1")
    assert passed is True


def test_control_character_is_blocked():
    """null byte 같은 보이지 않는 제어문자는 로그 저장·LLM 전달 중 오류를 낼 수 있어 차단."""
    passed, reason = check_input("안녕\x00하세요", "s1")
    assert passed is False
    assert "허용되지 않는 문자" in reason


def test_blank_input_is_blocked():
    """공백만 있는 입력은 빈 질문으로 간주 — LLM을 부를 이유가 없는 요청."""
    passed, reason = check_input("   ", "s1")
    assert passed is False
    assert "비어있습니다" in reason


def test_too_long_input_is_blocked():
    """1000자 초과는 차단 — 프롬프트 스터핑/자원낭비 방지.
    반복문자 규칙에 먼저 걸리지 않도록 "가나다"를 반복해 1002자를 만듦."""
    passed, reason = check_input("가나다" * 334, "s1")
    assert passed is False
    assert "너무 깁니다" in reason


def test_repeated_char_blocked_at_30_but_29_passes():
    """같은 문자 30개 연속부터 스팸으로 판단(경계값 테스트).
    29개는 통과, 30개는 차단 — 기준값(REPEATED_CHAR_THRESHOLD)이 정확히 지켜지는지 확인.
    세션을 나눈 이유: 같은 세션이면 두 번째 요청이 다른 규칙에 영향받지 않게 하기 위함."""
    assert check_input("ㅋ" * 29, "s1")[0] is True
    passed, reason = check_input("ㅎ" * 30, "s2")
    assert passed is False
    assert "반복된 문자" in reason


@pytest.mark.parametrize(
    "text",
    ["<script>alert(1)</script>", "javascript:alert(1)", '<img src=x onerror=alert(1)>'],
)
def test_xss_patterns_are_blocked(text):
    """스크립트 태그 / javascript: URI / 이벤트핸들러 속성 3가지 대표 XSS 패턴 차단.
    관리자 대시보드가 질문 로그를 화면에 띄우기 때문에 저장형 XSS를 막는 보조 방어선."""
    passed, reason = check_input(text, "s1")
    assert passed is False
    assert "허용되지 않는 형식" in reason


@pytest.mark.parametrize(
    ("text", "pii_name"),
    [
        ("제 주민번호는 010101-1234567 입니다", "주민등록번호"),
        ("카드 1234-5678-9012-3456 로 결제", "카드번호"),
        ("연락처는 010-1234-5678 입니다", "전화번호"),
    ],
    ids=["resident_number", "card_number", "mobile_phone"],  # 테스트 결과 출력에 한글이 \uXXXX로 깨져 보이지 않도록 라벨 지정
)
def test_pii_patterns_are_blocked(text, pii_name):
    """주민번호/카드번호/휴대폰번호가 질문에 섞이면 차단 — 민감정보가 questions 로그에
    그대로 남는 것을 막기 위함. 차단 사유에 어떤 종류인지 표시되는지도 확인."""
    passed, reason = check_input(text, "s1")
    assert passed is False
    assert pii_name in reason


def test_fullwidth_digit_phone_is_still_blocked():
    """전각숫자(０１０)로 정규식을 우회하려는 시도도 NFKC 정규화 후 검사해서 잡아야 함."""
    passed, reason = check_input("연락처 ０１０-１２３４-５６７８", "s1")
    assert passed is False
    assert "전화번호" in reason


def test_department_phone_is_not_mistaken_for_pii():
    """학과 대표번호(053-...)는 휴대폰 패턴(01x-...)이 아니라 통과해야 함.
    사용자가 "이 번호 맞나요?"처럼 학과 번호를 되묻는 정상 질문이 막히지 않는지 확인(오탐 방지)."""
    assert check_input("053-940-5441 맞나요?", "s1")[0] is True


def test_same_question_within_10s_is_blocked():
    """같은 세션에서 완전히 같은 질문이 10초 안에 또 오면 차단 — 이중클릭으로 인한
    불필요한 LLM 중복 호출 방지."""
    assert check_input("전형료는 얼마인가요?", "s1")[0] is True
    passed, reason = check_input("전형료는 얼마인가요?", "s1")
    assert passed is False
    assert "동일한 질문" in reason


def test_same_question_from_different_session_passes():
    """중복 차단은 세션 단위 — 서로 다른 사람이 우연히 같은 질문을 하면 둘 다 통과해야 함."""
    assert check_input("전형료는 얼마인가요?", "s1")[0] is True
    assert check_input("전형료는 얼마인가요?", "s2")[0] is True


def test_rate_limit_allows_10_per_minute_and_blocks_11th():
    """1분에 10번까지는 허용, 11번째는 차단(경계값 테스트).
    질문 내용을 매번 다르게 해서 중복 차단 규칙과 섞이지 않게 함."""
    for i in range(10):
        assert check_input(f"질문 {i}번", "s1")[0] is True
    passed, reason = check_input("질문 열한번째", "s1")
    assert passed is False
    assert "요청이 너무 많습니다" in reason
