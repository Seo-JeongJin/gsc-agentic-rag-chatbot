"""
Agent 루프 — LLM이 매 턴마다 도구 호출 여부를 스스로 판단하고, 그 판단을 실제로
실행 가능하게 만드는 제어 로직(파싱·호출중계·반복·상한·재시도)을 전부 담당하는 핵심 엔진.

⚠️ 지금은 generate_llm_response()가 가짜 응답을 돌려줌 — GPU 서버가 교내망 전용이라
집에서는 진짜 vLLM을 호출할 수 없기 때문. 다음 주 학교에서 이 함수 내부만 실제 vLLM
HTTP 호출로 교체하면 되고, 그 아래의 루프 제어 로직(run_agent_loop)은 전혀 안 바뀜.
"""

import json

import mcp_client
from system_prompt import ESCALATION_CONTACT_MESSAGE, SYSTEM_PROMPT

MAX_LOOP_ITERATIONS = 4        # 2026-10-23 실측 기반으로 5회→4회로 조정한 상한 (11번 단계)
THINKING_OFF_UNTIL = 2         # 1~2번째 호출까지는 생각모드 끔, 3번째부터 켬 (방식 A, 2026-10-23 확정)
MAX_RETRY = 2                  # GPU 호출 실패 시 재시도 횟수 (12번 단계)


async def generate_llm_response(messages: list[dict], tools: list[dict], thinking: bool) -> dict:
    """
    ⚠️ 가짜 LLM 호출 — 나중에 이 함수 내부만 vLLM의 /v1/chat/completions HTTP 요청으로 교체.
    입력(messages+tools+thinking)과 출력(OpenAI 호환 message dict) 형식은 실제 구현과 동일하게
    맞춰둬서, 교체해도 run_agent_loop 쪽 코드는 손댈 필요 없음.

    가짜 판단 로직: 아직 "tool" 역할 메시지가 하나도 없으면 검색 도구를 호출하는 척하고,
                   이미 도구 결과가 있으면 그걸 바탕으로 답변을 만든 척함.
    """
    has_tool_result = any(m["role"] == "tool" for m in messages)

    if not has_tool_result:
        last_user_message = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "fake-call-1",
                    "type": "function",
                    "function": {
                        "name": "search_documents",
                        "arguments": json.dumps({"query": last_user_message}, ensure_ascii=False),
                    },
                }
            ],
        }

    return {
        "role": "assistant",
        "content": "(가짜 답변) 검색 결과를 바탕으로 한 답변입니다. 실제 vLLM 연결 전까지는 이 고정 텍스트가 나갑니다.",
        "tool_calls": None,
    }


async def _call_llm_with_retry(messages: list[dict], tools: list[dict], thinking: bool) -> dict:
    """GPU 호출 실패(실제로는 timeout/5xx) 시 최대 MAX_RETRY번 재시도.
    지금은 가짜 호출이라 실패할 일이 없지만, 구조는 실제 연결 때 그대로 쓰기 위해 미리 만들어둠."""
    last_error: Exception | None = None
    for _ in range(MAX_RETRY + 1):
        try:
            return await generate_llm_response(messages, tools, thinking)
        except Exception as e:  # noqa: BLE001 — 실제 구현에서는 httpx 관련 예외로 좁힐 것
            last_error = e
    raise last_error  # type: ignore[misc]


async def run_agent_loop(question_text: str, history: list[dict]) -> dict:
    """
    Agent 루프의 본체. 5~12번 단계를 수행.

    반환값: {"answer_text": str, "tool_call_count": int, "needs_review": bool, "review_reason": str|None}
    needs_review/review_reason은 questions 테이블 스키마와 그대로 맞춰둔 형태 — main.py가
    이 값을 그대로 save_log()에 넘기면 됨.
    """
    tools = mcp_client.get_cached_tools()  # 0번 단계에서 캐싱해둔 도구 목록

    # 5번: 시스템 프롬프트 + 4번에서 가져온 과거 대화 + 이번 질문을 합쳐 대화기록 구성
    messages: list[dict] = (
        [{"role": "system", "content": SYSTEM_PROMPT}]
        + history
        + [{"role": "user", "content": question_text}]
    )

    tool_call_count = 0
    # output_guardrail.py가 "답변이 실제로 검색결과에 근거했는지" 비교할 재료 — 루프 중
    # 실제로 받은 도구 호출 결과를 전부 모아둠(최종 답변이 나왔을 때만 의미있게 쓰임).
    retrieved_context_parts: list[str] = []

    for iteration in range(1, MAX_LOOP_ITERATIONS + 1):
        thinking = iteration > THINKING_OFF_UNTIL  # 3번째 호출부터 생각모드 켬

        # 6번/10번: LLM 호출 (비스트리밍 — tool_calls 파싱에 완성된 응답이 필요)
        response = await _call_llm_with_retry(messages, tools, thinking)

        # 7번: 응답 파싱 — tool_calls가 없으면 최종 답변으로 간주, 루프 종료
        if not response.get("tool_calls"):
            return {
                "answer_text": response["content"],
                "tool_call_count": tool_call_count,
                "needs_review": False,
                "review_reason": None,
                "retrieved_context": "\n".join(retrieved_context_parts),
            }

        # tool_calls가 있으면 — LLM의 요청 자체도 대화기록에 추가해둬야 다음 호출에서 맥락이 이어짐
        messages.append(response)

        for call in response["tool_calls"]:
            tool_call_count += 1
            arguments = json.loads(call["function"]["arguments"])

            # 8번: MCP 클라이언트를 통해 실제 도구 호출 전달 (LLM은 네트워크 호출을 스스로 못 함)
            result = await mcp_client.call_tool(call["function"]["name"], arguments)
            result_text = json.dumps(result, ensure_ascii=False)
            retrieved_context_parts.append(result_text)

            # 9번: MCP 응답을 LLM이 읽을 수 있는 텍스트로 변환해 대화기록에 추가
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": result_text,
                }
            )
        # 이후 for 루프가 다시 돌면서 6번(재호출)으로 돌아감

    # 11번: 상한(MAX_LOOP_ITERATIONS)까지 다 썼는데도 최종 답변을 못 받은 경우.
    # 이 문구는 LLM이 아니라 코드가 그대로 반환하는 고정 텍스트라, system_prompt.py의
    # [문의 안내] 지침과 별개로 여기서도 직접 연락처를 넣어둬야 확실히 보장됨
    # (이 경로는 LLM 호출 자체가 끝난 뒤라 프롬프트 지침이 적용될 데이터가 없음).
    return {
        "answer_text": ESCALATION_CONTACT_MESSAGE,
        "tool_call_count": tool_call_count,
        "needs_review": True,
        "review_reason": "insufficient_info",
        "retrieved_context": "\n".join(retrieved_context_parts),
    }
