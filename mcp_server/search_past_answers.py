"""
search_past_answers — 이미 답변이 달린 과거 질문(questions 테이블) 중 비슷한 것을 검색.

search_documents.py와 구조는 거의 동일(벡터+키워드 하이브리드, RRF 병합)하지만,
검색 대상이 "공식 문서"가 아니라 "과거에 실제로 답변된 질문·답변 쌍"이라는 점이 다름 —
이게 "질문 데이터를 축적해서 운영에 활용"한다는 프로젝트 목표의 핵심 메커니즘(롱텀 메모리).
"""

from db import get_pool
from embedding import embed
from rrf import merge_rrf

CANDIDATE_LIMIT = 20
TOP_K = 5


async def search_past_answers(query: str, top_k: int = TOP_K) -> list[dict]:
    pool = await get_pool()
    query_vector = embed(query)

    async with pool.acquire() as conn:
        # answer_text가 NULL인 행(아직 답 못한 질문 등)은 검색 대상에서 제외.
        # similarity(=1-벡터거리, 0~1)도 같이 반환 — search_documents.py와 같은 이유
        # (2026-10-27부터 LLM이 신뢰도 판단 재료로 씀).
        vector_rows = await conn.fetch(
            """
            SELECT id, question_text, answer_text, 1 - (embedding <=> $1) AS similarity
            FROM questions
            WHERE answer_text IS NOT NULL
            ORDER BY embedding <=> $1
            LIMIT $2
            """,
            query_vector,
            CANDIDATE_LIMIT,
        )
        keyword_rows = await conn.fetch(
            """
            SELECT id, question_text, answer_text, 1 - (embedding <=> $1) AS similarity
            FROM questions
            WHERE answer_text IS NOT NULL
              AND content_tsv @@ plainto_tsquery('simple', $2)
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
