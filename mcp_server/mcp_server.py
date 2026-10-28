"""
MCP 서버 — FastMCP로 search_documents/search_past_answers를 "도구(tool)"로 노출.

실제 배포 시에는 이 파일이 별도 Docker 컨테이너로 떠서, FastAPI 쪽의 mcp_client.py가
HTTP로 이 서버에 접속해 도구 목록을 받아오고(0번), 도구 호출을 중계(8번)한다.
지금은 로컬 개발 단계라 같은 노트북에서 포트만 분리해서 띄움.
"""

import os

from fastmcp import FastMCP

from search_documents import search_documents as _search_documents
from search_past_answers import search_past_answers as _search_past_answers

mcp = FastMCP("rag-search-server")


@mcp.tool()
async def search_documents(query: str) -> list[dict]:
    """학과 공식 문서에서 질문과 관련된 내용을 하이브리드(벡터+키워드) 검색으로 찾는다."""
    return await _search_documents(query)


@mcp.tool()
async def search_past_answers(query: str) -> list[dict]:
    """이미 답변된 과거 질문 중 이번 질문과 비슷한 것을 하이브리드 검색으로 찾는다."""
    return await _search_past_answers(query)


if __name__ == "__main__":
    # 로컬에서 직접 실행할 땐 127.0.0.1, 컨테이너 안에서는 0.0.0.0이어야
    # 같은 Docker 네트워크의 다른 컨테이너(FastAPI)가 접속할 수 있음.
    host = os.environ.get("MCP_SERVER_HOST", "127.0.0.1")
    mcp.run(transport="http", host=host, port=9000)
