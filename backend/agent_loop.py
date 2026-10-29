"""
Agent 루프 — LLM이 매 턴마다 도구 호출 여부를 스스로 판단하고, 그 판단을 실제로
실행 가능하게 만드는 제어 로직(파싱·호출중계·반복·상한·실패처리)을 전부 담당하는 핵심 엔진.

LLM 호출 자체(네트워크·재시도·생각모드·<think> 제거)는 llm_client.py가 담당(2026-10-28 리뷰 2번으로 분리).
LLM_MODE=mock이면 아래 _mock_llm_response가 가짜 응답을 만들고, vllm이면 실제 vLLM을 부름 —
이 파일의 루프 로직은 어느 모드든 똑같이 동작함.

LLM이 이상하게 응답해도(깨진 도구 인자, 없는 도구, 빈 답) 요청 전체가 500으로 죽지 않게,
코드가 실패를 "도구 결과" 형태로 LLM에게 다시 알려서 다음 턴에 스스로 고칠 기회를 줌(리뷰 3번).
"""

import json
import logging

import llm_client
import mcp_client
from system_prompt import ESCALATION_CONTACT_MESSAGE, FOLLOW_UP_SEARCH_REMINDER, SYSTEM_PROMPT

logger = logging.getLogger("uvicorn.error")

MAX_LOOP_ITERATIONS = 4        # 2026-10-23 실측 기반으로 5회→4회로 조정한 상한 (11번 단계)
THINKING_OFF_UNTIL = 2         # 1~2번째 호출까지는 생각모드 끔, 3번째부터 켬 (방식 A, 2026-10-23 확정)

# 검색 결과 중 출력 가드레일의 "근거"로 쓸 본문 필드 — 나머지(id, source, similarity)는 근거가 아니라 메타데이터
_GROUNDING_FIELDS = ("chunk_text", "question_text", "answer_text")

# LLM에 보내는 길이 상한(글자 수) — 2026-10-28 2차 리뷰 5번.
# vLLM이 --max-model-len 8192(토큰)로 떠 있어서, 시스템 프롬프트(약 2,250자) + 이전 대화 + 이번 질문(최대 1,000자)
# + 검색 결과가 이걸 넘으면 vLLM이 400으로 거절함 → 503. 이전 대화가 24시간 남아 있어서 같은 세션은 계속 503이 됐음.
# 토큰이 아니라 글자 수로 재는 이유: 정확한 토큰 수는 Qwen 토크나이저가 있어야 알 수 있어서, 한국어 1글자 ≈ 1토큰으로
# 넉넉하게(보수적으로) 잡음. 실제 사용량은 llm_client가 vLLM 응답의 prompt_tokens를 로그로 남기니 학교에서 보고 조정할 것.
MAX_HISTORY_CHARS = 1500              # 이전 대화 전체(최근 턴부터 채움, 가장 최근 한 쌍은 길어도 잘라서 남김)
MAX_TOOL_RESULT_CHARS_PER_CALL = 1500  # 검색 1번의 결과(JSON 포함) — 3차 리뷰 3번: 전엔 전체 예산을 첫 검색이 거의 다 써서
                                       # 두 번째 검색이 문서 한 건도 못 받았음 → 검색마다 따로 배정
MAX_TOOL_RESULT_CHARS_TOTAL = 4500     # 한 질문의 루프 동안 도구 결과 전체(검색 3번 × 1500)
MAX_ITEM_TEXT_CHARS = 700              # 검색 결과 한 건의 본문(문서 청크 90%가 710자 이하 — 2026-10-28 실측)
_TRUNCATION_MARK = "…(생략)"         # 잘렸다는 표시 — LLM이 "일부만 본 것"을 알 수 있게


async def _mock_llm_response(messages: list[dict], tools: list[dict] | None, thinking: bool) -> dict:
    """
    ⚠️ 가짜 LLM 응답 — LLM_MODE=mock(집 개발용)일 때만 llm_client가 이걸 씀.
    입력(messages+tools+thinking)과 출력(OpenAI 호환 message dict) 형식은 실제 vLLM과 동일.

    가짜 판단 로직: 아직 "tool" 역할 메시지가 하나도 없으면 검색 도구를 호출하는 척하고,
                   이미 도구 결과가 있으면 그 검색 결과 첫 항목의 본문을 인용해서 답변을 만든 척함.

    검색 결과를 인용하는 이유(2026-10-28): 출력 가드레일 근거에서 SYSTEM_PROMPT를 빼자(리뷰 4번) 예전 고정 문구
    "(가짜 답변) 검색 결과를 바탕으로…"는 근거와 겹치는 단어가 없어서 정확히 "근거 없는 답변"으로 걸러졌음 —
    집에서 테스트할 때 모든 답변이 에스컬레이션이 돼버리지 않도록, 실제 LLM처럼 검색 내용을 담은 답을 흉내 냄.
    """
    tool_messages = [m for m in messages if m["role"] == "tool"]

    if not tool_messages:
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

    try:
        lines = _grounding_text(json.loads(tool_messages[-1]["content"])).split("\n")
        # 청크 첫 줄은 "[섹션 경로]" 머리표(chunking.py가 붙임)라 건너뛰고 실제 본문 첫 줄을 인용
        quoted = next((line for line in lines if line.strip() and not line.startswith("[")), "")
    except (json.JSONDecodeError, TypeError):
        quoted = ""
    content = (
        f"(가짜 답변) 검색 결과에 따르면: {quoted}" if quoted
        else "(가짜 답변) 관련 내용을 찾지 못했어요."
    )
    return {"role": "assistant", "content": content, "tool_calls": None}


async def generate_llm_response(messages: list[dict], tools: list[dict], thinking: bool) -> dict:
    """LLM 한 번 호출(6번/10번 단계). 실제 호출·재시도·생각모드 처리는 llm_client.chat이 함.
    이 함수 이름을 그대로 둔 이유: 테스트가 이 함수를 바꿔치기해서 "LLM이 이렇게 답하면?"을 흉내 내기 때문."""
    return await llm_client.chat(messages, tools, thinking, mock=_mock_llm_response)


def _grounding_text(result) -> str:
    """도구 결과에서 출력 가드레일이 "근거"로 쓸 본문만 뽑음(리뷰 4번).
    전엔 결과 JSON 전체를 근거로 넣어서, 답변에 나온 숫자가 우연히 문서 id나 similarity 값(0.65 등)과 같기만
    해도 "근거 있음"으로 통과했음. 검색 결과 형식이 예상과 다르면(dict 리스트가 아니면) 문자열로 바꿔서 그대로 씀."""
    if isinstance(result, list) and all(isinstance(item, dict) for item in result):
        return "\n".join(
            item[field] for item in result for field in _GROUNDING_FIELDS if isinstance(item.get(field), str)
        )
    return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)


def _truncate(text: str, max_chars: int) -> str:
    """text가 max_chars보다 길면 앞부분만 남기고 잘렸다는 표시를 붙임."""
    return text if len(text) <= max_chars else text[:max_chars] + _TRUNCATION_MARK


def _trim_history(history: list[dict], max_chars: int = MAX_HISTORY_CHARS) -> list[dict]:
    """이전 대화를 길이 상한 안으로 줄임 — 가장 최근 질문/답변 쌍부터 거꾸로 채우고, 넘치면 그보다 오래된 건 뺌.
    짝(user→assistant) 단위로 자르는 이유: 질문만 남고 답이 빠지거나 그 반대면 LLM이 대화를 잘못 이해함.

    가장 최근 한 쌍이 혼자 상한을 넘으면 통째로 버리지 않고 잘라서 남김(3차 리뷰 5번) — 전엔 긴 답변 바로 다음의
    "그럼 그건요?" 같은 이어지는 질문이 이전 대화를 하나도 못 받아서 맥락을 잃었음. 질문 쪽은 짧게, 답변에 나머지를 줌."""
    pairs = [history[i:i + 2] for i in range(0, len(history), 2)]
    kept: list[list[dict]] = []
    used = 0
    for pair in reversed(pairs):
        size = sum(len(m.get("content") or "") for m in pair)
        if used + size > max_chars:
            if not kept:  # 가장 최근 한 쌍조차 안 들어감 → 잘라서라도 남김
                question_budget = min(len(pair[0].get("content") or ""), max_chars // 3)
                budgets = [question_budget, max_chars - question_budget][: len(pair)]
                kept.append([{**m, "content": _truncate(m.get("content") or "", b)} for m, b in zip(pair, budgets)])
            break
        kept.append(pair)
        used += size
    return [m for pair in reversed(kept) for m in pair]


def _fit_result_to_budget(result, remaining_chars: int):
    """검색 결과를 LLM에 보낼 크기로 줄임: 결과 한 건의 본문은 MAX_ITEM_TEXT_CHARS로 자르고, 그래도 남은 예산을
    넘으면 순위가 낮은(뒤쪽) 결과부터 뺌. 검색 결과 형식(dict 리스트)이 아니면 문자열로 바꿔서 앞부분만 남김."""
    if not (isinstance(result, list) and all(isinstance(item, dict) for item in result)):
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        return text[:max(remaining_chars, 0)]

    trimmed = []
    for item in result:
        item = dict(item)
        for field in _GROUNDING_FIELDS:
            value = item.get(field)
            if isinstance(value, str) and len(value) > MAX_ITEM_TEXT_CHARS:
                item[field] = value[:MAX_ITEM_TEXT_CHARS] + _TRUNCATION_MARK
        trimmed.append(item)
    # RRF 순위가 높은 앞쪽 결과를 남기기 위해, 예산 안에 들어올 때까지 뒤에서부터 뺌
    while trimmed and len(json.dumps(trimmed, ensure_ascii=False)) > remaining_chars:
        trimmed.pop()
    return trimmed


async def _run_tool_call(call: dict, allowed_tools: set[str], remaining_chars: int) -> tuple[str, str]:
    """도구 호출 하나를 실행(8~9번 단계). 반환: (LLM에게 보여줄 결과 텍스트, 출력 가드레일용 근거 텍스트).
    remaining_chars: 이번 질문에서 도구 결과로 더 보낼 수 있는 글자 수(MAX_TOOL_RESULT_CHARS_TOTAL에서 남은 것).
    근거 텍스트도 "LLM이 실제로 본(잘린) 내용"으로 만듦 — LLM이 못 본 내용으로 답을 검사하면 안 되니까.

    실패해도 예외를 던지지 않고 "실패했다"는 내용을 결과 텍스트로 돌려줌 — 이걸 LLM에게 보여주면 다음 턴에
    인자를 고치거나 다른 도구를 쓰는 등 스스로 판단할 수 있음(판단은 LLM, 실패 감지·전달은 코드).
    실패한 결과는 근거가 아니므로 근거 텍스트는 빈 문자열."""
    name = call.get("function", {}).get("name", "")
    raw_arguments = call.get("function", {}).get("arguments") or "{}"

    # 도구 목록이 비어 있으면(MCP 연결 직후 등) 이름 검사는 건너뜀 — MCP 서버가 최종적으로 판단
    if allowed_tools and name not in allowed_tools:
        return json.dumps({"error": f"'{name}' 도구는 없음. 사용 가능한 도구: {sorted(allowed_tools)}"}, ensure_ascii=False), ""

    try:
        arguments = json.loads(raw_arguments)
        if not isinstance(arguments, dict):
            raise ValueError("인자는 JSON 객체여야 함")
    except (json.JSONDecodeError, ValueError) as e:
        return json.dumps({"error": f"도구 인자를 해석할 수 없음({e}) — 올바른 JSON 객체로 다시 호출할 것"}, ensure_ascii=False), ""

    try:
        result = await mcp_client.call_tool(name, arguments)
    except Exception as e:  # noqa: BLE001 — 도구 실패가 요청 전체를 죽이면 안 됨
        logger.warning("도구 호출 실패(%s): %s", name, e)
        return json.dumps({"error": f"'{name}' 도구 실행 실패: {e}"}, ensure_ascii=False), ""

    # "검색했는데 0건"과 "결과는 있는데 길이 예산 때문에 못 보냄"은 LLM에게 다르게 알려야 함(3차 리뷰 2번) — 전엔 0건도
    # "길이 제한으로 생략됨"이라고 알려서 LLM이 "정보는 있는데 못 봤다"고 오해했음(과거답변 검색은 초기에 거의 항상 0건)
    if result in ([], None, ""):
        return json.dumps({"notice": "관련 검색 결과 없음 — 다른 검색어로 다시 찾거나, 정보가 없으면 문의처를 안내할 것"}, ensure_ascii=False), ""
    fitted = _fit_result_to_budget(result, remaining_chars)
    if not fitted:
        notice = "(검색 결과가 길이 제한을 넘어 생략됨 — 지금까지 받은 정보로 답하거나, 정보가 부족하면 문의처를 안내할 것)"
        return json.dumps({"notice": notice}, ensure_ascii=False), ""
    return json.dumps(fitted, ensure_ascii=False), _grounding_text(fitted)


def _escalation(tool_call_count: int, grounding_parts: list[str]) -> dict:
    """답변을 못 만든 경우의 반환값 — 연락처 안내 고정 문구 + 관리자 검토 필요(insufficient_info).
    이 문구는 LLM이 아니라 코드가 그대로 반환하는 고정 텍스트라, system_prompt.py의 [문의 안내] 지침과 별개로
    연락처가 확실히 보장됨(이 경로는 LLM 호출이 끝난 뒤라 프롬프트 지침이 적용될 데이터가 없음)."""
    return {
        "answer_text": ESCALATION_CONTACT_MESSAGE,
        "tool_call_count": tool_call_count,
        "needs_review": True,
        "review_reason": "insufficient_info",
        "retrieved_context": "\n".join(grounding_parts),
    }


async def run_agent_loop(question_text: str, history: list[dict], system_prompt: str | None = None) -> dict:
    """
    Agent 루프의 본체. 5~12번 단계를 수행.

    반환값: {"answer_text", "tool_call_count", "needs_review", "review_reason", "retrieved_context", "history_used"}
    needs_review/review_reason은 questions 테이블 스키마와 그대로 맞춰둔 형태 — main.py가
    이 값을 그대로 save_log()에 넘기면 됨. retrieved_context는 출력 가드레일용 근거(검색 결과 본문),
    history_used는 길이 상한으로 줄인 뒤 LLM에 "실제로 보낸" 이전 대화(3차 리뷰 5번). 2026-10-29부터 출력 검토는
    이전 대화를 근거로 쓰지 않아서(이번 턴 검색 결과만) main.py는 이 값을 안 씀 — 디버깅·테스트용으로 남겨둠.
    LLM 서버 자체가 실패하면(재시도까지 다 실패) llm_client.LLMError가 그대로 올라감 — main.py가 503으로 처리.
    system_prompt: 기본(None)은 SYSTEM_PROMPT. 개인 자격 판정 질문은 main.py가 ELIGIBILITY_PROMPT를 넘김(2026-10-29 B9 —
    question_router.py 참고). 기본값을 함수 정의가 아니라 실행 시점에 정하는 이유: 실험·테스트에서 모듈의 SYSTEM_PROMPT를
    바꿔 끼웠을 때 그 값이 그대로 쓰이게.
    """
    tools = mcp_client.get_cached_tools()  # 0번 단계에서 캐싱해둔 도구 목록
    allowed_tools = {t["function"]["name"] for t in tools if "function" in t}

    # 5번: 시스템 프롬프트 + 4번에서 가져온 과거 대화(길이 상한 안으로 줄여서) + 이번 질문을 합쳐 대화기록 구성
    messages: list[dict] = (
        [{"role": "system", "content": system_prompt or SYSTEM_PROMPT}]
        + (history_used := _trim_history(history))
        # 이전 대화가 있으면 질문 뒤에 재검색 알림을 붙임(2026-10-29 실측 B11 — system_prompt.FOLLOW_UP_SEARCH_REMINDER 참고).
        # LLM에 보내는 메시지에만 붙고, main.py가 DB 저장·출력 검토에 쓰는 질문은 원래 question_text 그대로.
        + [{"role": "user", "content": question_text + (FOLLOW_UP_SEARCH_REMINDER if history_used else "")}]
    )

    tool_call_count = 0
    grounding_parts: list[str] = []  # 성공한 도구 호출의 근거 텍스트만 모음
    tool_chars_left = MAX_TOOL_RESULT_CHARS_TOTAL  # 이번 질문에서 도구 결과로 더 보낼 수 있는 글자 수

    for iteration in range(1, MAX_LOOP_ITERATIONS + 1):
        thinking = iteration > THINKING_OFF_UNTIL  # 3번째 호출부터 생각모드 켬

        # 6번/10번: LLM 호출 (비스트리밍 — tool_calls 파싱에 완성된 응답이 필요)
        response = await generate_llm_response(messages, tools, thinking)

        # 7번: 응답 파싱 — tool_calls가 없으면 최종 답변
        if not response.get("tool_calls"):
            answer = (response.get("content") or "").strip()
            if not answer:
                # 도구도 안 부르고 답도 비어 있음 — 빈 말풍선을 내보내지 않고 연락처 안내로(리뷰 3번)
                logger.warning("LLM이 빈 답변을 반환함 — 에스컬레이션 처리")
                return {**_escalation(tool_call_count, grounding_parts), "history_used": history_used}
            return {
                "answer_text": answer,
                "tool_call_count": tool_call_count,
                "needs_review": False,
                "review_reason": None,
                "retrieved_context": "\n".join(grounding_parts),
                "history_used": history_used,
            }

        # 11번: 마지막 턴인데 또 도구를 요청 — 실행해도 그 결과를 볼 LLM 호출이 더 없으므로 실행하지 않고 종료
        # (2026-10-28 결정 — 전엔 실행해서 검색 비용만 쓰고 tool_call_count만 부풀었음)
        if iteration == MAX_LOOP_ITERATIONS:
            break

        # tool_calls가 있으면 — LLM의 요청 자체도 대화기록에 추가해둬야 다음 호출에서 맥락이 이어짐
        messages.append(response)

        for call in response["tool_calls"]:
            tool_call_count += 1
            # 8번+9번: MCP로 실제 실행 → 결과(또는 실패 내용)를 LLM이 읽을 수 있는 텍스트로 대화기록에 추가
            # 검색 1번에 줄 수 있는 양 = 1회 상한과 남은 전체 예산 중 작은 쪽(앞 검색이 뒤 검색 몫까지 다 쓰지 않게)
            call_budget = min(MAX_TOOL_RESULT_CHARS_PER_CALL, tool_chars_left)
            result_text, grounding = await _run_tool_call(call, allowed_tools, call_budget)
            tool_chars_left -= len(result_text)
            if grounding:
                grounding_parts.append(grounding)
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result_text})
        # 이후 for 루프가 다시 돌면서 6번(재호출)으로 돌아감

    # 11번: 상한까지 다 썼는데도 최종 답변을 못 받은 경우
    return {**_escalation(tool_call_count, grounding_parts), "history_used": history_used}
