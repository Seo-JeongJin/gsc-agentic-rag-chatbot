"""
질문 의도 분류(question_router.py) 단위 테스트 — 입구에서 질문을 5종류(INFO/ELIG/CHAT/META/OOS)로 나누는 코드 쪽 동작.

실제 분류 판단은 LLM이 하고(학교 실측 2026-11-09: 발화 84개 × 3번 = 98% 정답, 정보 질문 놓침 0), 여기서는 LLM 응답을 직접 넣어서
"프롬프트 조립(앞 대화 포함) → 라벨 해석 → 실패·애매하면 INFO"가 맞게 동작하는지만 확인함.
"""

import llm_client
import question_router
from question_router import CHAT, ELIG, INFO, META, OOS, _build_prompt, _parse_intent, classify_intent

HISTORY = [
    {"role": "user", "content": "글로벌시스템융합과란?"},
    {"role": "assistant", "content": "AI 기술을 배우고 일본 메가벤처에 도전하는 3년제 학과예요."},
]


def test_prompt_has_all_labels_and_the_latest_turn():
    """분류 프롬프트에 라벨 5개와 바로 앞 대화 1턴이 들어가야 함 — 앞 대화가 없으면 "그럼 2차는요?"·"잘 모르는 거야?" 같은
    이어지는 말이 정보 질문인지 대화인지 가를 수 없음(실측: 이어지는 질문 6개·오늘 대화 17개 전부 앞 대화와 함께 판단)."""
    prompt = _build_prompt("잘 모르는거야?", HISTORY)

    for label in (INFO, ELIG, CHAT, META, OOS):
        assert f"- {label}:" in prompt
    assert "사용자: 글로벌시스템융합과란?" in prompt
    assert "챗봇: AI 기술을 배우고" in prompt
    assert "잘 모르는거야?" in prompt


def test_prompt_without_history_says_none():
    """첫 질문이면 앞 대화 칸에 "(없음)" — 빈 칸이면 모델이 앞 대화가 잘린 걸로 오해할 수 있음."""
    assert "[바로 앞 대화]\n(없음)" in _build_prompt("안녕하세요", [])


def test_prompt_keeps_the_examples_that_fixed_misclassification():
    """[실측 2·3차] 예시 문구가 어느 라벨 옆에 있느냐로 결과가 뒤집혔음 — "너 GPT야?"는 META 설명에 넣어야 잡혔고(12 → 0 놓침),
    "일본어를 못하는데 괜찮나요?"는 ELIG 옆에 두면 오히려 ELIG로 갔고(3/3) INFO 설명으로 옮기니 해결. 이 위치를 고정."""
    prompt = _build_prompt("너 GPT야?", [])
    info_part = prompt.split("- INFO:")[1].split("- ELIG:")[0]
    meta_part = prompt.split("- META:")[1].split("- OOS:")[0]

    assert "일본어를 못하는데 괜찮나요?" in info_part
    assert "너 GPT야?" in meta_part


def test_parse_label_variants():
    """실제 LLM은 "**CHAT**", "Chat.", "META - 내부 정보 요청"처럼 장식을 붙이기 쉬움 — 첫 영어 단어만 대문자로 보고 판단."""
    assert _parse_intent("CHAT") == CHAT
    assert _parse_intent("**META**") == META
    assert _parse_intent("Chat.") == CHAT
    assert _parse_intent("OOS - 날씨 질문") == OOS
    assert _parse_intent("elig") == ELIG


def test_unknown_or_empty_label_falls_back_to_info():
    """모르는 라벨·빈 응답·한글만 있는 응답은 INFO — 애매할 때 정보 질문 경로(검색하는 기존 동작)로 보내는 게 가장 안전함.
    정보 질문을 잡담·거절 경로로 잘못 보내면 사용자가 답을 못 받기 때문."""
    for raw in ["", "잘 모르겠어요", "QUESTION", "정보 질문입니다"]:
        assert _parse_intent(raw) == INFO, raw


async def test_llm_failure_falls_back_to_info(monkeypatch):
    """분류 LLM 호출이 실패해도 예외를 밖으로 던지지 않고 INFO — 분류 실패 때문에 질문 전체가 503이 되면 안 됨."""
    async def failing_call(prompt):
        raise llm_client.LLMError("GPU 서버 응답 없음")

    monkeypatch.setattr(question_router, "generate_router_llm_call", failing_call)

    assert await classify_intent("입학 정원이 몇 명이에요?", []) == INFO


async def test_classifies_with_llm_output(monkeypatch):
    """classify_intent가 LLM 응답을 해석해서 돌려주는지 — 끝에서 끝으로 한 번."""
    async def fake_call(prompt):
        return "CHAT"

    monkeypatch.setattr(question_router, "generate_router_llm_call", fake_call)

    assert await classify_intent("잘 모르는거야?", HISTORY) == CHAT


async def test_mock_mode_always_routes_to_info():
    """집 개발(LLM_MODE=mock)에선 항상 INFO — 가짜 LLM 환경의 기존 동작(검색하는 Agent 루프)이 그대로 유지되게."""
    assert await classify_intent("안녕하세요", []) == INFO
