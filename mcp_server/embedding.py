"""
임베딩 모델 래퍼 — 텍스트를 BGE-M3의 1024차원 벡터로 바꾸는 유일한 입구.
search_documents / search_past_answers / indexing_pipeline / /internal/embed 전부 이 embed() 하나만 씀.

EMBEDDING_MODE 환경변수로 계산하는 곳을 바꿈(2026-10-28, 4단계):
- local(기본값): 이 컨테이너 안에서 CPU로 BGE-M3를 직접 돌림 — 집 개발용(GPU 서버에 접속 불가할 때)
- vllm: 학과 GPU 서버의 vLLM 임베딩 API(POST {EMBEDDING_URL}/v1/embeddings) 호출 — 학교 실측/운영용.
  이 모드에선 모델·torch가 GPU 서버 쪽에 있으므로 이 컨테이너는 HTTP 요청만 보냄.
학교에서는 .env의 EMBEDDING_MODE/EMBEDDING_URL만 바꾸면 되고, 이 함수를 부르는 쪽은 전혀 안 바뀜.

embed()가 async인 이유(리뷰 18번): 전엔 동기 함수라 CPU 임베딩(수백 ms)이나 네트워크 대기 동안
MCP 서버 전체가 멈춰서 다른 검색 요청도 같이 기다렸음. 이제 local은 별도 스레드, vllm은 비동기 HTTP.

주의: 두 모드는 같은 모델(BAAI/bge-m3)이어야 함 — 다른 모델이면 이미 저장된 벡터와 비교가 의미 없어짐.
모드를 바꾼 뒤엔 문서를 다시 색인할 것(index_cli.py). 코사인 유사도는 벡터 길이(정규화 여부)와 무관해서
같은 모델이면 정규화 방식 차이는 검색 결과에 영향 없음.
"""

import asyncio
import logging
import os

import httpx

logger = logging.getLogger("uvicorn.error")

EMBEDDING_DIM = 1024                     # BGE-M3 차원 = documents/questions의 vector(1024) 컬럼
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"
EMBEDDING_TIMEOUT_SECONDS = 30.0
MAX_RETRY = 2
RETRY_BACKOFF_SECONDS = (1.0, 2.0)

_model = None  # local 모드에서만 로딩(최초 호출 시 1회, 수십 초) — vllm 모드에선 torch를 아예 import하지 않음


class EmbeddingError(Exception):
    """임베딩 계산 최종 실패(설정 누락, 재시도 다 실패, 응답 형식/차원 이상)."""


def _mode() -> str:
    """매 호출마다 환경변수를 읽음 — 테스트에서 모드를 바꿔가며 확인할 수 있게."""
    mode = os.environ.get("EMBEDDING_MODE", "local").strip().lower()
    if mode not in ("local", "vllm"):
        raise EmbeddingError(f"EMBEDDING_MODE는 local 또는 vllm이어야 함(현재: {mode!r}) — .env 확인")
    return mode


def _embed_local(text: str) -> list[float]:
    """local 모드: 이 컨테이너의 CPU로 BGE-M3 계산(동기 함수 — embed()가 별도 스레드에서 부름).
    sentence_transformers(=torch)를 여기서 import하는 이유: vllm 모드에서는 무거운 torch를 불러올 필요가 없어서."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(os.environ.get("EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL)
    return _model.encode(text).tolist()


async def _embed_vllm(text: str) -> list[float]:
    """vllm 모드: GPU 서버의 OpenAI 호환 임베딩 API 호출. 5xx/타임아웃/연결실패는 재시도, 4xx는 바로 실패."""
    base_url = (os.environ.get("EMBEDDING_URL") or "").rstrip("/")
    if not base_url:
        raise EmbeddingError("EMBEDDING_MODE=vllm인데 EMBEDDING_URL이 비어 있음 — .env에 GPU 서버 임베딩 주소(예: http://<GPU서버>:8001)를 넣을 것")
    payload = {"model": os.environ.get("EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL, "input": text}

    last_error: Exception | None = None
    for attempt in range(MAX_RETRY + 1):
        try:
            async with httpx.AsyncClient(timeout=EMBEDDING_TIMEOUT_SECONDS) as client:
                res = await client.post(f"{base_url}/v1/embeddings", json=payload)
            if res.status_code >= 500:
                raise httpx.HTTPStatusError(f"vLLM 임베딩 서버 오류 {res.status_code}", request=res.request, response=res)
            if res.status_code >= 400:
                raise EmbeddingError(f"vLLM 임베딩 요청 거절 {res.status_code}: {res.text[:300]}")
            vector = res.json()["data"][0]["embedding"]
            break
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.TransportError) as e:
            last_error = e
            logger.warning("임베딩 호출 실패(%d/%d): %s", attempt + 1, MAX_RETRY + 1, e)
            if attempt < MAX_RETRY:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)])
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise EmbeddingError(f"vLLM 임베딩 응답 형식이 예상과 다름: {e}") from e
    else:
        raise EmbeddingError(f"임베딩 호출 {MAX_RETRY + 1}번 모두 실패: {last_error}") from last_error

    if len(vector) != EMBEDDING_DIM:
        # 다른 모델이 떠 있으면 차원이 다름 — DB 컬럼(vector(1024))에 못 넣거나, 넣어도 검색이 엉망이 됨
        raise EmbeddingError(f"임베딩 차원이 {len(vector)} — {EMBEDDING_DIM}이어야 함(BGE-M3가 맞는지 GPU 서버 확인)")
    return vector


async def embed(text: str) -> list[float]:
    """텍스트 하나를 1024차원 벡터로 변환."""
    if _mode() == "vllm":
        return await _embed_vllm(text)
    # local: CPU 계산을 별도 스레드에서 — 계산하는 동안에도 서버가 다른 요청을 처리할 수 있게
    return await asyncio.to_thread(_embed_local, text)
