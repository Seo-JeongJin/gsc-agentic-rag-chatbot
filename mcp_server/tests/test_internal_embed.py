"""
내부 전용 임베딩 API(mcp_server.py의 POST /internal/embed) 테스트 — 리뷰 1번.

backend가 "정상 답변된 질문"을 과거답변 검색 대상으로 저장할 때, 질문 임베딩을 받으려고 부르는 API.
임베딩 코드는 이 서버(embedding.py) 한 곳에만 두기 위해 backend에 모델을 따로 안 올리고 여기를 호출함.
"""

from fastmcp import Client
from starlette.testclient import TestClient

import mcp_server as server


def test_embed_returns_1024_dim_vector(monkeypatch):
    """텍스트를 보내면 1024차원 벡터를 JSON으로 돌려줘야 함(BGE-M3 차원 = questions.embedding 컬럼 차원).
    진짜 모델 대신 가짜 embed로 바꿔서 API 형식만 확인."""
    monkeypatch.setattr(server, "embed", lambda text: [0.5] * 1024)
    client = TestClient(server.mcp.http_app())

    res = client.post("/internal/embed", json={"text": "기숙사 신청 방법"})

    assert res.status_code == 200
    assert len(res.json()["embedding"]) == 1024


def test_embed_rejects_missing_or_blank_text(monkeypatch):
    """text가 없거나 공백뿐이면 400 — 빈 문자열 임베딩을 저장하면 모든 질문과 애매하게 비슷한 쓰레기 벡터가 됨."""
    monkeypatch.setattr(server, "embed", lambda text: [0.5] * 1024)
    client = TestClient(server.mcp.http_app())

    assert client.post("/internal/embed", json={}).status_code == 400
    assert client.post("/internal/embed", json={"text": "   "}).status_code == 400


async def test_embed_api_is_not_exposed_as_mcp_tool():
    """/internal/embed는 일반 HTTP 엔드포인트라 LLM이 받는 도구 목록에는 나타나면 안 됨
    — 도구로 만들면 LLM이 "embed"를 검색 도구처럼 호출해버릴 수 있음. 도구는 검색 2개뿐이어야 함."""
    async with Client(server.mcp) as client:
        tools = await client.list_tools()

    assert {t.name for t in tools} == {"search_documents", "search_past_answers"}


def test_embed_rejects_non_json_body(monkeypatch):
    """JSON이 아닌 요청이 오면 서버 오류(500)가 아니라 요청 오류(400)여야 함(커밋 전 자체 검토에서 발견)."""
    monkeypatch.setattr(server, "embed", lambda text: [0.5] * 1024)
    client = TestClient(server.mcp.http_app())

    res = client.post("/internal/embed", content=b"not json", headers={"Content-Type": "application/json"})

    assert res.status_code == 400
