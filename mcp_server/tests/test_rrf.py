"""
RRF 병합(rrf.py) — "현재 동작 고정" 테스트.

RRF는 벡터검색 순위와 키워드검색 순위를 하나로 합치는 공식(1 / (60 + 순위)의 합).
여기서는 "양쪽 다 나온 문서가 위로 간다"는 핵심 성질과, 반환 형식이 보존되는지를 확인.
"""

from rrf import merge_rrf


def test_item_in_both_lists_ranks_first():
    """양쪽 리스트에 다 등장한 문서(id=3)가 한쪽에서만 1위인 문서(id=1)보다 위에 와야 함
    — 하이브리드 검색을 쓰는 이유 자체("의미도 비슷하고 단어도 겹치는 문서가 제일 믿을 만함").
    id=2와 id=4는 각각 한쪽에서 2위라 점수가 같으므로 순서는 따지지 않고 포함 여부만 확인."""
    vector = [{"id": 1}, {"id": 2}, {"id": 3}]
    keyword = [{"id": 3}, {"id": 4}]

    ids = [row["id"] for row in merge_rrf(vector, keyword)]

    assert ids[0] == 3
    assert ids[1] == 1
    assert set(ids[2:]) == {2, 4}


def test_returns_at_most_top_k():
    """top_k개까지만 돌려줘야 함 — LLM에 넘기는 검색 결과 분량을 제한하는 장치."""
    vector = [{"id": i} for i in range(10)]
    assert len(merge_rrf(vector, [], top_k=3)) == 3


def test_other_row_fields_are_preserved():
    """id 말고 다른 필드(chunk_text, similarity)도 그대로 남아야 함 — similarity를 LLM에게
    넘기는 기능(2026-10-27)이 rrf.py 수정 없이 동작하는 근거가 바로 이 성질."""
    vector = [{"id": 1, "chunk_text": "본문", "similarity": 0.65}]

    [row] = merge_rrf(vector, [])

    assert row == {"id": 1, "chunk_text": "본문", "similarity": 0.65}


def test_empty_lists_return_empty():
    """검색 결과가 하나도 없을 때 에러 없이 빈 리스트를 돌려줘야 함(무관한 질문 등)."""
    assert merge_rrf([], []) == []
