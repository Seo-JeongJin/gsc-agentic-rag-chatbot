"""
LLM 호출 입구(llm_client.py) 테스트 — 리뷰 2번.

실제 vLLM 서버 대신 httpx의 MockTransport(가짜 네트워크)로 "vLLM이 이렇게 응답하면?"을 흉내 냄.
학교에서 실제로 붙였을 때 요청 형식이 틀려서 헤매지 않도록, 보내는 요청 내용까지 같이 확인.
"""

import json

import httpx
import pytest

import llm_client

REAL_ASYNC_CLIENT = httpx.AsyncClient


def _install_fake_vllm(monkeypatch, handler):
    """llm_client가 만드는 httpx.AsyncClient를 "가짜 네트워크를 쓰는 클라이언트"로 바꿔치기.
    handler(request) -> httpx.Response 로 vLLM의 응답을 마음대로 정할 수 있음. 보낸 요청은 sent에 기록."""
    sent = []

    def recording_handler(request):
        sent.append(request)
        return handler(request)

    transport = httpx.MockTransport(recording_handler)
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda **kw: REAL_ASYNC_CLIENT(transport=transport, **kw))
    monkeypatch.setenv("LLM_MODE", "vllm")
    monkeypatch.setenv("VLLM_URL", "http://gpu-server:8000")
    monkeypatch.setenv("VLLM_MODEL", "test-model")
    monkeypatch.setattr(llm_client, "RETRY_BACKOFF_SECONDS", (0, 0))  # 테스트가 실제로 기다리지 않게
    return sent


def _chat_response(content=None, tool_calls=None, status=200):
    """vLLM(OpenAI 호환) /v1/chat/completions 응답 형태."""
    message = {"role": "assistant", "content": content, "tool_calls": tool_calls}
    return httpx.Response(status, json={"choices": [{"message": message}]})


async def _unused_mock(*args):
    raise AssertionError("vllm 모드에서는 mock이 호출되면 안 됨")


# ----- 요청 형식 -----


async def test_vllm_request_has_model_tools_and_thinking_off(monkeypatch):
    """thinking=False면 chat_template_kwargs로 생각모드를 명시적으로 끄고, tools가 있으면 tool_choice=auto로 같이 보내야 함
    (스펙 3절 6번: 서버 기본값은 켜짐, 1~2번째 호출에서만 요청 파라미터로 끔)."""
    sent = _install_fake_vllm(monkeypatch, lambda req: _chat_response(content="답변"))
    tools = [{"type": "function", "function": {"name": "search_documents", "parameters": {}}}]

    await llm_client.chat([{"role": "user", "content": "질문"}], tools=tools, thinking=False, mock=_unused_mock)

    body = json.loads(sent[0].content)
    assert str(sent[0].url) == "http://gpu-server:8000/v1/chat/completions"
    assert body["model"] == "test-model"
    assert body["tools"] == tools and body["tool_choice"] == "auto"
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


async def test_thinking_on_omits_template_kwargs(monkeypatch):
    """thinking=True면 chat_template_kwargs 키 자체를 빼서 모델 기본값(켜짐)이 적용되게 함 — 스펙에서 정한 방식."""
    sent = _install_fake_vllm(monkeypatch, lambda req: _chat_response(content="답변"))

    await llm_client.chat([{"role": "user", "content": "질문"}], thinking=True, mock=_unused_mock)

    assert "chat_template_kwargs" not in json.loads(sent[0].content)


async def test_complete_always_turns_thinking_off(monkeypatch):
    """단발성 호출(카테고리 분류/로그 요약/카테고리 제안)은 항상 생각모드를 꺼야 함 — 켜져 있으면 느리고,
    응답에 생각 과정이 섞여서 "대분류 > 소분류" 같은 정해진 형식 파싱이 깨질 위험이 있음."""
    sent = _install_fake_vllm(monkeypatch, lambda req: _chat_response(content="입학전형 > 일반"))

    await llm_client.complete("프롬프트", mock=_unused_mock)

    body = json.loads(sent[0].content)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert "tools" not in body


async def test_complete_can_turn_thinking_on_for_answer_review(monkeypatch):
    """[2026-10-29 실측] 출력 검토만 예외로 생각모드를 켬 — 꺼진 상태로는 지어낸 답(편입 가능 단정 등)을 20번 중 0번 잡았고,
    켜면 오탐 없이 편입 5/5·틀린 자격 판정 3/5를 잡았음. 생각 과정은 지워지고 JSON 판정만 돌아와야 함."""
    sent = _install_fake_vllm(monkeypatch, lambda req: _chat_response(content='<think>근거 대조…</think>{"verdict": "fail"}'))

    result = await llm_client.complete("프롬프트", mock=_unused_mock, thinking=True)

    assert "chat_template_kwargs" not in json.loads(sent[0].content)
    assert result == '{"verdict": "fail"}'


# ----- 응답 처리 -----


async def test_think_block_is_removed_from_content(monkeypatch):
    """[2번 버그 재현] 생각 과정(<think>…</think>)이 content에 섞여 오면 지우고 실제 답만 돌려줘야 함.
    안 지우면 사용자에게 생각 과정이 그대로 보이고, 카테고리 파서는 </think>의 ">"에서 잘려서 전부 기타로 감."""
    _install_fake_vllm(monkeypatch, lambda req: _chat_response(content="<think>기숙사 질문이네</think>\n\n캠퍼스생활·비용 > 기숙사"))

    assert await llm_client.complete("프롬프트", mock=_unused_mock) == "캠퍼스생활·비용 > 기숙사"


async def test_dangling_think_close_tag_is_removed(monkeypatch):
    """채팅 템플릿이 <think>를 프롬프트 쪽에 넣으면 모델 응답은 "생각…</think>답"처럼 닫는 태그만 올 수 있음 — 이것도 정리."""
    _install_fake_vllm(monkeypatch, lambda req: _chat_response(content="생각 중...</think>\n답변입니다"))

    message = await llm_client.chat([{"role": "user", "content": "q"}], mock=_unused_mock)

    assert message["content"] == "답변입니다"


async def test_tool_calls_are_passed_through(monkeypatch):
    """도구 호출 응답은 tool_calls를 그대로 돌려줘야 agent_loop가 MCP로 전달할 수 있음."""
    tool_calls = [{"id": "c1", "type": "function", "function": {"name": "search_documents", "arguments": "{}"}}]
    _install_fake_vllm(monkeypatch, lambda req: _chat_response(content=None, tool_calls=tool_calls))

    message = await llm_client.chat([{"role": "user", "content": "q"}], mock=_unused_mock)

    assert message["tool_calls"] == tool_calls
    assert message["content"] is None


# ----- 재시도 / 실패 -----


async def test_retries_on_5xx_then_succeeds(monkeypatch):
    """GPU 서버 일시 오류(5xx)는 최대 2번 재시도(스펙 12번) — 2번 실패 후 3번째 성공이면 정상 응답."""
    responses = iter([httpx.Response(503), httpx.Response(500), _chat_response(content="답변")])
    sent = _install_fake_vllm(monkeypatch, lambda req: next(responses))

    message = await llm_client.chat([{"role": "user", "content": "q"}], mock=_unused_mock)

    assert message["content"] == "답변"
    assert len(sent) == 3


async def test_gives_up_after_max_retries(monkeypatch):
    """3번(첫 시도 + 재시도 2번) 다 실패하면 LLMError — 호출하는 쪽이 503/502 등으로 처리."""
    sent = _install_fake_vllm(monkeypatch, lambda req: httpx.Response(503))

    with pytest.raises(llm_client.LLMError):
        await llm_client.chat([{"role": "user", "content": "q"}], mock=_unused_mock)
    assert len(sent) == 3


async def test_4xx_is_not_retried(monkeypatch):
    """4xx(요청 형식이 잘못됨 — 모델 이름 오타 등)는 다시 보내도 똑같이 실패하므로 재시도하지 않고 바로 에러."""
    sent = _install_fake_vllm(monkeypatch, lambda req: httpx.Response(400, json={"error": "model not found"}))

    with pytest.raises(llm_client.LLMError, match="400"):
        await llm_client.chat([{"role": "user", "content": "q"}], mock=_unused_mock)
    assert len(sent) == 1


async def test_vllm_mode_without_url_gives_clear_error(monkeypatch):
    """LLM_MODE=vllm인데 VLLM_URL을 안 채웠으면 원인을 알려주는 에러 — 학교에서 .env 설정 빠뜨렸을 때 바로 알 수 있게."""
    monkeypatch.setenv("LLM_MODE", "vllm")
    monkeypatch.delenv("VLLM_URL", raising=False)

    with pytest.raises(llm_client.LLMError, match="VLLM_URL"):
        await llm_client.chat([{"role": "user", "content": "q"}], mock=_unused_mock)


# ----- mock 모드 -----


async def test_mock_mode_uses_given_mock(monkeypatch):
    """LLM_MODE=mock(기본값)이면 네트워크 없이 호출하는 쪽이 넘긴 가짜 응답 함수를 씀 — 집에서 개발/테스트용."""
    monkeypatch.setenv("LLM_MODE", "mock")

    async def fake(prompt):
        return f"가짜:{prompt}"

    assert await llm_client.complete("안녕", mock=fake) == "가짜:안녕"
