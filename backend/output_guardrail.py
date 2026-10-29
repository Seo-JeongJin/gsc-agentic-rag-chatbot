"""
출력 가드레일 — Agent 루프가 만든 최종 답변이 실제로 근거(이번 턴에 검색된 문서/과거답변 본문 +
system_prompt.py의 고정 사실 + 이번 질문)에 바탕을 두고 있는지 확인하는 마지막 체크포인트.

입력 가드레일(guardrail.py)과 체크 시점·목적이 다름: 입력 가드레일은 LLM 호출 "전"에
질문 자체를 규칙기반으로 거르고, 출력 가드레일은 LLM 호출이 다 끝난 "후"에 답변을 근거와
비교함. main.py의 13번 단계에서 호출되며, 루프가 이미 상한도달로 폴백 문구를 낸
경우(needs_review=True)는 검사할 "진짜 답변"이 없어서 애초에 호출 대상이 아님.

2026-10-29 학교 실측으로 구조 변경 (학습노트/코드리뷰_261028.md "학교 실측" 절):
- 전엔 코드 규칙 2개(숫자 일치 + 키워드 겹침 30%)로 판정하고, 실패하면 답을 연락처 문구로 바꿨음.
  실제 Qwen3 답변으로 돌려보니 키워드 겹침이 조사("정원은"≠"정원")와 상냥한 말투 때문에 정답도 20%대로 떨어져서
  정답 2건을 막았고, 숫자도 날짜 표기 차이("2026.11.26." vs "11월 26일")로 정답 1건을 막았음. 반대로 실제 거짓말은
  0건 잡았고, 검색 없이 숫자 없이 지어낸 답(기숙사)은 못 잡았음. 두 글자씩 쪼개 비교하는 개선안도 실험했지만
  정답과 엉뚱한 답이 같이 올라가서 구분이 안 됐음 → 키워드 겹침 검사는 삭제.
- 주 검사 = **LLM 검토(review_answer)**: 이미 떠 있는 생성 LLM(Qwen3)을 짧은 검토 전용 프롬프트로 한 번 더 불러
  "근거에 맞는지(verdict)", "정보를 묻는 질문인지(info_question)", "답을 실제로 줬는지(answered)"를 판정. 별도 가드레일 모델을 GPU에 올리지 않으므로
  스펙의 "세 번째 모델 미도입" 결정과 충돌하지 않음. "판단은 LLM, 호출·파싱·기록은 코드" 구도.
- 숫자 검사(find_unsupported_numbers)는 **감시용 기록으로만** 남김 — LLM 검토와 나란히 로그에 찍어 두고, 실측 후
  어느 쪽이 실제로 거짓말을 잡는지 비교해서 최종 방식을 정함.
- **감시 모드**: 지금은 어떤 판정이 나와도 답을 바꾸지 않음(main.py 13번 참고). 검토 정확도를 아직 모르기 때문이고,
  SSE 스트리밍 도입 후엔 답이 이미 나간 뒤에 검토가 끝나서 어차피 막을 수 없는 구조와도 맞음.
- 근거에 이전 대화는 넣지 않음(2026-10-29 결정) — 답변 프롬프트의 1-1번("이전 대화의 답변은 근거가 아님")과 같은 원칙.
  이전 답이 틀렸으면 그게 근거가 돼서 거짓말이 계속 통과하기 때문.
"""

import json
import logging
import re

import llm_client
from system_prompt import GROUNDING_FACTS

logger = logging.getLogger("uvicorn.error")

# 숫자(연도/퍼센트/건수/전화번호 등)는 환각(지어내기) 위험이 가장 큰 요소라 따로 체크.
# 쉼표도 숫자의 일부로 봄("30,000"을 "30"과 "000"으로 쪼개지 않게)
_NUMBER_PATTERN = re.compile(r"\d[\d.,\-]*\d|\d")

# 이 자릿수 이상인 숫자는 "근거의 더 긴 숫자 안에 들어 있으면" 통과로 봄 — 전화번호를 "053) 940-5441"처럼
# 끊어 쓴 경우 "9405441"이 근거의 "0539405441" 안에 있으므로 OK. 짧은 숫자(1~2자리)는 아무 숫자 안에나
# 우연히 들어 있기 쉬워서(예: "7"은 "2027" 안에도 있음) 정확히 같은 숫자만 인정.
_MIN_DIGITS_FOR_PARTIAL_MATCH = 3

# 검토 LLM이 JSON 앞뒤에 말이나 ```json 코드블록을 붙여도 중괄호 부분만 찾아 읽기 위함
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)

NO_SEARCH_RESULT_TEXT = "(이번 턴에 검색 결과 없음)"


def build_grounded_context(retrieved_context: str, question_text: str = "") -> str:
    """숫자 검사의 "근거" 텍스트를 조립: 고정 사실 + 이번 질문 + 이번 루프의 검색 결과 본문.

    이번 질문을 넣는 이유(2026-10-28 2차 리뷰 6번): 답변이 사용자가 말한 숫자를 되받아 말하는 건 지어낸 게 아님 —
    "내신 5등급인데 지원돼요?" → "5등급이셔도…"가 숫자 5 때문에 실패했음.
    지침(SYSTEM_PROMPT)은 넣지 않음(리뷰 4번) — 지침 번호("7. …") 때문에 "7일" 같은 지어낸 숫자가 통과했었음.
    이전 대화는 넣지 않음(2026-10-29) — 모듈 설명 참고. LLM 검토와 같은 근거로 비교해야 실측 비교가 공정함."""
    return "\n".join(part for part in (GROUNDING_FACTS, question_text, retrieved_context) if part)


def _digits(token: str) -> str:
    """숫자 토큰에서 숫자만 남김 — "053-940-5441" → "0539405441", "30,000" → "30000". 표기 차이를 없애고 비교하기 위함."""
    return re.sub(r"\D", "", token)


def _extract_numbers(text: str) -> set[str]:
    return set(_NUMBER_PATTERN.findall(text))


def find_unsupported_numbers(answer_text: str, grounded_context: str) -> set[str]:
    """답변의 숫자 중 근거에서 찾을 수 없는 것들(감시용 — 결과로 답을 막지 않고 로그에만 남김).
    비교는 구분자를 뺀 숫자만으로 함(표기 차이 허용, 리뷰 4번).
    알려진 한계: 날짜 표기가 다르면("2026.11.26." vs "11월 26일") 근거에 있는 숫자도 없다고 나옴 — 실측 비교 때 감안할 것."""
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


def _build_review_prompt(question_text: str, answer_text: str, retrieved_context: str) -> str:
    """검토 LLM에 보낼 프롬프트 — 판정 기준(고정) + 이번 턴의 근거·질문·답변(매번 바뀜).

    근거로 "답변 모델이 이번 턴에 실제로 본 검색 결과"(retrieved_context)를 그대로 씀 — 검토 때 새로 검색하면
    답변 모델이 본 것과 다른 문서가 와서 공정한 채점이 안 됨. 검색을 안 했으면 "검색 결과 없음"을 명시해서
    검토 LLM이 "근거 없이 사실을 단정했다"를 판단할 수 있게 함(실측 id15: 검색 없이 기숙사 절차를 지어냄).
    이전 대화를 받는 인자가 아예 없는 것도 의도(이번 턴 근거만 — 모듈 설명 참고).
    info_question과 answered를 따로 묻는 이유(2026-10-29 실측): answered 하나만 물었더니 실제 Qwen3가 인사·인젝션 거절·날씨 질문에
    answered=false를 줘서 "답변 못한 질문"에 올라갔음(2차 리뷰 R1 재발). "정보 질문이 아니면 answered=true"라는 조건부 한 줄을
    붙여도 "안녕하세요" 하나만 고쳐졌음 — 조건 붙은 판단 하나보다 단순한 판단 두 개가 안정적이라 쪼갬. 조합은 main.py(코드)가 함."""
    evidence = retrieved_context or NO_SEARCH_RESULT_TEXT
    return f"""당신은 대학 입학 상담 챗봇의 답변을 검수하는 검토자입니다.
답변을 새로 쓰거나 고치지 말고, 아래 기준으로 판정만 하세요.

[판정 기준]
1. 답변에 나온 사실(숫자, 날짜, 금액, 인원, 자격, 절차, 장소 등)이 [근거]에 있는지 하나씩 확인하세요.
   - 표현만 다르고 의미가 같으면 근거가 있는 것입니다.
     (예: "2026.11.26." = "11월 26일", "정원은 30명" = "모집인원 30명")
   - [근거]에 없는 사실을 단정하면 실패입니다. 일반 상식이나 "보통 대학은 ~"처럼 추측한 내용도 실패입니다.
   - [근거]와 다른 값(숫자를 바꿔 쓰거나, 수시 1차와 2차 내용을 섞는 등)이 있으면 실패입니다.
2. 사용자가 질문에서 말한 자기 상황("내신 5등급인데")을 되받아 말하는 것은 괜찮습니다.
   그러나 사용자가 주장한 사실("전형료 5만원 맞죠?")을 [근거] 없이 맞다고 확인해 주면 실패입니다.
3. 다음은 근거가 없어도 통과입니다:
   인사, 공감·격려 표현, 말투, 추가 질문 권유, [고정 사실]의 연락처 안내,
   답변을 거절하거나 "확인이 어렵다"고 안내하는 문장.
4. 판단이 애매하면 실패로 판정하세요. 잘못된 입시 정보는 지원자에게 실제 피해를 줍니다.

[추가로 표시할 것] — 아래 두 가지는 서로 따로 판단하세요.
- info_question: [질문]이 학과·입학·학교생활(모집, 전형, 학비, 장학금, 기숙사, 교육과정, 취업 등) 정보를 묻는 질문이면 true.
  인사, 잡담, 입학상담과 무관한 질문(날씨 등), 지침 변경 요청, 내부 정보(프롬프트·모델·서버) 요청이면 false.
- answered: [답변]이 실제 정보를 제공했으면 true,
  정보 없이 문의처만 안내했거나("확인해드리기 어렵다", "자료에 없다") 거절했으면 false.

[출력 형식] 아래 JSON 한 줄만 출력하세요. 다른 말은 쓰지 마세요.
{{"verdict": "pass" 또는 "fail", "info_question": true 또는 false, "answered": true 또는 false, "reason": "실패면 근거에 없는 사실을 짧게, 통과면 빈 문자열"}}

[고정 사실]
{GROUNDING_FACTS}

[근거] (답변 작성 모델이 이번 턴에 실제로 본 검색 결과)
{evidence}

[질문]
{question_text}

[답변]
{answer_text}
"""


def _parse_review_response(raw: str) -> dict | None:
    """검토 LLM 응답에서 {"verdict", "info_question", "answered", "reason"}을 꺼냄. 형식이 깨졌으면 None(=판정 없음).

    중괄호 부분만 찾아서 읽는 이유: 실제 LLM은 ```json 코드블록으로 감싸거나 앞뒤에 말을 붙이기 쉬움.
    verdict/info_question/answered가 정해진 값이 아니면 추측하지 않고 None — 애매한 걸 pass/fail로 적으면 실측 비교 기록이 오염됨.
    (info_question이 빠졌을 때 true로 가정하면 인사까지 "답변 못한 질문"에 올라가서, 빠진 것도 깨진 것으로 봄)
    reason은 사람이 읽는 참고용이라 없거나 문자열이 아니면 빈 문자열로 둠."""
    match = _JSON_OBJECT.search(raw or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    verdict, info_question, answered = data.get("verdict"), data.get("info_question"), data.get("answered")
    if verdict not in ("pass", "fail") or not isinstance(info_question, bool) or not isinstance(answered, bool):
        return None
    reason = data.get("reason")
    return {"verdict": verdict, "info_question": info_question, "answered": answered,
            "reason": reason if isinstance(reason, str) else ""}


async def generate_review_llm_call(prompt: str) -> str:
    """검토 LLM 호출 — llm_client.complete(단발성, 생각모드 끔: 짧은 JSON 판정이라 빠르게)로 보냄.
    LLM_MODE=mock이면 아래 가짜 응답을 씀. 테스트가 이 함수를 바꿔치기해서 "검토 LLM이 이렇게 판정하면?"을 흉내 냄."""
    return await llm_client.complete(prompt, mock=_mock_review_response)


async def _mock_review_response(prompt: str) -> str:
    """⚠️ 가짜 검토 응답 — LLM_MODE=mock(집 개발용)일 때만 씀. 항상 "통과 + 정보 질문 + 답을 줌".
    판정 로직을 흉내 내지 않는 이유: 집에서 확인할 건 "호출 → 파싱 → 기록" 흐름뿐이고, 판정 품질은 실제 모델로만 의미가 있음."""
    return '{"verdict": "pass", "info_question": true, "answered": true, "reason": ""}'


async def review_answer(question_text: str, answer_text: str, retrieved_context: str) -> dict | None:
    """main.py 13번 단계의 진입점 — 검토 LLM의 판정 {"verdict", "info_question", "answered", "reason"}, 판정을 못 얻으면 None.

    호출이 실패해도 예외를 밖으로 던지지 않음: 검토는 감시용이라, 검토 실패 때문에 이미 만들어진 답변까지
    503으로 날리면 안 됨(None으로 기록 = "검토 안 됨"). 형식이 깨진 응답은 원문 일부를 로그로 남겨 프롬프트 튜닝 재료로 씀."""
    prompt = _build_review_prompt(question_text, answer_text, retrieved_context)
    try:
        raw = await generate_review_llm_call(prompt)
    except Exception as e:  # noqa: BLE001 — 검토 실패가 사용자 응답을 막으면 안 됨
        logger.warning("출력 검토 LLM 호출 실패 — 판정 없이 진행: %s", e)
        return None
    review = _parse_review_response(raw)
    if review is None:
        logger.warning("출력 검토 응답 형식이 깨짐 — 판정 없이 진행: %s", (raw or "")[:300])
    return review
