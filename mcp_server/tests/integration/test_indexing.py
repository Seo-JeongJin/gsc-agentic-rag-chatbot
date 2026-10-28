"""
문서 색인(indexing_pipeline.py, index_cli.py) 통합 테스트 — 리뷰 17번 + 6번(색인 실행 명령).
"""

import index_cli
import indexing_pipeline
from tests.integration.conftest import async_fake_embed, fake_vector

DOC_V1 = """# 학과 안내

## 1. 학과 소개
학과 소개 내용입니다.

## 2. 전형
전형 내용입니다.

## 3. 장학금
장학금 내용입니다.
"""

# 섹션 하나(장학금)가 빠진 수정본 — 재색인하면 청크가 3개 → 2개로 줄어야 함
DOC_V2 = """# 학과 안내

## 1. 학과 소개
학과 소개 내용입니다(수정).

## 2. 전형
전형 내용입니다.
"""


async def _chunks(db, source: str) -> list[str]:
    """해당 source로 저장된 청크 텍스트를 순서대로."""
    rows = await db.fetch("SELECT chunk_text FROM documents WHERE source=$1 ORDER BY chunk_index", source)
    return [r["chunk_text"] for r in rows]


async def test_reindex_removes_chunks_that_no_longer_exist(db, monkeypatch):
    """[17번 버그 재현] 문서를 고쳐서 청크 수가 줄어든 채로 다시 색인하면, 없어진 청크는 DB에서도 지워져야 함.
    수정 전엔 ON CONFLICT로 "같은 번호 청크만 덮어쓰기"라서, 3번째 청크(장학금)가 옛 내용 그대로 남아
    계속 검색됐음 — 문서에서 지운 정보를 챗봇이 계속 답하는 위험."""
    monkeypatch.setattr(indexing_pipeline, "embed", async_fake_embed)

    await indexing_pipeline.index_document("faq.md", DOC_V1)
    assert len(await _chunks(db, "faq.md")) == 3

    count = await indexing_pipeline.index_document("faq.md", DOC_V2)

    chunks = await _chunks(db, "faq.md")
    assert count == len(chunks) == 2
    assert not any("장학금" in c for c in chunks)
    assert any("(수정)" in c for c in chunks)


async def test_reindex_does_not_touch_other_sources(db, monkeypatch):
    """재색인은 같은 source의 청크만 교체해야 함 — 다른 문서의 청크까지 지우면 안 됨."""
    monkeypatch.setattr(indexing_pipeline, "embed", async_fake_embed)
    await indexing_pipeline.index_document("other.md", DOC_V1)

    await indexing_pipeline.index_document("faq.md", DOC_V2)

    assert len(await _chunks(db, "other.md")) == 3


async def test_index_cli_indexes_given_file(db, monkeypatch, tmp_path):
    """[6번] 학과 컴퓨터에서 "파일 경로만 주면 색인"하는 명령. source 이름은 기본으로 파일 이름을 씀."""
    monkeypatch.setattr(indexing_pipeline, "embed", async_fake_embed)
    doc = tmp_path / "gsc-faq.md"
    doc.write_text(DOC_V1, encoding="utf-8")

    count = await index_cli.run([str(doc)])

    assert count == 3
    assert len(await _chunks(db, "gsc-faq.md")) == 3
