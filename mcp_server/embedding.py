"""
임베딩 모델 래퍼.

지금(GPU 서버 연결 전 — 교내망 밖이라 접속 불가)은 이 노트북의 CPU에서 BGE-M3를 직접 돌림.
학교 GPU 서버(vLLM)에 연결 가능해지면, embed() 함수 내부만 "로컬 모델 호출"에서
"vLLM에 HTTP 요청"으로 바꾸면 됨 — 이 함수를 호출하는 쪽(search_documents.py 등)은
전혀 수정할 필요 없음. 그래서 이 래퍼 함수로 분리해둔 것.
"""

from sentence_transformers import SentenceTransformer

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    """모델은 최초 호출 시 한 번만 로딩(수십 초 소요, 이후 호출부터는 캐싱된 걸 재사용)."""
    global _model
    if _model is None:
        _model = SentenceTransformer("BAAI/bge-m3")
    return _model


def embed(text: str) -> list[float]:
    """텍스트 하나를 1024차원 벡터로 변환."""
    model = _get_model()
    vector = model.encode(text)
    return vector.tolist()
