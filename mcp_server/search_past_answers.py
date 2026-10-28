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

# "검색해도 되는 과거 답변"의 조건(2026-10-28 결정, 리뷰 1번) — 벡터/키워드 두 쿼리에 똑같이 적용.
# - needs_review = FALSE: 에스컬레이션(답 못함)·가드레일 실패로 안전문구가 나간 질문 제외
# - guardrail_passed = TRUE: 출력 가드레일 검사를 실제로 통과한 답변만
# - user_rating이 없거나 1보다 큼: 사용자가 👎(1점)를 준 답변 제외
# - embedding IS NOT NULL: 임베딩이 저장된 질문만(위 조건을 만족하는 질문만 backend가 임베딩을 저장함)
# 전엔 "answer_text IS NOT NULL"(= 사실상 모든 질문)이라, 틀린 답이 "과거 답변" 근거로 재활용될 수 있었음.
TRUSTED_ANSWER_CONDITION = """
    needs_review = FALSE
    AND guardrail_passed = TRUE
    AND (user_rating IS NULL OR user_rating > 1)
    AND embedding IS NOT NULL
"""


async def search_past_answers(query: str, top_k: int = TOP_K) -> list[dict]:
    pool = await get_pool()
    query_vector = await embed(query)  # async — 임베딩 계산 동안 서버가 멈추지 않게(embedding.py 참고)

    async with pool.acquire() as conn:
        # 믿을 만한 과거 답변(TRUSTED_ANSWER_CONDITION)만 검색 대상.
        # similarity(=1-벡터거리, 0~1)도 같이 반환 — search_documents.py와 같은 이유
        # (2026-10-27부터 LLM이 신뢰도 판단 재료로 씀).
        vector_rows = await conn.fetch(
            f"""
            SELECT id, question_text, answer_text, 1 - (embedding <=> $1) AS similarity
            FROM questions
            WHERE {TRUSTED_ANSWER_CONDITION}
            ORDER BY embedding <=> $1
            LIMIT $2
            """,
            query_vector,
            CANDIDATE_LIMIT,
        )
        keyword_rows = await conn.fetch(
            f"""
            SELECT id, question_text, answer_text, 1 - (embedding <=> $1) AS similarity
            FROM questions
            WHERE {TRUSTED_ANSWER_CONDITION}
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
