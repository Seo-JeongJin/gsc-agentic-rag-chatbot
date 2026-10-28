"""
출력 가드레일 — Agent 루프가 만든 최종 답변이 실제로 근거(검색된 문서/과거답변 +
system_prompt.py의 고정 지침)에 바탕을 두고 있는지 확인하는 마지막 체크포인트.

입력 가드레일(guardrail.py)과 체크 시점·목적이 다름: 입력 가드레일은 LLM 호출 "전"에
질문 자체를 규칙기반으로 거르고, 출력 가드레일은 LLM 호출이 다 끝난 "후"에 답변을 근거와
비교해서 거름. main.py의 13번 단계에서 호출되며, 루프가 이미 상한도달로 폴백 문구를 낸
경우(needs_review=True)는 검사할 "진짜 답변"이 없어서 애초에 호출 대상이 아님.

⚠️ 설계 원칙(CLAUDE.md 핵심설계원칙): 가드레일 "모델"(별도 NLI/임베딩유사도 모델)은 GPU
자원 제약으로 도입하지 않고 규칙 기반으로 구현. 정확도는 모델보다 떨어질 수 있고, 아래
MIN_KEYWORD_OVERLAP_RATIO 값은 지금은 임의값 — 실제 vLLM 연결 후 다양한 실제 답변으로
FPR/FNR을 재보고 튜닝해야 함(지금은 가짜 LLM이라 답변 패턴이 고정돼 있어 튜닝 자체가
의미 없음, `학습노트/실측테스트_체크리스트.md`에 추가해둘 것).
"""

import re

# 숫자(연도/퍼센트/건수/전화번호 등)는 환각(지어내기) 위험이 가장 큰 요소라 따로 체크.
_NUMBER_PATTERN = re.compile(r"\d[\d.\-]*\d|\d")

MIN_KEYWORD_OVERLAP_RATIO = 0.3
MIN_KEYWORDS_TO_CHECK = 3  # 답변의 키워드가 이보다 적으면(인사말/짧은 안내문 등) 겹침비율 검사 생략


def _extract_numbers(text: str) -> set[str]:
    return set(_NUMBER_PATTERN.findall(text))


def _extract_keywords(text: str, min_length: int = 2) -> set[str]:
    """형태소 분석기 없이 한글/영문 토큰만 뽑는 단순 추출 — 조사가 안 떨어져서 완벽하진
    않지만, "답변에만 있고 근거엔 전혀 없는 단어가 너무 많은지"를 보는 용도라 이 정도로 충분."""
    tokens = re.findall(r"[가-힣]+|[A-Za-z]+", text)
    return {t for t in tokens if len(t) >= min_length}


def check_output(answer_text: str, grounded_context: str) -> tuple[bool, str]:
    """
    (통과여부, 실패사유) 반환 — 통과 시 실패사유는 빈 문자열.

    1. 숫자 일치: 답변의 숫자가 근거에 하나도 없으면 실패.
    2. 키워드 겹침: 답변 키워드 중 근거에도 있는 비율이 MIN_KEYWORD_OVERLAP_RATIO 미만이면 실패.
       (답변 키워드가 MIN_KEYWORDS_TO_CHECK 미만인 짧은 답변은 이 검사를 생략 — 근거비교 자체가
       의미 없는 짧은 인사말/안내문까지 엄격하게 걸러내면 오탐(FPR)이 커짐.)
    """
    answer_numbers = _extract_numbers(answer_text)
    context_numbers = _extract_numbers(grounded_context)
    unsupported_numbers = answer_numbers - context_numbers
    if unsupported_numbers:
        return False, f"답변에만 있고 근거에는 없는 숫자: {', '.join(sorted(unsupported_numbers))}"

    answer_keywords = _extract_keywords(answer_text)
    if len(answer_keywords) < MIN_KEYWORDS_TO_CHECK:
        return True, ""

    context_keywords = _extract_keywords(grounded_context)
    overlap_ratio = len(answer_keywords & context_keywords) / len(answer_keywords)
    if overlap_ratio < MIN_KEYWORD_OVERLAP_RATIO:
        return False, f"근거와의 키워드 겹침비율이 너무 낮음({overlap_ratio:.0%})"

    return True, ""
