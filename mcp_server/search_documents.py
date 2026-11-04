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
TOP_K = 6              # RRF 병합 후 최종적으로 몇 개를 돌려줄지
# (2026-11-04 학교 실측 B20) 5 → 6: 긴 절을 250자 조각으로 나눈 뒤(chunking.py) 조각이 짧아져서 6개도 검색 1번 예산(2500자) 안에 들어감
# (평균 5.9개). 5개로는 "면접에 못 가면?"의 면접 결시 조각·"입도선매전형"의 50% 조각(둘 다 6위)이 빠졌음 → 6개면 정답 전달 26/26.
# 참고: 키워드 검색(아래 plainto_tsquery, 단어를 AND로 묶음)은 질문 문장 그대로면 50/50 0건이지만, 모델이 실제로 쓰는 짧은 검색어
# ("입학 정원", "인턴십" 등 — 24개 중 17개를 줄여 씀)에선 26개 중 6~8개가 걸려서 동작함(실측_버그기록.md B18 정정).

# 키워드 검색 대상 = 청크에서 첫 줄([제목])을 뺀 본문 — 위 설명과 아래 keyword_rows 주석 참고(2026-11-04 A3)
_BODY_TSVECTOR = "to_tsvector('simple', substr(chunk_text, strpos(chunk_text, E'\\n') + 1))"


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
        # (2) 키워드 검색 — 청크의 **본문**(첫 줄 [제목] 제외)에 대고 검색, ts_rank로 관련도 순 정렬.
        # similarity는 벡터검색과 똑같은 공식으로 계산(순위와 무관한 값이라 어느 쪽에서
        # 왔든 동일) — RRF 병합 후에도 그대로 남아있어야 해서 여기도 같이 계산해둠.
        # (2026-11-04 실측 A3) 제목을 빼는 이유: 긴 절을 250자 조각으로 나누며(chunking.py) 조각마다 "[… > 4.5 입도선매전형]" 제목을
        # 붙였더니, 모델이 쓰는 짧은 검색어 "입도선매전형"이 그 절의 조각 전부에 제목 때문에 걸려서 "학비 50% 감면"이 있는 문단이
        # 12위 밖으로 밀렸음. 제목은 벡터 검색용 맥락으로만 쓰고 키워드는 본문만 봄 → 모델 실제 검색어 26개 정답 전달 25 → 26.
        # 저장된 content_tsv 컬럼(제목 포함, schema.sql) 대신 검색할 때 계산함 — 컬럼을 바꾸면 모든 컴퓨터의 DB를 다시 만들어야 해서.
        # 대가: 미리 만든 GIN 색인을 못 씀(문서 110조각이라 측정상 차이 없음 — 실측_버그기록.md A3).
        keyword_rows = await conn.fetch(
            f"""
            SELECT id, chunk_text, source, 1 - (embedding <=> $1) AS similarity
            FROM documents
            WHERE {_BODY_TSVECTOR} @@ plainto_tsquery('simple', $2)
            ORDER BY ts_rank({_BODY_TSVECTOR}, plainto_tsquery('simple', $2)) DESC
            LIMIT $3
            """,
            query_vector,
            query,
            CANDIDATE_LIMIT,
        )

    vector_results = [dict(row) for row in vector_rows]
    keyword_results = [dict(row) for row in keyword_rows]

    return merge_rrf(vector_results, keyword_results, id_key="id", top_k=top_k)
