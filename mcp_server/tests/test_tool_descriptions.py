"""
MCP 도구 설명 테스트 — LLM이 받는 도구 목록의 설명(description) 문구를 고정.

도구 설명은 단순 주석이 아니라 "LLM이 이 도구를 언제 쓸지 판단하는 근거"라서, 문구 하나가 동작을 바꿈.
FastMCP는 @mcp.tool() 함수의 docstring을 그대로 도구 설명으로 보냄 → 실제로 클라이언트가 받는 목록으로 확인.
"""

from fastmcp import Client

import mcp_server as server


async def _description(tool_name: str) -> str:
    """MCP 클라이언트로 도구 목록을 받아서 해당 도구의 설명을 꺼냄 — backend의 mcp_client가 받는 것과 같은 경로."""
    async with Client(server.mcp) as client:
        tools = await client.list_tools()
    return next(t.description for t in tools if t.name == tool_name)


async def test_search_documents_description_covers_school_life():
    """[2026-10-29 실측 B3 재현] 설명이 "학과 공식 문서에서 찾는다"뿐이라 실제 Qwen3가 "기숙사 신청은 어떻게 하나요?"를
    "학과 문서 범위 밖"으로 보고 검색을 안 했음(5번 중 0번) — 시스템 프롬프트에 "기숙사도 검색하라"를 넣어도 도구 설명을 따랐음.
    설명에 학교생활 범위를 적었더니 5번 중 5번 검색. 문서에 실제로 있는 주제(기숙사·학비·장학 등)가 설명에 있어야 함."""
    description = await _description("search_documents")

    for topic in ["입학", "학비", "장학", "기숙사", "교육과정", "취업"]:
        assert topic in description, topic
