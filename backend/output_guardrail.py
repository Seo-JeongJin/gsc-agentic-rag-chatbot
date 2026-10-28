"""
출력 가드레일 — Agent 루프가 만든 최종 답변이 실제로 근거(검색된 문서/과거답변 본문 +
system_prompt.py의 고정 사실 + 이번 대화의 이전 턴)에 바탕을 두고 있는지 확인하는 마지막 체크포인트.

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

from system_prompt import GROUNDING_FACTS

# 숫자(연도/퍼센트/건수/전화번호 등)는 환각(지어내기) 위험이 가장 큰 요소라 따로 체크.
# 쉼표도 숫자의 일부로 봄("30,000"을 "30"과 "000"으로 쪼개지 않게)
_NUMBER_PATTERN = re.compile(r"\d[\d.,\-]*\d|\d")

MIN_KEYWORD_OVERLAP_RATIO = 0.3
MIN_KEYWORDS_TO_CHECK = 3  # 답변의 키워드가 이보다 적으면(인사말/짧은 안내문 등) 겹침비율 검사 생략

# 이 자릿수 이상인 숫자는 "근거의 더 긴 숫자 안에 들어 있으면" 통과로 봄 — 전화번호를 "053) 940-5441"처럼
# 끊어 쓴 경우 "9405441"이 근거의 "0539405441" 안에 있으므로 OK. 짧은 숫자(1~2자리)는 아무 숫자 안에나
# 우연히 들어 있기 쉬워서(예: "7"은 "2027" 안에도 있음) 정확히 같은 숫자만 인정.
_MIN_DIGITS_FOR_PARTIAL_MATCH = 3


def build_grounded_context(retrieved_context: str, history: list[dict]) -> str:
    """출력 가드레일의 "근거" 텍스트를 조립: 고정 사실 + 이번 대화의 이전 턴 + 이번 루프의 검색 결과 본문.

    2026-10-28 리뷰 4번으로 바뀜 — 전엔 main.py가 SYSTEM_PROMPT "전체"를 근거에 넣어서, 지침 번호("7. …")
    때문에 "7일" 같은 지어낸 숫자가 통과하고, 수백 개 지침 단어 때문에 무관한 답변도 키워드가 우연히 겹쳐
    통과했음(실측 중 발견된 "배경잡음" 문제). 이제 지침(행동 규칙)은 빼고 "답변에 나와도 되는 사실"만 근거로 씀.
    이전 턴을 넣는 이유: 앞에서 이미 안내한 내용을 다시 말하는 답변이 "근거 없음"으로 오탐되지 않게."""
    history_text = "\n".join(m["content"] for m in history if isinstance(m.get("content"), str))
    return "\n".join(part for part in (GROUNDING_FACTS, history_text, retrieved_context) if part)


def _digits(token: str) -> str:
    """숫자 토큰에서 숫자만 남김 — "053-940-5441" → "0539405441", "30,000" → "30000". 표기 차이를 없애고 비교하기 위함."""
    return re.sub(r"\D", "", token)


def _extract_numbers(text: str) -> set[str]:
    return set(_NUMBER_PATTERN.findall(text))


def _unsupported_numbers(answer_text: str, grounded_context: str) -> set[str]:
    """답변의 숫자 중 근거에서 찾을 수 없는 것들. 비교는 구분자를 뺀 숫자만으로 함(표기 차이 허용, 리뷰 4번)."""
    context_digit_tokens = {_digits(t) for t in _extract_numbers(grounded_context)}
    unsupported = set()
    for token in _extract_numbers(answer_text):
        digits = _digits(token)
        if digits in context_digit_tokens:
            continue
        if len(digits) >= _MIN_DIGITS_FOR_PARTIAL_MATCH and any(digits in c for c in context_digit_tokens):
            continue
        unsupported.add(token)
    return unsupported


def _extract_keywords(text: str, min_length: int = 2) -> set[str]:
    """형태소 분석기 없이 한글/영문 토큰만 뽑는 단순 추출 — 조사가 안 떨어져서 완벽하진
    않지만, "답변에만 있고 근거엔 전혀 없는 단어가 너무 많은지"를 보는 용도라 이 정도로 충분."""
    tokens = re.findall(r"[가-힣]+|[A-Za-z]+", text)
    return {t for t in tokens if len(t) >= min_length}


def check_output(answer_text: str, grounded_context: str) -> tuple[bool, str]:
    """
    (통과여부, 실패사유) 반환 — 통과 시 실패사유는 빈 문자열.

    1. 숫자 일치: 답변에 나온 숫자 중 **하나라도** 근거에서 찾을 수 없으면 실패(구분자 차이는 허용 — "053) 940-5441"
       = "053-940-5441"). (전엔 docstring에 "하나도 없으면"이라고 적혀 있었는데 실제 동작은 원래 "하나라도"였음 — 2026-10-28 정정)
    2. 키워드 겹침: 답변 키워드 중 근거에도 있는 비율이 MIN_KEYWORD_OVERLAP_RATIO 미만이면 실패.
       (답변 키워드가 MIN_KEYWORDS_TO_CHECK 미만인 짧은 답변은 이 검사를 생략 — 근거비교 자체가
       의미 없는 짧은 인사말/안내문까지 엄격하게 걸러내면 오탐(FPR)이 커짐.)

    grounded_context는 build_grounded_context()로 만든 것을 넘길 것(main.py 13번 단계).
    """
    unsupported_numbers = _unsupported_numbers(answer_text, grounded_context)
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
