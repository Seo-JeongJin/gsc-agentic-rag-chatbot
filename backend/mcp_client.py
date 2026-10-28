"""
MCP 클라이언트 — 별도 파일이 아니라 "두 가지 역할을 가리키는 이름"이라고 설명했던 바로 그 부분.

역할 1 (connect_and_cache_tools): FastAPI 앱이 시작할 때 MCP 서버에 연결하고,
         사용 가능한 도구 목록을 받아서 캐싱. LLM은 매 호출마다 stateless라서
         이 목록을 매번 다시 실어 보내야 하는데, 서버 연결/조회를 매번 새로 하면 느리니까
         앱 시작 시 한 번만 하고 재사용.

역할 2 (call_tool): agent_loop가 "이 도구를 이 파라미터로 실행해줘"라고 하면,
         그 요청을 실제 네트워크 호출로 바꿔서 MCP 서버에 전달하고 결과를 받아옴.
         LLM은 네트워크 호출을 스스로 못 하기 때문에 이 중계가 반드시 필요함.

역할 3 (embed_text, 2026-10-28 추가): MCP 서버의 "내부 전용" 임베딩 API(/internal/embed)를 호출.
         이건 LLM이 쓰는 도구가 아니라 코드끼리 쓰는 기능이라 MCP 프로토콜이 아닌 일반 HTTP로 부름
         (main.py가 정상 답변된 질문을 과거답변 검색 대상으로 저장할 때 사용).

연결이 끊기거나 늦게 뜨는 경우 대비 (2026-10-28 리뷰 15번):
- 앱 시작 시: MCP 서버가 아직 준비 안 됐으면 바로 죽지 않고 몇 번 재시도함
  (docker compose로 같이 띄우면 backend가 MCP보다 먼저 떠서 연결 실패로 종료되는 문제가 실제로 반복됐음)
- 실행 중: 호출이 연결 오류로 실패하면 한 번 다시 연결해서 같은 호출을 재시도하고, 재연결마저 실패했으면
  다음 호출 때 다시 연결을 시도함. (참고: 2026-10-28 실측에서 MCP 서버만 재시작해 봤을 땐 fastmcp가 스스로
  복구해서 이 경로까지 오지 않았음 — 리뷰 때 "재시작 전까지 전부 실패"라고 한 건 틀렸던 것. 안전장치로 유지.)
"""

import asyncio
import logging
import os
from urllib.parse import urlsplit

import httpx
from fastmcp import Client
from fastmcp.exceptions import ToolError

# 로컬에서 직접 실행할 땐 localhost, Docker Compose 안에서 실행될 땐 서비스명(mcp_server)으로 교체됨
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://127.0.0.1:9000/mcp")

# 앱 시작 시 연결 재시도 — 최대 10번 × 2초 = 약 20초까지 MCP 서버가 뜨길 기다림.
# 그래도 안 되면 에러로 멈춤(주소 오타 같은 설정 오류를 무한정 기다리며 숨기지 않기 위함).
MCP_CONNECT_RETRIES = 10
MCP_CONNECT_RETRY_DELAY_SECONDS = 2.0

# 내부 임베딩 API 대기 시간 — 로컬 CPU 임베딩은 MCP 서버가 뜬 뒤 "첫 호출" 때 BGE-M3 모델을 로딩해서
# 수십 초가 걸릴 수 있음(이후 호출은 빠름). 백그라운드 작업이라 사용자 응답 속도와는 무관해서 넉넉하게 잡음.
EMBED_TIMEOUT_SECONDS = 120.0

logger = logging.getLogger("uvicorn.error")

_client: Client | None = None
_cached_tools: list[dict] | None = None


async def _open_client() -> Client:
    """MCP 서버에 새 연결을 열어서 반환. async with 대신 __aenter__를 수동으로 호출하는 이유:
    요청 하나가 끝나도 연결을 닫지 않고 앱이 켜져 있는 동안 계속 유지하기 위함
    (앱 종료 시 close_connection에서 __aexit__로 직접 닫음)."""
    client = Client(MCP_SERVER_URL)
    await client.__aenter__()
    return client


async def connect_and_cache_tools() -> list[dict]:
    """앱 시작 시 1회 호출. MCP 서버에 연결을 열어두고, 도구 목록을 OpenAI 호환 형식으로 캐싱.
    연결이 실패하면 MCP_CONNECT_RETRY_DELAY_SECONDS 간격으로 MCP_CONNECT_RETRIES번까지 재시도."""
    global _client, _cached_tools

    last_error: Exception | None = None
    for attempt in range(1, MCP_CONNECT_RETRIES + 1):
        try:
            _client = await _open_client()
            break
        except Exception as e:  # noqa: BLE001 — 연결 단계의 어떤 실패든 "아직 준비 안 됨"으로 보고 재시도
            last_error = e
            logger.warning("MCP 서버 연결 실패(%d/%d), 재시도 예정: %s", attempt, MCP_CONNECT_RETRIES, e)
            if attempt < MCP_CONNECT_RETRIES:
                await asyncio.sleep(MCP_CONNECT_RETRY_DELAY_SECONDS)
    else:
        # for문이 break 없이 끝남 = 모든 시도 실패
        raise RuntimeError(f"MCP 서버에 연결할 수 없음({MCP_SERVER_URL}) — {MCP_CONNECT_RETRIES}번 시도 실패") from last_error

    tools = await _client.list_tools()

    # vLLM에 보낼 때 기대하는 OpenAI 호환 tools 형식으로 변환해서 캐싱
    _cached_tools = [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.input_schema,
            },
        }
        for tool in tools
    ]
    return _cached_tools


def get_cached_tools() -> list[dict]:
    """캐싱된 도구 목록 반환 — agent_loop가 매 LLM 호출마다 이걸 가져다 씀."""
    if _cached_tools is None:
        raise RuntimeError("MCP 도구 목록이 아직 캐싱되지 않음 — connect_and_cache_tools()가 먼저 호출돼야 함")
    return _cached_tools


async def _reconnect() -> None:
    """끊긴 연결을 정리하고 새로 연결. 옛 연결을 닫다가 에러가 나도(이미 끊겨 있으면 흔함) 무시."""
    global _client
    old_client, _client = _client, None
    if old_client is not None:
        try:
            await old_client.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001 — 이미 끊긴 연결 정리 실패는 의미 없음
            pass
    _client = await _open_client()


async def call_tool(name: str, arguments: dict) -> list[dict]:
    """agent_loop가 LLM의 tool_calls 요청을 그대로 넘기면, 실제로 MCP 서버에 호출을 전달.

    실패 처리를 두 가지로 구분:
    - ToolError: 연결은 멀쩡하고 "도구 실행 자체"가 실패한 것(잘못된 인자 등) → 재연결해도 같은 결과라 그대로 올려보냄
    - 그 외 예외: 연결이 끊긴 것으로 보고 한 번만 재연결 후 재시도(두 번째도 실패하면 그 에러를 올려보냄)
    """
    if _client is None:
        # 직전 재연결이 실패해서 연결이 비어 있는 상태 — 여기서 다시 연결을 시도함(2026-10-28 2차 리뷰 4번).
        # 전엔 바로 에러만 내서, 재연결이 한 번 실패하면 backend를 재시작할 때까지 모든 검색이 실패했음.
        # (앱 시작 전, 즉 connect_and_cache_tools가 아예 안 불린 경우엔 도구 목록도 없어서 애초에 여기 올 일이 없음)
        await _reconnect()
    try:
        result = await _client.call_tool(name, arguments)
    except ToolError:
        raise
    except Exception as e:  # noqa: BLE001 — 연결 문제로 보고 1회 재연결
        logger.warning("MCP 도구 호출 실패, 재연결 후 1회 재시도: %s", e)
        await _reconnect()
        result = await _client.call_tool(name, arguments)
    return result.data


def _internal_api_url(path: str) -> str:
    """MCP 서버 주소(예: http://mcp_server:9000/mcp)에서 같은 서버의 내부 API 주소를 만듦
    (예: http://mcp_server:9000/internal/embed). 주소 설정을 MCP_SERVER_URL 하나로만 관리하기 위함."""
    parts = urlsplit(MCP_SERVER_URL)
    return f"{parts.scheme}://{parts.netloc}{path}"


async def embed_text(text: str) -> list[float]:
    """MCP 서버의 내부 임베딩 API로 텍스트 임베딩(1024차원)을 받아옴.
    실패하면(서버 다운, 타임아웃, 4xx/5xx) 예외를 그대로 올려보냄 — 호출하는 main.py가 백그라운드에서
    잡아서 로그만 남김(임베딩 저장 실패는 "그 질문이 과거답변 검색에 안 걸린다"는 것뿐, 답변엔 영향 없음)."""
    async with httpx.AsyncClient(timeout=EMBED_TIMEOUT_SECONDS) as client:
        res = await client.post(_internal_api_url("/internal/embed"), json={"text": text})
        res.raise_for_status()
        return res.json()["embedding"]


async def close_connection() -> None:
    """앱 종료 시 1회 호출. 연결을 정리."""
    if _client is not None:
        await _client.__aexit__(None, None, None)
