"""
MCP 클라이언트 — 별도 파일이 아니라 "두 가지 역할을 가리키는 이름"이라고 설명했던 바로 그 부분.

역할 1 (connect_and_cache_tools): FastAPI 앱이 시작할 때 MCP 서버에 연결하고,
         사용 가능한 도구 목록을 받아서 캐싱. LLM은 매 호출마다 stateless라서
         이 목록을 매번 다시 실어 보내야 하는데, 서버 연결/조회를 매번 새로 하면 느리니까
         앱 시작 시 한 번만 하고 재사용.

역할 2 (call_tool): agent_loop가 "이 도구를 이 파라미터로 실행해줘"라고 하면,
         그 요청을 실제 네트워크 호출로 바꿔서 MCP 서버에 전달하고 결과를 받아옴.
         LLM은 네트워크 호출을 스스로 못 하기 때문에 이 중계가 반드시 필요함.
"""

import os

from fastmcp import Client

# 로컬에서 직접 실행할 땐 localhost, Docker Compose 안에서 실행될 땐 서비스명(mcp_server)으로 교체됨
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://127.0.0.1:9000/mcp")

_client: Client | None = None
_cached_tools: list[dict] | None = None


async def connect_and_cache_tools() -> list[dict]:
    """앱 시작 시 1회 호출. MCP 서버에 연결을 열어두고, 도구 목록을 OpenAI 호환 형식으로 캐싱."""
    global _client, _cached_tools

    _client = Client(MCP_SERVER_URL)
    await _client.__aenter__()  # 연결을 계속 유지하기 위해 수동으로 진입(앱 종료 시 close_connection에서 빠져나옴)

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


async def call_tool(name: str, arguments: dict) -> list[dict]:
    """agent_loop가 LLM의 tool_calls 요청을 그대로 넘기면, 실제로 MCP 서버에 호출을 전달."""
    if _client is None:
        raise RuntimeError("MCP 클라이언트가 아직 연결되지 않음 — connect_and_cache_tools()가 먼저 호출돼야 함")
    result = await _client.call_tool(name, arguments)
    return result.data


async def close_connection() -> None:
    """앱 종료 시 1회 호출. 연결을 정리."""
    if _client is not None:
        await _client.__aexit__(None, None, None)
