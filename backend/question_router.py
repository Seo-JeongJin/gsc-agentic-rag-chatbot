"""
질문 분류 — Agent 루프 시작 전에, 이번 질문을 어떤 시스템 프롬프트로 답할지 정함 (2026-10-29 학교 실측 B9).

왜 필요한가: 사용자가 자기 성적·자격을 알려주며 "자격이 되나요?"라고 물으면, 실제 Qwen3가 기본 프롬프트로는
직접 계산해서 틀린 판정을 냈음("내신 4.2·5.8등급" → 평균 5.0, 기준 5.9 이내라 자격 되는데 "충족하지 못합니다" — 15/15 판정).
기본 프롬프트에 금지 규칙을 넣거나 위치를 옮기거나 생각모드를 켜도 최선이 3/5. 규칙 5줄짜리 전용 프롬프트로는 0/15.
→ 기본 프롬프트에 규칙을 더 쌓는 대신, 이런 질문만 골라서 전용 프롬프트(system_prompt.ELIGIBILITY_PROMPT)로 보냄.

"판단은 LLM, 경로 선택은 코드": "개인 자격 판정 질문인가"는 LLM이 판단(실측 33/33 정답)하고, 그 결과로 어느 프롬프트를
쓸지는 코드(main.py)가 정함. 판정을 못 얻으면(호출 실패·애매한 응답) 항상 기본 경로 — 지금까지와 같은 동작이라 가장 안전함.
SSE 도입(예정)과도 맞음: 답을 만들기 "전"에 경로를 정하므로, 답이 스트리밍된 뒤에 고칠 필요가 없음.
"""

import logging
import re

import llm_client

logger = logging.getLogger("uvicorn.error")

# 응답 앞에 붙기 쉬운 장식(**굵게**, 따옴표, 공백)을 걷어낸 뒤 첫 단어만 봄
_FIRST_WORD = re.compile(r"[A-Za-z]+")


def _build_prompt(question_text: str) -> str:
    """분류 프롬프트 — 학교 실측(2026-10-29)에서 33/33 맞힌 문구 그대로.
    NO 예시 두 개("자격 조건이 뭐예요?", "고3이 아니어도?")를 지우지 말 것: 이게 없으면 자격 "기준"을 묻는 일반 질문까지
    YES가 돼서, 일반 질문의 답이 "기준만 나열하고 판정은 안 하는" 형태로 바뀜."""
    return f"""다음 질문이 "사용자가 자기 성적·등급·자격증·출신 학교·학력 같은 개인 정보를 알려주면서, 본인이 지원 자격이 되는지 판정해 달라고 묻는 질문"이면 YES, 아니면 NO라고만 답하세요.
- 자격 기준 자체를 묻는 일반 질문(예: "입도선매 자격 조건이 뭐예요?")은 NO입니다.
- 개인 정보 없이 일반적인 경우를 묻는 질문(예: "고3이 아니어도 지원할 수 있어요?")은 NO입니다.

[질문]
{question_text}"""


def _parse_router_response(raw: str) -> bool:
    """LLM 응답의 첫 영어 단어가 YES면 True, 그 밖(NO·빈 응답·한글 "예"·알 수 없는 말)은 전부 False.
    애매하면 False인 이유: 기본 경로가 지금까지의 동작이라, 잘못 YES로 보내는 것보다 안전함."""
    match = _FIRST_WORD.search(raw or "")
    return bool(match) and match.group(0).upper() == "YES"


async def generate_router_llm_call(prompt: str) -> str:
    """분류 LLM 호출 — llm_client.complete(단발성, 생각모드 끔: YES/NO 한 단어라 빠르게, 실측 0.2~0.3초).
    LLM_MODE=mock이면 아래 가짜 응답을 씀. 테스트가 이 함수를 바꿔치기해서 "LLM이 YES라고 하면?"을 흉내 냄."""
    return await llm_client.complete(prompt, mock=_mock_router_response)


async def _mock_router_response(prompt: str) -> str:
    """⚠️ 가짜 분류 응답 — LLM_MODE=mock(집 개발용)일 때만 씀. 항상 NO(=기본 경로)라 집에서의 기존 동작이 그대로 유지됨."""
    return "NO"


async def is_personal_eligibility(question_text: str) -> bool:
    """main.py가 Agent 루프 전에 호출하는 진입점 — 개인 자격 판정 질문이면 True.
    호출이 실패해도 예외를 밖으로 던지지 않고 False: 분류는 경로 선택용이라, 분류 실패 때문에 질문 전체가 503이 되면 안 됨."""
    try:
        raw = await generate_router_llm_call(_build_prompt(question_text))
    except Exception as e:  # noqa: BLE001 — 분류 실패가 사용자 응답을 막으면 안 됨
        logger.warning("질문 분류 LLM 호출 실패 — 기본 경로로 진행: %s", e)
        return False
    return _parse_router_response(raw)
