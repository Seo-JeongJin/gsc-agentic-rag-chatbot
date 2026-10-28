"""
MCP 서버 — FastMCP로 search_documents/search_past_answers를 "도구(tool)"로 노출.

실제 배포 시에는 이 파일이 별도 Docker 컨테이너로 떠서, FastAPI 쪽의 mcp_client.py가
HTTP로 이 서버에 접속해 도구 목록을 받아오고(0번), 도구 호출을 중계(8번)한다.
지금은 로컬 개발 단계라 같은 노트북에서 포트만 분리해서 띄움.
"""

import os

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from embedding import EmbeddingError, embed
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


@mcp.custom_route("/internal/embed", methods=["POST"])
async def internal_embed(request: Request) -> JSONResponse:
    """내부 전용 임베딩 API — backend가 "정상 답변된 질문"을 과거답변 검색 대상으로 저장할 때 호출(2026-10-28, 리뷰 1번).
    요청: {"text": "..."} → 응답: {"embedding": [1024개 숫자]}

    @mcp.tool()이 아니라 일반 HTTP 엔드포인트(custom_route)로 만든 이유: 도구로 만들면 LLM이 받는 도구 목록에
    "embed"가 끼어서 LLM이 검색 도구처럼 호출할 수 있음. 이건 LLM이 판단해서 쓰는 기능이 아니라 코드끼리 쓰는 기능.
    임베딩 코드를 이 서버(embedding.py) 한 곳에만 두기 위해 backend에 모델을 따로 올리지 않고 여기를 부르게 함.

    ⚠️ 인증 없음 — 같은 Docker 네트워크의 backend만 부른다는 전제. 서비스 공개 전에 9000번 포트를
    외부에 열지 않도록 막아야 함(리뷰 6단계 항목)."""
    try:
        body = await request.json()
    except ValueError:  # JSON이 아닌 요청 — 서버 오류(500)가 아니라 요청 오류(400)로 응답
        body = None
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str) or not text.strip():
        return JSONResponse({"error": "text(비어있지 않은 문자열)가 필요함"}, status_code=400)

    # embed()는 async라 계산(local: 별도 스레드 / vllm: 비동기 HTTP)하는 동안에도 서버가 다른 요청을 처리함
    try:
        vector = await embed(text)
    except EmbeddingError as e:
        # 임베딩 서버(vLLM) 문제 — 호출한 backend가 로그만 남기고 넘어감(질문이 과거답변 검색에서 빠질 뿐)
        return JSONResponse({"error": str(e)}, status_code=502)
    return JSONResponse({"embedding": vector})


if __name__ == "__main__":
    # 로컬에서 직접 실행할 땐 127.0.0.1, 컨테이너 안에서는 0.0.0.0이어야
    # 같은 Docker 네트워크의 다른 컨테이너(FastAPI)가 접속할 수 있음.
    host = os.environ.get("MCP_SERVER_HOST", "127.0.0.1")
    mcp.run(transport="http", host=host, port=9000)
