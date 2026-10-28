"""
RRF(Reciprocal Rank Fusion) 병합.

벡터검색 결과 리스트와 키워드검색 결과 리스트, 두 개의 서로 다른 "순위 리스트"를
하나의 점수로 합치는 알고리즘. search_documents.py와 search_past_answers.py가 공통으로 사용.

핵심 아이디어: 각 리스트에서 몇 번째 순위에 있었는지를 보고 점수를 매기되,
"한쪽에만 있어도" 점수가 생기고, "양쪽 모두 상위에 있으면" 점수가 더 높아지게 만듦.
"""

from collections import defaultdict
from typing import Any

RRF_K = 60  # 공식의 상수. 값이 클수록 순위 차이의 영향이 완만해짐(관례적으로 60을 많이 씀)


def merge_rrf(
    *ranked_lists: list[dict[str, Any]],
    id_key: str = "id",
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """
    여러 개의 순위 리스트(각각 dict의 리스트, 리스트 내 순서가 곧 순위)를 RRF로 병합.

    예: merge_rrf(벡터검색결과, 키워드검색결과, id_key="id", top_k=5)
    같은 id_key 값을 가진 행이 여러 리스트에 등장할수록, 그리고 상위에 있을수록 최종 점수가 높아짐.
    """
    scores: dict[Any, float] = defaultdict(float)
    rows_by_id: dict[Any, dict[str, Any]] = {}

    for ranked_list in ranked_lists:
        for rank, row in enumerate(ranked_list, start=1):
            key = row[id_key]
            scores[key] += 1 / (RRF_K + rank)
            rows_by_id[key] = row  # 같은 id가 여러 리스트에 있으면 마지막 걸로 덮어써도 내용은 동일

    ranked_ids = sorted(scores, key=lambda k: scores[k], reverse=True)[:top_k]
    return [rows_by_id[k] for k in ranked_ids]
