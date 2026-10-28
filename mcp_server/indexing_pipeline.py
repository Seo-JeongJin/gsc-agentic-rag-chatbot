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

    같은 source로 다시 호출하면(문서 수정 후 재색인, 또는 임베딩 모델 교체 후 재색인) 그 source의
    기존 청크를 **전부 지우고 새로 넣음** — 한 트랜잭션이라 중간에 실패하면 지운 것도 취소되어 원래 상태로 돌아감.
    (2026-10-28 리뷰 17번: 전엔 ON CONFLICT로 "같은 번호 청크만 덮어쓰기"라서, 문서가 줄어들면
    없어진 청크가 옛 내용 그대로 남아 계속 검색됐음.) 다른 source의 청크는 건드리지 않음.

    임베딩을 트랜잭션 "밖"에서 먼저 다 계산하는 이유: 임베딩은 오래 걸리는데(CPU면 청크당 수백 ms),
    그동안 트랜잭션을 열어두면 검색 쪽이 이 문서의 청크를 못 보는 시간이 길어짐. DB 작업만 짧게 묶음.

    반환값: 몇 개의 청크가 저장됐는지.
    """
    chunks = split_markdown_into_chunks(full_text)
    vectors = [await embed(chunk_text) for chunk_text in chunks]

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM documents WHERE source = $1", source)
            await conn.executemany(
                "INSERT INTO documents (source, chunk_index, chunk_text, embedding) VALUES ($1, $2, $3, $4)",
                [(source, i, text, vector) for i, (text, vector) in enumerate(zip(chunks, vectors))],
            )

    return len(chunks)
