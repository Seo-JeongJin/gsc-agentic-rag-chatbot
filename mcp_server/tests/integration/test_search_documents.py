"""
문서 검색(search_documents.py) 통합 테스트 — 실제 PostgreSQL(테스트 전용 DB)에 문서를 넣고 하이브리드 검색 결과를 확인.

벡터는 가짜(방향을 직접 정한 1024차원 벡터)로 넣어서 "벡터로는 어느 쪽이 더 가까운지"를 테스트가 정함 —
그래야 키워드 검색이 순위를 어떻게 바꾸는지만 따로 볼 수 있음.
"""

import search_documents as sd


def _unit(index: int) -> list[float]:
    """index번째 축 방향의 단위 벡터(1024차원) — 서로 다른 축끼리는 코사인 유사도 0."""
    vector = [0.0] * 1024
    vector[index] = 1.0
    return vector


async def _fake_embed(text: str) -> list[float]:
    """검색어는 항상 0번 축 방향 — 문서 벡터가 0번 축에 가까울수록 벡터 검색 순위가 높음."""
    return _unit(0)


async def _insert(db, index: int, text: str, vector: list[float]) -> int:
    return await db.fetchval(
        "INSERT INTO documents (source, chunk_index, chunk_text, embedding) VALUES ('faq.md', $1, $2, $3) RETURNING id",
        index, text, vector,
    )


async def test_heading_words_do_not_count_for_keyword_search(db, monkeypatch):
    """[2026-11-04 실측 A3 재현] 긴 절을 250자 조각으로 나누면서(B20) 조각마다 "[… > 4.5 입도선매전형]" 제목을 붙였더니,
    모델이 쓰는 짧은 검색어 "입도선매전형"이 키워드 검색에서 그 절의 조각 **전부**에 제목 때문에 걸려서 "면접은 필수"·"유형 3" 같은
    조각이 위로 몰리고, 본문에 "입도선매전형 … 50%"가 있는 장학 혜택 문단은 12위 밖으로 밀렸음("입도선매가 뭐예요?"에 50% 누락).
    키워드 검색은 첫 줄([제목])을 뺀 본문만 보게 함 — 실측: 모델 실제 검색어 26개 정답 전달 25 → 26.
    (제목은 벡터 검색용 맥락으로는 계속 붙어 있음 — 조각만 따로 검색돼도 어느 절인지 알 수 있게)"""
    monkeypatch.setattr(sd, "embed", _fake_embed)
    # 제목에만 검색어가 있는 조각 7개 — 벡터로는 검색어와 같은 방향(가장 가까움)
    for i in range(7):
        await _insert(db, i, f"[4. 입학 안내 > 4.5 입도선매전형 > 입도선매전형 자격]\n- 항목 {i}: 기타 안내", _unit(0))
    # 본문에 검색어가 있는 장학 혜택 조각 — 벡터로는 덜 가까움(유사도 0.6)
    answer_vector = [0.0] * 1024
    answer_vector[0], answer_vector[1] = 0.6, 0.8
    answer_id = await _insert(db, 7, "[5. 자주 묻는 질문 > 지원 자격·전형]\n**Q.** 입도선매전형 장학 혜택: 수업료 50% 감면", answer_vector)

    results = await sd.search_documents("입도선매전형")

    assert answer_id in [r["id"] for r in results]
