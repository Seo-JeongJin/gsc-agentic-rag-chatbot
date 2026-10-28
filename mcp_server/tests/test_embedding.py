"""
임베딩 래퍼(embedding.py) 테스트 — EMBEDDING_MODE 스위치(local/vllm), 리뷰 18번.

vllm 모드는 실제 GPU 서버 대신 httpx MockTransport(가짜 네트워크)로 "vLLM 임베딩 API가 이렇게 응답하면?"을 흉내 냄.
"""

import json

import httpx
import pytest

import embedding

REAL_ASYNC_CLIENT = httpx.AsyncClient


def _install_fake_vllm(monkeypatch, handler):
    """embedding.py가 만드는 httpx.AsyncClient를 가짜 네트워크용으로 바꿔치기하고 vllm 모드로 설정. 보낸 요청은 sent에 기록."""
    sent = []

    def recording_handler(request):
        sent.append(request)
        return handler(request)

    transport = httpx.MockTransport(recording_handler)
    monkeypatch.setattr(embedding.httpx, "AsyncClient", lambda **kw: REAL_ASYNC_CLIENT(transport=transport, **kw))
    monkeypatch.setenv("EMBEDDING_MODE", "vllm")
    monkeypatch.setenv("EMBEDDING_URL", "http://gpu-server:8001")
    monkeypatch.setenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    monkeypatch.setattr(embedding, "RETRY_BACKOFF_SECONDS", (0, 0))
    return sent


def _embedding_response(vector, status=200):
    """vLLM(OpenAI 호환) /v1/embeddings 응답 형태."""
    return httpx.Response(status, json={"data": [{"embedding": vector}]})


async def test_vllm_mode_calls_embeddings_api(monkeypatch):
    """vllm 모드면 GPU 서버의 /v1/embeddings에 model+input을 보내고, 응답의 data[0].embedding을 그대로 돌려줘야 함."""
    sent = _install_fake_vllm(monkeypatch, lambda req: _embedding_response([0.1] * 1024))

    vector = await embedding.embed("기숙사 신청 방법")

    assert len(vector) == 1024
    assert str(sent[0].url) == "http://gpu-server:8001/v1/embeddings"
    assert json.loads(sent[0].content) == {"model": "BAAI/bge-m3", "input": "기숙사 신청 방법"}


async def test_vllm_wrong_dimension_is_rejected(monkeypatch):
    """1024차원이 아닌 벡터가 오면(다른 모델을 띄워 놓은 경우 등) 에러 — DB의 vector(1024) 컬럼에 넣다가 터지거나,
    엉뚱한 모델 벡터로 검색 품질이 조용히 망가지는 걸 미리 막음."""
    _install_fake_vllm(monkeypatch, lambda req: _embedding_response([0.1] * 768))

    with pytest.raises(embedding.EmbeddingError, match="1024"):
        await embedding.embed("q")


async def test_vllm_retries_on_5xx(monkeypatch):
    """GPU 서버 일시 오류(5xx)는 재시도 — 2번 실패 후 3번째 성공이면 정상."""
    responses = iter([httpx.Response(503), httpx.Response(502), _embedding_response([0.1] * 1024)])
    sent = _install_fake_vllm(monkeypatch, lambda req: next(responses))

    assert len(await embedding.embed("q")) == 1024
    assert len(sent) == 3


async def test_vllm_mode_without_url_gives_clear_error(monkeypatch):
    """EMBEDDING_MODE=vllm인데 EMBEDDING_URL을 안 채웠으면 원인을 알려주는 에러(학교에서 .env 빠뜨렸을 때)."""
    monkeypatch.setenv("EMBEDDING_MODE", "vllm")
    monkeypatch.delenv("EMBEDDING_URL", raising=False)

    with pytest.raises(embedding.EmbeddingError, match="EMBEDDING_URL"):
        await embedding.embed("q")


async def test_local_mode_uses_local_model(monkeypatch):
    """local 모드(기본값)면 네트워크 없이 로컬 BGE-M3로 계산 — 진짜 모델 대신 가짜 계산 함수로 바꿔서 경로만 확인."""
    monkeypatch.setenv("EMBEDDING_MODE", "local")
    monkeypatch.setattr(embedding, "_embed_local", lambda text: [0.2] * 1024)

    assert await embedding.embed("q") == [0.2] * 1024
