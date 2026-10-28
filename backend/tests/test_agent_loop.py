"""
Agent 루프(agent_loop.py) — "현재 동작 고정" 테스트.

MCP 서버 없이 돌리기 위해 mcp_client의 두 함수(도구목록 조회/도구호출)를 가짜로 바꿔치기함.
LLM은 이미 agent_loop.py 안의 mock(generate_llm_response)이라 그대로 사용.
"""

import json

import pytest

import agent_loop
from system_prompt import ESCALATION_CONTACT_MESSAGE

# 가짜 MCP가 돌려줄 검색 결과 — 실제 search_documents 반환 형식(id/chunk_text/source/similarity)과 동일하게
FAKE_SEARCH_RESULT = [
    {"id": 1, "chunk_text": "기숙사 신청은 입학 후 학생생활관 홈페이지에서 합니다.", "source": "faq.md", "similarity": 0.65}
]


@pytest.fixture
def fake_mcp(monkeypatch):
    """MCP 서버 대신 쓰는 가짜 MCP 클라이언트.
    도구 목록은 빈 리스트, 도구 호출은 항상 FAKE_SEARCH_RESULT를 돌려주고,
    어떤 도구가 어떤 인자로 호출됐는지 calls 리스트에 기록해서 테스트에서 확인할 수 있게 함."""
    calls = []

    async def fake_call_tool(name, arguments):
        calls.append((name, arguments))
        return FAKE_SEARCH_RESULT

    monkeypatch.setattr(agent_loop.mcp_client, "get_cached_tools", lambda: [])
    monkeypatch.setattr(agent_loop.mcp_client, "call_tool", fake_call_tool)
    return calls


async def test_one_tool_call_then_final_answer(fake_mcp):
    """가장 기본 흐름: LLM이 검색 도구를 1번 부르고 → 결과를 보고 → 최종 답변.
    도구 이름/인자가 MCP로 정확히 전달되는지, 반환값의 각 필드(main.py가 그대로 save_log에
    넘기는 값들)가 맞는지, 출력 가드레일이 쓸 근거(retrieved_context)에 검색 결과가 담기는지 확인."""
    result = await agent_loop.run_agent_loop("기숙사 신청 방법", history=[])

    assert fake_mcp == [("search_documents", {"query": "기숙사 신청 방법"})]
    assert result["tool_call_count"] == 1
    assert result["needs_review"] is False
    assert result["review_reason"] is None
    assert isinstance(result["answer_text"], str) and result["answer_text"]
    assert json.loads(result["retrieved_context"]) == FAKE_SEARCH_RESULT


async def test_messages_ordered_system_history_question(fake_mcp, monkeypatch):
    """LLM에게 보내는 대화기록 순서가 [시스템프롬프트 → 과거대화 → 이번질문]인지 확인.
    순서가 틀리면 LLM이 과거 대화를 이번 질문으로 착각하거나 지침을 무시할 수 있음.
    spy로 mock LLM을 감싸서, 실제로 전달된 messages를 가로채 확인함."""
    seen_messages = []
    original = agent_loop.generate_llm_response

    async def spy(messages, tools, thinking):
        seen_messages.append([dict(m) for m in messages])  # 이후 append로 바뀌기 전 상태를 복사해둠
        return await original(messages, tools, thinking)

    monkeypatch.setattr(agent_loop, "generate_llm_response", spy)
    history = [{"role": "user", "content": "이전 질문"}, {"role": "assistant", "content": "이전 답변"}]

    await agent_loop.run_agent_loop("이번 질문", history=history)

    first_call = seen_messages[0]
    assert [m["role"] for m in first_call] == ["system", "user", "assistant", "user"]
    assert first_call[-1]["content"] == "이번 질문"


async def test_loop_limit_returns_escalation_and_needs_review(fake_mcp, monkeypatch):
    """LLM이 계속 도구만 부르고 답을 안 내는 상황 — 코드가 상한(4회)에서 강제로 끊어야 함(11번 단계).
    이때 연락처 안내 문구 + 관리자 검토 필요(insufficient_info) 표시가 되는지 확인.
    덤으로 생각모드 전환 규칙(1~2번째 끔, 3번째부터 켬)이 호출 순서대로 지켜지는지도 기록해서 확인."""
    thinking_flags = []

    async def always_tool_call(messages, tools, thinking):
        """항상 도구 호출만 요청하는 가짜 LLM — 루프가 스스로 끝나지 않게 만듦."""
        thinking_flags.append(thinking)
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"call-{len(thinking_flags)}",
                    "type": "function",
                    "function": {"name": "search_documents", "arguments": json.dumps({"query": "q"})},
                }
            ],
        }

    monkeypatch.setattr(agent_loop, "generate_llm_response", always_tool_call)

    result = await agent_loop.run_agent_loop("헤매는 질문", history=[])

    assert result["answer_text"] == ESCALATION_CONTACT_MESSAGE
    assert result["needs_review"] is True
    assert result["review_reason"] == "insufficient_info"
    assert thinking_flags == [False, False, True, True]
