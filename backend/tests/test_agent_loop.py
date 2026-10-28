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
    # 출력 가드레일이 쓸 근거 — 검색 결과의 "본문"만 담겨야 함(2026-10-28 리뷰 4번으로 변경:
    # 전엔 JSON 전체라 id·similarity 숫자까지 근거로 인정돼서 숫자 검사가 흐려졌음)
    assert "기숙사 신청은 입학 후 학생생활관 홈페이지에서 합니다." in result["retrieved_context"]
    assert "similarity" not in result["retrieved_context"]
    assert "0.65" not in result["retrieved_context"]


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
    # [3번 버그 재현] 마지막(4번째) LLM 호출이 또 도구를 요청해도 실행하지 않아야 함 — 그 결과를 볼 LLM 호출이
    # 더 없어서 검색 비용만 들고 tool_call_count만 부풀었음(2026-10-28 결정: 실행 안 하고 바로 에스컬레이션)
    assert len(fake_mcp) == 3
    assert result["tool_call_count"] == 3


# ----- 리뷰 3번: 실제 LLM이 이상하게 응답해도 500으로 죽지 않기 (2026-10-28 4단계) -----


def _tool_call_response(name: str, arguments: str) -> dict:
    """도구 호출을 요청하는 LLM 응답 한 개."""
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": name, "arguments": arguments}}],
    }


def _scripted_llm(monkeypatch, responses: list[dict]) -> list[list[dict]]:
    """LLM이 차례로 responses를 돌려주게 함. 매 호출 때 받은 messages를 기록해서 반환(무엇을 LLM에 보여줬는지 확인용)."""
    seen = []
    queue = list(responses)

    async def scripted(messages, tools, thinking):
        seen.append([dict(m) for m in messages])
        return queue.pop(0)

    monkeypatch.setattr(agent_loop, "generate_llm_response", scripted)
    return seen


async def test_broken_tool_arguments_are_reported_back_to_llm(fake_mcp, monkeypatch):
    """[3번 버그 재현] LLM이 도구 인자를 깨진 JSON으로 보내면, 수정 전엔 json.loads 예외로 /api/ask가 500이었음.
    이제는 MCP를 부르지 않고 "인자를 해석할 수 없음"을 도구 결과로 LLM에게 돌려줘서, 다음 턴에 LLM이 스스로 고치게 함."""
    seen = _scripted_llm(monkeypatch, [
        _tool_call_response("search_documents", "{query: 깨진JSON"),
        {"role": "assistant", "content": "다시 확인한 답변입니다", "tool_calls": None},
    ])

    result = await agent_loop.run_agent_loop("질문", history=[])

    assert fake_mcp == []  # 깨진 인자로는 MCP를 부르지 않음
    assert result["answer_text"] == "다시 확인한 답변입니다"
    tool_message = seen[1][-1]
    assert tool_message["role"] == "tool" and "인자" in tool_message["content"]


async def test_unknown_tool_is_reported_back_to_llm(fake_mcp, monkeypatch):
    """[3번 버그 재현] LLM이 없는 도구 이름을 지어내면(예: "search_web") MCP에 보내지 않고 에러를 도구 결과로 돌려줌."""
    monkeypatch.setattr(agent_loop.mcp_client, "get_cached_tools", lambda: [
        {"type": "function", "function": {"name": "search_documents"}},
    ])
    seen = _scripted_llm(monkeypatch, [
        _tool_call_response("search_web", json.dumps({"query": "q"})),
        {"role": "assistant", "content": "답변", "tool_calls": None},
    ])

    await agent_loop.run_agent_loop("질문", history=[])

    assert fake_mcp == []
    assert "search_web" in seen[1][-1]["content"]


async def test_mcp_tool_error_is_reported_back_to_llm(monkeypatch):
    """[3번 버그 재현] MCP 도구 실행 자체가 실패(검색 서버 에러 등)해도 500이 아니라, 실패 사실을 LLM에게 알리고 루프를 계속함."""
    async def failing_call_tool(name, arguments):
        raise RuntimeError("검색 서버 오류")

    monkeypatch.setattr(agent_loop.mcp_client, "get_cached_tools", lambda: [])
    monkeypatch.setattr(agent_loop.mcp_client, "call_tool", failing_call_tool)
    seen = _scripted_llm(monkeypatch, [
        _tool_call_response("search_documents", json.dumps({"query": "q"})),
        {"role": "assistant", "content": "답변", "tool_calls": None},
    ])

    result = await agent_loop.run_agent_loop("질문", history=[])

    assert result["answer_text"] == "답변"
    assert "실패" in seen[1][-1]["content"]
    assert result["retrieved_context"] == ""  # 실패한 도구 결과는 근거로 쓰지 않음


async def test_empty_answer_becomes_escalation(fake_mcp, monkeypatch):
    """[3번 버그 재현] LLM이 도구도 안 부르고 빈 답(None/"")을 내면, 수정 전엔 None이면 출력 가드레일에서 500,
    ""면 빈 말풍선이 나갔음. 이제는 연락처 안내(에스컬레이션) + 관리자 검토 필요로 처리."""
    _scripted_llm(monkeypatch, [{"role": "assistant", "content": "", "tool_calls": None}])

    result = await agent_loop.run_agent_loop("질문", history=[])

    assert result["answer_text"] == ESCALATION_CONTACT_MESSAGE
    assert result["needs_review"] is True
    assert result["review_reason"] == "insufficient_info"


# ----- 2차 리뷰 5번: LLM에 보내는 길이 상한 (vLLM --max-model-len 8192 대비, 2026-10-28) -----


async def test_long_search_results_are_truncated_before_sending(monkeypatch):
    """[5번 버그 재현] 검색 결과가 길어도 LLM에 보내는 도구 결과는 길이 상한 안으로 잘려야 함.
    수정 전엔 그대로 다 보내서, 검색 5건 × 최대 3번 + 이전 대화가 쌓이면 vLLM 길이 제한(8192)을 넘어
    400 → 503이 났음(같은 세션은 이전 대화가 24시간 남아서 계속 503)."""
    huge = [{"id": i, "chunk_text": "가" * 5000, "source": "faq.md", "similarity": 0.6} for i in range(5)]

    async def huge_call_tool(name, arguments):
        return huge

    monkeypatch.setattr(agent_loop.mcp_client, "get_cached_tools", lambda: [])
    monkeypatch.setattr(agent_loop.mcp_client, "call_tool", huge_call_tool)
    seen = _scripted_llm(monkeypatch, [
        _tool_call_response("search_documents", json.dumps({"query": "q"})),
        _tool_call_response("search_documents", json.dumps({"query": "q2"})),
        {"role": "assistant", "content": "답변", "tool_calls": None},
    ])

    await agent_loop.run_agent_loop("질문", history=[])

    tool_contents = [m["content"] for m in seen[-1] if m["role"] == "tool"]
    assert sum(len(c) for c in tool_contents) <= agent_loop.MAX_TOOL_RESULT_CHARS_TOTAL
    assert "…" in tool_contents[0]  # 잘렸다는 표시가 남아야 LLM이 "일부만 본 것"을 알 수 있음


async def test_long_history_keeps_only_most_recent_turns(fake_mcp, monkeypatch):
    """[5번] 이전 대화(숏텀메모리)도 길이 상한 안에서 "최근 것부터" 남겨야 함 — 오래된 턴부터 뺌."""
    history = []
    for i in range(10):
        history += [{"role": "user", "content": f"질문{i} " + "나" * 300},
                    {"role": "assistant", "content": f"답변{i} " + "다" * 300}]
    seen = _scripted_llm(monkeypatch, [{"role": "assistant", "content": "답변", "tool_calls": None}])

    await agent_loop.run_agent_loop("이번 질문", history=history)

    sent_history = seen[0][1:-1]  # [system, ...history..., 이번 질문]
    assert sum(len(m["content"]) for m in sent_history) <= agent_loop.MAX_HISTORY_CHARS
    assert sent_history[-1]["content"].startswith("답변9")  # 가장 최근 턴은 남아 있어야 함
    assert sent_history[0]["role"] == "user"               # 질문/답변 짝이 깨지지 않게 user부터 시작


# ----- 3차 리뷰 2·3·5번 (2026-10-28) -----


async def test_empty_search_result_is_reported_as_no_results(monkeypatch):
    """[3차 2번 버그 재현] 검색 결과가 0건이면 "관련 결과 없음"으로 알려야 함 — 수정 전엔 "길이 제한으로 생략됨"이라고
    잘못 알려서, LLM이 "정보는 있는데 못 봤다"고 오해했음(과거답변 검색은 초기에 거의 항상 0건)."""
    async def empty_call_tool(name, arguments):
        return []

    monkeypatch.setattr(agent_loop.mcp_client, "get_cached_tools", lambda: [])
    monkeypatch.setattr(agent_loop.mcp_client, "call_tool", empty_call_tool)
    seen = _scripted_llm(monkeypatch, [
        _tool_call_response("search_past_answers", json.dumps({"query": "q"})),
        {"role": "assistant", "content": "답변", "tool_calls": None},
    ])

    await agent_loop.run_agent_loop("질문", history=[])

    tool_content = seen[1][-1]["content"]
    assert "결과 없음" in tool_content
    assert "길이 제한" not in tool_content


async def test_second_search_is_not_starved_by_the_first(monkeypatch):
    """[3차 3번 버그 재현] 첫 검색이 길이 예산을 거의 다 써서 두 번째 검색(같은 턴의 과거답변 검색, 또는 다음 턴의
    재검색)이 문서 한 건도 못 받았음 — 여러 번 검색하며 판단하는 루프가 사실상 1회 검색이 됐음.
    이제 검색 1번마다 예산을 따로 줌: 두 번째 검색에도 실제 본문이 들어가야 함."""
    results = [{"id": i, "chunk_text": "가" * 700, "source": "faq.md", "similarity": 0.6} for i in range(5)]

    async def call_tool(name, arguments):
        return results

    monkeypatch.setattr(agent_loop.mcp_client, "get_cached_tools", lambda: [])
    monkeypatch.setattr(agent_loop.mcp_client, "call_tool", call_tool)
    seen = _scripted_llm(monkeypatch, [
        _tool_call_response("search_documents", json.dumps({"query": "q1"})),
        _tool_call_response("search_documents", json.dumps({"query": "q2"})),
        {"role": "assistant", "content": "답변", "tool_calls": None},
    ])

    await agent_loop.run_agent_loop("질문", history=[])

    tool_contents = [m["content"] for m in seen[-1] if m["role"] == "tool"]
    assert len(tool_contents) == 2
    assert all("가가가" in c for c in tool_contents)  # 두 번 다 실제 본문을 받음
    assert all(len(c) <= agent_loop.MAX_TOOL_RESULT_CHARS_PER_CALL for c in tool_contents)


async def test_long_latest_turn_is_truncated_not_dropped(fake_mcp, monkeypatch):
    """[3차 5번 버그 재현] 가장 최근 질문/답변 한 쌍이 혼자 상한을 넘으면, 수정 전엔 이전 대화가 통째로 사라져서
    긴 답변 바로 다음의 "그럼 그건요?" 같은 질문이 맥락을 잃었음. 최근 한 쌍은 잘라서라도 남겨야 함."""
    history = [{"role": "user", "content": "전형 일정 자세히 알려주세요"},
               {"role": "assistant", "content": "수시 1차는 " + "다" * 3000}]
    seen = _scripted_llm(monkeypatch, [{"role": "assistant", "content": "답변", "tool_calls": None}])

    result = await agent_loop.run_agent_loop("그럼 그건요?", history=history)

    sent_history = seen[0][1:-1]
    assert [m["role"] for m in sent_history] == ["user", "assistant"]
    assert sent_history[1]["content"].startswith("수시 1차는")
    assert sum(len(m["content"]) for m in sent_history) <= agent_loop.MAX_HISTORY_CHARS + 20  # 잘림 표시 몇 글자
    # 출력 가드레일은 LLM이 실제로 본(잘린) 대화로 검사해야 함 — 못 본 내용으로 통과시키지 않게
    assert result["history_used"] == sent_history
