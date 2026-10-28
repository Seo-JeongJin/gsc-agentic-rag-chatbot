"""
마크다운 청킹(chunking.py) — "현재 동작 고정" 테스트.

학과 문서를 어떻게 잘라서 documents 테이블에 넣는지가 검색 품질을 좌우함.
실제 문서(gsc-source-FAQ-updated.md)의 구조(제목/FAQ/표/유의사항)를 축소한 샘플로 확인.
"""

from chunking import MAX_SECTION_LENGTH, extract_meta_instructions, split_markdown_into_chunks

TABLE = "| 구분 | 날짜 |\n|---|---|\n| 접수 | 9월 |"

# 실제 학과 문서 구조를 흉내 낸 샘플: 일반 섹션 / FAQ 섹션 / 하위제목+표 / 유의사항
SAMPLE_DOC = f"""# 학과 안내

## 1. 학과 소개
학과는 2020년에 설립되었습니다.

## 2. 자주 묻는 질문
**Q. 기숙사가 있나요?**
네, 있습니다.

**Q. 장학금은요?**
성적 장학금이 있습니다.

## 3. 전형 일정
### 3.1 수시
{TABLE}

## 4. 챗봇 답변 시 유의사항
합격을 보장하지 마세요.
"""


def test_chunks_follow_heading_faq_and_table_structure():
    """청킹 규칙 4가지를 한 번에 확인:
    - ## 제목 단위로 나뉨, 각 청크 앞에 [섹션 경로]가 붙음(청크만 검색돼도 맥락이 살아있게)
    - FAQ 섹션은 Q&A 하나당 청크 하나(질문 하나 = 검색 단위 하나라 매칭이 정확함)
    - ### 하위 제목은 "상위 > 하위" 경로로 표시, 본문 없는 상위 제목(## 3.)은 청크를 안 만듦
    - "유의사항" 섹션은 검색 청크에서 빠짐"""
    chunks = split_markdown_into_chunks(SAMPLE_DOC)

    assert chunks == [
        "[1. 학과 소개]\n학과는 2020년에 설립되었습니다.",
        "[2. 자주 묻는 질문]\n**Q. 기숙사가 있나요?**\n네, 있습니다.",
        "[2. 자주 묻는 질문]\n**Q. 장학금은요?**\n성적 장학금이 있습니다.",
        f"[3. 전형 일정 > 3.1 수시]\n{TABLE}",
    ]


def test_meta_instruction_section_is_excluded_and_extractable():
    """"유의사항"은 검색될 문서가 아니라 챗봇이 항상 지켜야 할 지침이라, 검색 청크에는
    없어야 하고 extract_meta_instructions()로는 따로 꺼낼 수 있어야 함."""
    chunks = split_markdown_into_chunks(SAMPLE_DOC)

    assert not any("합격을 보장" in c for c in chunks)
    assert extract_meta_instructions(SAMPLE_DOC) == "합격을 보장하지 마세요."


def test_long_section_splits_by_paragraph_but_keeps_table_whole():
    """1200자를 넘는 긴 섹션은 문단 단위로 쪼개되, 표는 절대 중간에 자르지 않고
    독립된 청크 하나로 유지해야 함 — 표가 잘리면 행/열 의미가 깨져서 검색돼도 쓸모없음.
    [문단 800자] [표] [문단 800자] → 청크 3개, 가운데가 표 통째로."""
    long_para = "문단 내용입니다. " * 80
    body = f"{long_para}\n\n{TABLE}\n\n{long_para}"
    assert len(body) > MAX_SECTION_LENGTH  # 전제 확인: 정말 "긴 섹션" 분기를 타는 입력인지

    chunks = split_markdown_into_chunks(f"## 긴 섹션\n{body}")

    assert len(chunks) == 3
    assert chunks[1] == f"[긴 섹션]\n{TABLE}"
    assert all(c.startswith("[긴 섹션]\n") for c in chunks)
