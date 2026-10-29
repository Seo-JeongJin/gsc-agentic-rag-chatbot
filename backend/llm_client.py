"""
LLM 호출 입구 — 생성 LLM(vLLM의 Qwen3)을 부르는 코드가 4곳(Agent 루프, 카테고리 분류, 로그 요약,
카테고리 제안)에 흩어져 있던 걸 이 파일 하나로 모음(2026-10-28 리뷰 2번).

왜 한 곳으로 모으나: 실제 vLLM을 붙일 때 4곳이 각자 주소·모델명·타임아웃·재시도·생각모드 처리를 따로
구현하면, 하나만 빠뜨려도(예: 단발성 호출에서 생각모드를 안 끔) 조용히 깨짐. 여기서 한 번만 제대로 처리.

LLM_MODE 환경변수로 동작을 바꿈:
- mock(기본값): 네트워크 없이, 호출하는 쪽이 넘긴 가짜 응답 함수를 씀 — 집에서 개발/테스트용
- vllm: 실제 vLLM의 OpenAI 호환 API(POST {VLLM_URL}/v1/chat/completions) 호출 — 학교 실측/운영용
학교에서는 .env의 LLM_MODE/VLLM_URL/VLLM_MODEL만 바꾸면 되고 코드는 안 바뀜.

"판단은 LLM, 실행/제약은 코드" 원칙에서 이 파일은 "코드" 쪽: 네트워크 호출, 재시도, 생각모드 on/off,
응답에서 생각 과정 제거 — 전부 LLM이 스스로 못 하는 일.
"""

import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable

import httpx

logger = logging.getLogger("uvicorn.error")

DEFAULT_VLLM_MODEL = "Qwen/Qwen3-14B-AWQ"  # 실제 이름은 학교에서 GET {VLLM_URL}/v1/models로 확인 후 .env에 넣을 것
LLM_TIMEOUT_SECONDS = 60.0                 # 생각모드 켠 호출은 몇 초 걸림(실측 1.5~3.3초) — 넉넉하게
MAX_RETRY = 2                              # GPU 호출 실패(5xx/타임아웃/연결실패) 시 재시도 횟수 (스펙 12번)
RETRY_BACKOFF_SECONDS = (1.0, 2.0)         # 재시도 전 대기 시간 — 서버가 잠깐 바쁜 경우 바로 다시 때리지 않게

# 생각 과정 블록. DOTALL: 생각 내용에 줄바꿈이 있어도 한 블록으로 매칭
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)

# 호출하는 쪽이 넘기는 가짜 응답 함수 형태
ChatMock = Callable[[list[dict], list[dict] | None, bool], Awaitable[dict]]
CompleteMock = Callable[[str], Awaitable[str]]


class LLMError(Exception):
    """LLM 호출이 최종적으로 실패함(재시도 다 실패, 4xx, 응답 형식 이상, 설정 누락).
    호출하는 쪽이 이걸 잡아서 사용자/관리자에게 적절한 응답(503/502)으로 바꿈."""


class _RetryableError(Exception):
    """내부용 — "다시 보내면 될 수도 있는" 실패(5xx). 재시도 루프에서만 씀."""


def _mode() -> str:
    """매 호출마다 환경변수를 읽음(모듈 로딩 시 고정하지 않음) — 테스트에서 모드를 바꿔가며 확인할 수 있게."""
    mode = os.environ.get("LLM_MODE", "mock").strip().lower()
    if mode not in ("mock", "vllm"):
        raise LLMError(f"LLM_MODE는 mock 또는 vllm이어야 함(현재: {mode!r}) — .env 확인")
    return mode


def strip_thinking(text: str | None) -> str | None:
    """응답에서 Qwen3의 생각 과정(<think>…</think>)을 지우고 실제 답만 남김.

    vLLM을 --reasoning-parser 없이 띄우면 생각모드 켠 호출의 content에 생각 과정이 그대로 섞여 옴(스펙 12절
    기동 옵션에 이 플래그가 없음). 안 지우면 ① 사용자에게 생각 과정이 보이고 ② 카테고리 파서가 "</think>"의
    ">"에서 잘려서 전부 기타로 분류됨(리뷰 2번). 채팅 템플릿이 <think>를 프롬프트 쪽에 넣으면 응답엔 닫는 태그만
    올 수도 있어서 그 경우도 처리."""
    if text is None:
        return None
    text = _THINK_BLOCK.sub("", text)
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return text.strip()


def _build_payload(messages: list[dict], tools: list[dict] | None, thinking: bool) -> dict:
    """vLLM(OpenAI 호환) 요청 body 조립.
    생각모드: 끌 때만 chat_template_kwargs로 명시, 켤 때는 키 자체를 빼서 모델 기본값(켜짐) 적용 — 스펙 3절 6/10번 방식
    (서버 재시작 없이 요청마다 바꿀 수 있음, 2026-10-23 curl로 확인됨)."""
    payload: dict = {
        "model": os.environ.get("VLLM_MODEL") or DEFAULT_VLLM_MODEL,
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"  # 도구를 쓸지 말지는 LLM이 판단(Agentic RAG의 핵심)
    if not thinking:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    return payload


async def _post_chat_completions(payload: dict) -> dict:
    """vLLM에 요청을 보내고 JSON 응답을 반환. 일시적 실패(5xx/타임아웃/연결 실패)는 MAX_RETRY번까지 재시도,
    4xx(요청이 잘못됨 — 모델 이름 오타 등)는 다시 보내도 같으므로 바로 LLMError."""
    base_url = (os.environ.get("VLLM_URL") or "").rstrip("/")
    if not base_url:
        raise LLMError("LLM_MODE=vllm인데 VLLM_URL이 비어 있음 — .env에 GPU 서버 주소(예: http://<GPU서버>:8000)를 넣을 것")
    url = f"{base_url}/v1/chat/completions"

    last_error: Exception | None = None
    for attempt in range(MAX_RETRY + 1):
        try:
            async with httpx.AsyncClient(timeout=LLM_TIMEOUT_SECONDS) as client:
                res = await client.post(url, json=payload)
            if res.status_code >= 500:
                raise _RetryableError(f"vLLM 서버 오류 {res.status_code}: {res.text[:200]}")
            if res.status_code >= 400:
                raise LLMError(f"vLLM 요청 거절 {res.status_code}: {res.text[:300]}")
            data = res.json()
            # 실제 토큰 사용량을 로그로 남김 — 길이 상한(agent_loop.MAX_*_CHARS 등)을 글자 수로 어림잡아 둬서,
            # 학교에서 실제 prompt_tokens가 8192(--max-model-len)에 얼마나 가까운지 보고 조정하기 위함
            usage = data.get("usage") or {}
            if usage:
                logger.info("LLM 사용량: prompt_tokens=%s, completion_tokens=%s",
                            usage.get("prompt_tokens"), usage.get("completion_tokens"))
            return data
        except (_RetryableError, httpx.TimeoutException, httpx.TransportError) as e:
            last_error = e
            logger.warning("LLM 호출 실패(%d/%d): %s", attempt + 1, MAX_RETRY + 1, e)
            if attempt < MAX_RETRY:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)])
    raise LLMError(f"LLM 호출 {MAX_RETRY + 1}번 모두 실패: {last_error}") from last_error


async def _vllm_chat(messages: list[dict], tools: list[dict] | None, thinking: bool) -> dict:
    """실제 vLLM 호출 → 응답에서 message 하나를 꺼냄."""
    data = await _post_chat_completions(_build_payload(messages, tools, thinking))
    try:
        return data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"vLLM 응답 형식이 예상과 다름: {str(data)[:300]}") from e


async def chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    thinking: bool = False,
    *,
    mock: ChatMock,
) -> dict:
    """Agent 루프용 — 대화기록+도구목록을 보내고 OpenAI 호환 assistant 메시지 dict를 돌려줌.
    반환: {"role": "assistant", "content": 생각과정 제거된 답 또는 None, "tool_calls": 리스트 또는 None}
    mock: LLM_MODE=mock일 때 쓸 가짜 응답 함수(호출하는 쪽이 자기 상황에 맞는 가짜를 넘김)."""
    if _mode() == "mock":
        message = await mock(messages, tools, thinking)
    else:
        message = await _vllm_chat(messages, tools, thinking)
    return {
        "role": "assistant",
        "content": strip_thinking(message.get("content")),
        "tool_calls": message.get("tool_calls") or None,
    }


async def complete(prompt: str, *, mock: CompleteMock, thinking: bool = False) -> str:
    """단발성 호출용(카테고리 분류/로그 요약/카테고리 제안/질문 분류/출력 검토) — 프롬프트 하나 보내고 텍스트 하나 받음.
    도구 없음, **생각모드는 기본 끔**(정해진 형식으로 짧게 답해야 하는 호출이라 생각모드는 느리기만 하고 파싱 위험만 큼).
    예외: 출력 검토만 thinking=True(2026-10-29 실측 — 끄면 지어낸 답을 0/20 잡고, 켜면 오탐 없이 편입 단정 5/5). 생각 과정은
    아래 strip_thinking으로 지우므로 파서는 끈 경우와 똑같이 판정 부분만 받음.
    빈 응답이면 LLMError — 빈 문자열을 정상 답처럼 넘기면 파서가 "형식 깨짐"과 구분을 못 함."""
    if _mode() == "mock":
        return strip_thinking(await mock(prompt)) or ""
    message = await _vllm_chat([{"role": "user", "content": prompt}], tools=None, thinking=thinking)
    content = strip_thinking(message.get("content"))
    if not content:
        raise LLMError("LLM이 빈 응답을 돌려줌")
    return content
