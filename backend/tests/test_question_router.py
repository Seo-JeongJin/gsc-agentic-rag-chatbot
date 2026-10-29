"""
질문 분류(question_router.py) 단위 테스트 — "개인 자격 판정 질문"을 골라내는 코드 쪽 동작.

실제 분류 판단은 LLM이 하고(학교 실측: 11개 질문 × 3번 = 33/33 정답), 여기서는 LLM 응답을 직접 넣어서
"프롬프트 조립 → YES/NO 해석 → 실패 시 일반 경로"가 맞게 동작하는지만 확인함.
"""

import llm_client
import question_router
from question_router import _build_prompt, _parse_router_response, is_personal_eligibility


def test_prompt_contains_question_and_negative_examples():
    """분류 프롬프트에 질문이 들어가고, "자격 기준 자체를 묻는 일반 질문은 NO"라는 예시가 있어야 함 —
    이 예시가 없으면 "입도선매 자격 조건이 뭐예요?"까지 YES가 돼서 일반 질문도 전용 프롬프트로 감."""
    prompt = _build_prompt("내신 4.2등급인데 입도선매 되나요?")
    assert "내신 4.2등급인데 입도선매 되나요?" in prompt
    assert "자격 조건이 뭐예요?" in prompt


def test_parse_yes_and_no_variants():
    """실제 LLM은 "YES", "Yes.", "**YES**", "YES - 개인 성적을…"처럼 장식을 붙이기 쉬움 — 앞의 단어만 봄."""
    for raw in ["YES", "Yes.", "**YES**", "yes", " YES - 개인 성적을 알려주며 묻는 질문"]:
        assert _parse_router_response(raw) is True, raw
    for raw in ["NO", "No.", "**NO**", "no - 일반 질문"]:
        assert _parse_router_response(raw) is False, raw


def test_parse_unclear_response_is_no():
    """YES인지 알 수 없는 응답은 NO(=지금까지와 같은 일반 경로) — 애매할 때 전용 프롬프트로 보내면
    일반 질문의 답이 "기준만 나열하고 판정은 안 하는" 형태로 바뀌어서, 일반 경로가 더 안전한 기본값."""
    for raw in ["", "잘 모르겠습니다", "YESNO 판단 불가", "예"]:
        assert _parse_router_response(raw) is False, raw


async def test_llm_failure_falls_back_to_general_path(monkeypatch):
    """분류 LLM 호출이 실패해도 예외를 밖으로 던지지 않고 False — 분류 실패 때문에 질문 전체가 503이 되면 안 됨."""
    async def failing_call(prompt):
        raise llm_client.LLMError("GPU 서버 응답 없음")

    monkeypatch.setattr(question_router, "generate_router_llm_call", failing_call)

    assert await is_personal_eligibility("내신 4.2등급인데 입도선매 되나요?") is False


async def test_classifies_with_llm_output(monkeypatch):
    """is_personal_eligibility가 LLM 응답을 해석해서 돌려주는지 — 끝에서 끝으로 한 번."""
    async def fake_call(prompt):
        return "YES"

    monkeypatch.setattr(question_router, "generate_router_llm_call", fake_call)

    assert await is_personal_eligibility("JLPT 3급 있는데 입도선매 지원돼요?") is True
