"""
search_documents — 학과 공식 문서(documents 테이블)에서 하이브리드 검색.

흐름: 질문을 벡터로 변환 → (1)벡터 유사도 검색 (2)키워드 검색을 각각 수행 → RRF로 병합.
벡터검색은 "의미가 비슷한" 문서를, 키워드검색은 "같은 단어가 등장하는" 문서를 찾음 —
둘을 합치면 의미 기반 검색의 장점과, 고유명사·특정 용어를 정확히 맞추는 장점을 둘 다 가져감.
"""

from db import get_pool
from embedding import embed
from rrf import merge_rrf

CANDIDATE_LIMIT = 20   # 벡터/키워드 검색 각각에서 몇 개씩 후보로 가져올지 (병합 전 단계)
TOP_K = 5              # RRF 병합 후 최종적으로 몇 개를 돌려줄지


async def search_documents(query: str, top_k: int = TOP_K) -> list[dict]:
    pool = await get_pool()
    query_vector = await embed(query)  # async — 임베딩 계산 동안 서버가 멈추지 않게(embedding.py 참고)

    async with pool.acquire() as conn:
        # (1) 벡터 유사도 검색 — <=> 는 pgvector의 코사인 거리 연산자, 작을수록 더 비슷함.
        # similarity(=1-거리, 0~1, 높을수록 비슷함)를 같이 반환 — 전엔 정렬 기준으로만 쓰고
        # 버렸는데, 2026-10-27부터 LLM이 "이 문서를 얼마나 믿어도 되는지" 판단할 재료로 씀
        # (하드 임계값으로 코드가 자르는 게 아니라, 숫자를 보여주고 판단은 LLM에게 맡김).
        vector_rows = await conn.fetch(
            """
            SELECT id, chunk_text, source, 1 - (embedding <=> $1) AS similarity
            FROM documents
            ORDER BY embedding <=> $1
            LIMIT $2
            """,
            query_vector,
            CANDIDATE_LIMIT,
        )
        # (2) 키워드 검색 — tsvector 컬럼(content_tsv)에 대고 검색, ts_rank로 관련도 순 정렬.
        # similarity는 벡터검색과 똑같은 공식으로 계산(순위와 무관한 값이라 어느 쪽에서
        # 왔든 동일) — RRF 병합 후에도 그대로 남아있어야 해서 여기도 같이 계산해둠.
        keyword_rows = await conn.fetch(
            """
            SELECT id, chunk_text, source, 1 - (embedding <=> $1) AS similarity
            FROM documents
            WHERE content_tsv @@ plainto_tsquery('simple', $2)
            ORDER BY ts_rank(content_tsv, plainto_tsquery('simple', $2)) DESC
            LIMIT $3
            """,
            query_vector,
            query,
            CANDIDATE_LIMIT,
        )

    vector_results = [dict(row) for row in vector_rows]
    keyword_results = [dict(row) for row in keyword_rows]

    return merge_rrf(vector_results, keyword_results, id_key="id", top_k=top_k)
