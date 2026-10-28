"""
LLM 응답 파서 — "현재 동작 고정" 테스트.

카테고리 태깅(category_tagging.py)과 카테고리 제안(batch_analysis.py)은 둘 다
"LLM이 낸 텍스트를 코드가 파싱해서 DB에 쓸 값으로 바꾸는" 구조라, 파서가 정상 형식을
제대로 읽는지 + 이상한 값을 안전하게 걸러내는지가 핵심.
(실제 LLM이 <think> 블록·"- " 접두사 등을 섞는 경우는 4단계에서 별도로 추가 예정)
"""

from db.batch_analysis import _parse_cluster_response
from db.category_tagging import _parse_category_response

# 실제 categories 테이블(32행) 대신 쓰는 축소판 — 파서는 "목록에 있는지"만 보므로 몇 개면 충분
CATEGORIES = [
    ("입학전형", "지원자격"),
    ("캠퍼스생활·비용", "기숙사"),
    ("기타", "기타"),
]


# ----- 카테고리 태깅 파서 (category_tagging._parse_category_response) -----

def test_category_valid_format_is_parsed():
    """프롬프트가 요구한 "대분류 > 소분류" 형식 그대로 오면 그 값을 그대로 돌려줘야 함."""
    assert _parse_category_response("입학전형 > 지원자격", CATEGORIES) == ("입학전형", "지원자격")


def test_category_surrounding_whitespace_is_ignored():
    """LLM이 앞뒤 공백·줄바꿈을 붙여도 같은 카테고리로 인식해야 함 — 실제 LLM 출력에 흔함."""
    assert _parse_category_response("  캠퍼스생활·비용  >  기숙사 \n", CATEGORIES) == ("캠퍼스생활·비용", "기숙사")


def test_category_not_in_list_falls_back_to_etc():
    """LLM이 목록에 없는 카테고리를 지어내면 기타>기타로 폴백 — FK 위반이나 엉뚱한 분류가
    DB에 들어가는 걸 코드가 막는 지점("판단은 LLM, 검증은 코드")."""
    assert _parse_category_response("존재안함 > 없는값", CATEGORIES) == ("기타", "기타")


def test_category_without_separator_falls_back_to_etc():
    """구분자(>)가 없는 형식 깨진 응답도 기타>기타로 안전하게 처리."""
    assert _parse_category_response("입학전형", CATEGORIES) == ("기타", "기타")


# ----- 카테고리 제안(T6) 파서 (batch_analysis._parse_cluster_response) -----

def test_cluster_proposal_is_parsed():
    """"제안: 있음" 형식이면 대분류/소분류/이유/관련 질문 번호를 각각 뽑아내야 함
    — 이 값들로 category_proposals의 이름·건수·샘플이 만들어짐.
    (2026-10-28 리뷰 11번 수정으로 "관련 질문" 줄이 응답 형식에 추가됨 — 그 전엔 번호 없이 이름/이유만 받았음)"""
    raw = "제안: 있음\n카테고리: 학과소개 > 포트폴리오 제출\n관련 질문: 2, 4, 5\n이유: 포트폴리오 질문이 반복됨"
    assert _parse_cluster_response(raw, question_count=5) == {
        "major": "학과소개",
        "minor": "포트폴리오 제출",
        "reason": "포트폴리오 질문이 반복됨",
        "question_numbers": [2, 4, 5],
    }


def test_cluster_no_proposal_returns_none():
    """"제안: 없음"이면 None — 호출하는 쪽이 이걸 보고 no_pattern 상태로 응답함."""
    assert _parse_cluster_response("제안: 없음", question_count=5) is None


def test_cluster_proposal_without_category_line_returns_none():
    """"있음"이라고 해놓고 카테고리 줄을 빠뜨린 불완전한 응답은 저장하지 않고 None 처리
    — 이름 없는 제안이 관리자 화면에 뜨는 걸 막음."""
    assert _parse_cluster_response("제안: 있음\n관련 질문: 1, 2\n이유: 뭔가 있음", question_count=5) is None


def test_cluster_out_of_range_and_duplicate_numbers_are_dropped():
    """LLM이 목록에 없는 번호(0, 99)나 같은 번호를 두 번 쓰면 코드가 걸러냄 — 건수가 부풀려지거나
    없는 질문을 샘플로 꺼내다 에러 나는 걸 막는 "검증은 코드" 지점."""
    raw = "제안: 있음\n카테고리: 학과소개 > 포트폴리오 제출\n관련 질문: 0, 1, 1, 3, 99\n이유: 반복됨"
    assert _parse_cluster_response(raw, question_count=5)["question_numbers"] == [1, 3]


def test_cluster_proposal_without_valid_numbers_returns_none():
    """"있음"이라면서 관련 질문 번호가 없거나 전부 범위 밖이면 근거를 확인할 수 없으므로 제안하지 않음(None)."""
    raw = "제안: 있음\n카테고리: 학과소개 > 포트폴리오 제출\n관련 질문: 없음\n이유: 반복됨"
    assert _parse_cluster_response(raw, question_count=5) is None
