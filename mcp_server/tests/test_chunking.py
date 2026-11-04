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


def test_text_before_first_section_is_kept():
    """[17번 버그 재현] 첫 ## 제목 앞에 있는 본문(문서 소개 등)도 검색 청크로 남아야 함.
    수정 전엔 ## 제목 사이의 본문만 잘라내서, 맨 앞부분은 통째로 버려졌음. 섹션 이름은 문서의 # 제목을 씀."""
    doc = "# 글로벌시스템융합과 안내\n\n본 문서는 입학 안내 자료입니다.\n\n## 1. 학과 소개\n소개 내용."

    chunks = split_markdown_into_chunks(doc)

    assert chunks[0] == "[글로벌시스템융합과 안내]\n본 문서는 입학 안내 자료입니다."
    assert chunks[1] == "[1. 학과 소개]\n소개 내용."


# ----- 2026-11-04 실측 B20: 긴 항목 목록 절을 줄 단위 조각으로 -----

# 실제 4.5 입도선매전형 절처럼 "- 항목" 줄이 여러 개 이어진 긴 절(빈 줄 없음 → 문단 단위로는 안 쪼개짐)
# (줄 끝 공백 없음 — 실제 문서처럼. 조각 경계의 앞뒤 공백은 정리되므로 줄 끝 공백이 있으면 비교가 어긋남)
BULLETS = [f"- 항목 {i}: " + " ".join(["자격 기준 설명입니다"] * 6) for i in range(8)]


def test_long_bullet_section_is_split_into_short_pieces_with_heading():
    """[B20 재현] 1,310자짜리 입도선매 절이 한 덩어리라 "검정고시 출신인데 돼요?"에 13위로 밀렸고, 들어와도 700자에서 잘려서
    "3등급(1.0–3.9)"(810번째 글자)·"검정고시 제한"(1,080번째 글자)을 LLM이 본 적이 없었음. 줄 단위 250자 이하 조각으로 나누면
    정답 전달 23/26 → 26/26(검색 결과 6개와 함께). 조각마다 섹션 제목을 붙여서 조각만 검색돼도 맥락이 남게 함."""
    from chunking import PIECE_MAX_LENGTH

    chunks = split_markdown_into_chunks("## 4.5 입도선매전형\n" + "\n".join(BULLETS))

    assert len(chunks) > 1
    assert all(c.startswith("[4.5 입도선매전형]\n") for c in chunks)
    assert all(len(c.partition("\n")[2]) <= PIECE_MAX_LENGTH for c in chunks)
    assert [line for c in chunks for line in c.split("\n")[1:]] == BULLETS  # 내용은 빠짐없이, 순서대로


def test_short_section_and_faq_are_not_split():
    """500자 이하 절과 FAQ Q&A는 그대로 — Q&A는 이미 질문 하나 = 조각 하나라 쪼개면 질문과 답이 갈라짐."""
    short = "## 짧은 절\n" + "\n".join(BULLETS[:2])
    long_qa = "## 자주 묻는 질문\n**Q. 자격이 뭐예요?**\n" + "\n".join(BULLETS)

    assert len(split_markdown_into_chunks(short)) == 1
    assert len(split_markdown_into_chunks(long_qa)) == 1


def test_table_inside_long_section_stays_whole():
    """긴 절을 줄 단위로 나눠도 표(|로 시작하는 줄 묶음)는 한 조각으로 통째로 — 표 행이 갈라지면 의미가 깨짐."""
    big_table = "| 전형 | 인원 |\n|---|---|\n" + "\n".join(f"| 전형{i} | {i} |" for i in range(30))
    chunks = split_markdown_into_chunks("## 4.2 모집인원\n" + "\n".join(BULLETS[:3]) + "\n" + big_table)

    assert f"[4.2 모집인원]\n{big_table}" in chunks
