"""
문서 색인 파이프라인 — 원본 문서 텍스트를 청크로 쪼개고, 각 청크를 임베딩해서
documents 테이블에 저장. 관리자가 새 문서를 추가할 때 수동으로 실행하는 구조
(사용자 질문이 들어올 때마다 자동으로 도는 게 아님).

청킹 로직 자체는 chunking.py에 분리돼 있음(제목/FAQ/표 구조를 인식하는 마크다운 전용 로직) —
여기서는 그 결과를 받아서 임베딩하고 저장하는 역할만 함.
"""

from chunking import split_markdown_into_chunks
from db import get_pool
from embedding import embed


async def index_document(source: str, full_text: str) -> int:
    """
    source(문서를 구분하는 이름)와 전체 마크다운 텍스트를 받아 청크로 쪼개고 색인.

    같은 source로 다시 호출하면(문서 수정 후 재색인) ON CONFLICT로 기존 청크를 덮어씀 —
    documents 테이블의 UNIQUE(source, chunk_index) 제약 덕분에 중복 생성 없이 안전하게 갱신됨.

    반환값: 몇 개의 청크가 저장됐는지.
    """
    pool = await get_pool()
    chunks = split_markdown_into_chunks(full_text)

    async with pool.acquire() as conn:
        for chunk_index, chunk_text in enumerate(chunks):
            vector = embed(chunk_text)
            await conn.execute(
                """
                INSERT INTO documents (source, chunk_index, chunk_text, embedding)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (source, chunk_index) DO UPDATE
                SET chunk_text = EXCLUDED.chunk_text,
                    embedding = EXCLUDED.embedding
                """,
                source,
                chunk_index,
                chunk_text,
                vector,
            )

    return len(chunks)
