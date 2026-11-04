"""
마크다운 문서 전용 청킹 로직.

학과 문서(마크다운)는 제목(##/###) 구조, FAQ(**Q. ...** 형식), 표(|...|)가 섞여 있어서
"그냥 글자수로 자르기"가 아니라 문서 구조를 따라가며 자르는 게 맞다. 원칙:

1. ##/### 제목 단위로 먼저 나눔 — 섹션 경계에서 자르지, 문장·문단 중간에서 안 자름.
2. FAQ 섹션(**Q. ...** 패턴이 있는 섹션)은 Q&A 하나하나를 개별 청크로 — 이미 완결된 검색 단위라
   가장 정확하게 매칭됨 (예: "입도선매전형이 뭔가요" 질문 하나 = 청크 하나).
3. 표(|로 시작하는 줄)는 중간에 자르지 않고 통째로 유지 — 표를 쪼개면 행이 깨져서 의미를 잃음.
4. 각 청크 맨 앞에 "[어느 섹션에서 나온 내용인지]"를 붙여서, 청크만 따로 검색돼도 맥락이 살아있게 함.

주의: "챗봇 답변 시 유의사항"처럼 검색 대상이 아니라 "챗봇이 항상 지켜야 할 행동지침"에
해당하는 섹션은 extract_meta_instructions()로 따로 뽑아내고, 일반 문서 청크에는 포함하지 않음
(이런 지침은 매번 검색돼서 우연히 걸리는 게 아니라 항상 적용돼야 하므로 system_prompt.py로 가야 함).
"""

import re

MAX_SECTION_LENGTH = 1200  # 제목 단위로 나눈 섹션이 이보다 길면 추가로 쪼갬(표는 쪼개지 않음)
# (2026-11-04 학교 실측 B20) 일반 절(FAQ Q&A 제외)의 조각이 이 길이를 넘으면 줄 단위로 PIECE_MAX_LENGTH 이하 조각으로 다시 나눔.
# 왜: 4.5 입도선매전형 절(1,310자 — 자격 유형 4개·제한·면접 비중이 한 덩어리)이 여러 주제가 섞여 임베딩이 흐려져서
#     "검정고시 출신인데 돼요?"에 13위, 4.9 면접 절은 "면접에 못 가면?"에 19위로 밀렸고, 들어와도 검색 결과 한 건당 700자에서 잘려
#     "3등급(1.0–3.9)"·"검정고시 제한"(810·1,080번째 글자)을 LLM이 본 적이 없었음. 이 절들은 "- 항목" 줄이 빈 줄 없이 이어져서
#     위의 문단 단위 쪼개기로는 안 나뉨. 실측: 250자 조각 + 검색 결과 6개(search_documents.TOP_K)로 정답 전달 23/26 → 26/26
#     (400자 조각은 23/26 그대로). 500자 이하 절은 원래대로 둠 — 짧은 절까지 쪼개면 맥락만 끊김.
SPLIT_LINES_OVER = 500
PIECE_MAX_LENGTH = 250

HEADING_PATTERN = re.compile(r"^(#{2,3})\s+(.+)$", re.MULTILINE)
TITLE_PATTERN = re.compile(r"^#\s+(.+)$", re.MULTILINE)  # 문서 맨 위의 "# 문서제목" (## 아님)
DEFAULT_PREAMBLE_TITLE = "문서 개요"  # "# 제목"이 없는 문서의 머리말 섹션 이름
QA_SPLIT_PATTERN = re.compile(r"(?=\*\*Q\.)")  # **Q. 로 시작하는 지점마다 쪼갬

# 이 문구가 제목에 포함된 섹션은 "검색용 문서"가 아니라 "챗봇 행동지침"으로 취급.
META_INSTRUCTION_MARKER = "유의사항"


def _split_by_headings(markdown_text: str) -> list[tuple[str, str]]:
    """##/### 제목 경계로 (제목경로, 본문) 리스트로 분리.

    첫 ## 제목 앞의 본문(머리말)도 하나의 섹션으로 포함함 — 섹션 이름은 문서의 "# 제목"(없으면 "문서 개요").
    (2026-10-28 리뷰 17번: 전엔 ## 제목 사이의 본문만 잘라서, 문서 맨 앞 소개글이나 ## 제목이 아예 없는
    문서는 통째로 색인에서 빠졌음.)"""
    matches = list(HEADING_PATTERN.finditer(markdown_text))
    sections = []
    h2_title = ""

    # 머리말: 문서 시작 ~ 첫 ## 제목 직전(## 제목이 하나도 없으면 문서 전체). "# 제목" 줄 자체는 본문에서 뺌.
    preamble_end = matches[0].start() if matches else len(markdown_text)
    preamble = markdown_text[:preamble_end]
    title_match = TITLE_PATTERN.search(preamble)
    preamble_title = title_match.group(1).strip() if title_match else DEFAULT_PREAMBLE_TITLE
    preamble_body = TITLE_PATTERN.sub("", preamble).strip()
    if preamble_body:
        sections.append((preamble_title, preamble_body))

    for i, match in enumerate(matches):
        level, title = match.group(1), match.group(2).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown_text)
        body = markdown_text[start:end].strip()

        if level == "##":
            h2_title = title
            heading_path = title
        else:  # "###"
            heading_path = f"{h2_title} > {title}"

        if body:
            sections.append((heading_path, body))

    return sections


def _split_qa_section(heading_path: str, body: str) -> list[str]:
    """**Q. ...** 패턴이 있는 섹션을 Q&A 단위로 쪼갬. 각 청크 = 제목경로 + 질문 하나 + 답변."""
    parts = QA_SPLIT_PATTERN.split(body)
    chunks = []
    for part in parts:
        part = part.strip()
        if part:
            chunks.append(f"[{heading_path}]\n{part}")
    return chunks


def _split_long_section(heading_path: str, body: str) -> list[str]:
    """일반 섹션이 너무 길면 문단(빈 줄) 단위로 쪼갬 — 표(|로 시작하는 줄)는 쪼개지 않고 통째로 유지."""
    if len(body) <= MAX_SECTION_LENGTH:
        return [f"[{heading_path}]\n{body}"]

    paragraphs = body.split("\n\n")
    chunks = []
    buffer = ""

    for para in paragraphs:
        if para.strip().startswith("|"):
            # 표는 독립된 청크로 — 버퍼에 쌓인 내용 먼저 내보내고, 표는 절대 쪼개지 않음
            if buffer:
                chunks.append(f"[{heading_path}]\n{buffer.strip()}")
                buffer = ""
            chunks.append(f"[{heading_path}]\n{para.strip()}")
            continue

        if buffer and len(buffer) + len(para) > MAX_SECTION_LENGTH:
            chunks.append(f"[{heading_path}]\n{buffer.strip()}")
            buffer = para
        else:
            buffer = f"{buffer}\n\n{para}" if buffer else para

    if buffer:
        chunks.append(f"[{heading_path}]\n{buffer.strip()}")

    return chunks


def _split_into_line_pieces(chunk: str) -> list[str]:
    """"[제목]\\n본문" 청크가 SPLIT_LINES_OVER자를 넘으면 줄 단위로 PIECE_MAX_LENGTH자 이하 조각으로 나누고, 조각마다 같은 제목을 붙임(B20).

    - 표(|로 시작하는 연속된 줄)는 길어도 한 조각으로 통째로 — 표 행이 갈라지면 의미가 깨짐(원칙 3).
    - 한 줄이 PIECE_MAX_LENGTH보다 길면 그 줄은 자르지 않고 한 조각으로 둠 — 문장 중간에서 자르지 않는 원칙(원칙 1).
    - 줄 순서와 내용은 그대로(조각을 이어 붙이면 원래 본문)."""
    head, _, body = chunk.partition("\n")
    if len(chunk) <= SPLIT_LINES_OVER:
        return [chunk]

    # 표 줄 묶음과 일반 줄 묶음을 나눔 — 표는 통째로, 일반 줄만 길이에 맞춰 나눔
    blocks: list[list[str]] = []
    for line in body.split("\n"):
        if blocks and line.startswith("|") == blocks[-1][-1].startswith("|"):
            blocks[-1].append(line)
        else:
            blocks.append([line])

    pieces, buffer = [], ""
    for block in blocks:
        if block[0].startswith("|"):
            if buffer.strip():
                pieces.append(buffer.strip())
                buffer = ""
            pieces.append("\n".join(block).strip())
            continue
        for line in block:
            if buffer and len(buffer) + len(line) > PIECE_MAX_LENGTH:
                pieces.append(buffer.strip())
                buffer = line
            else:
                buffer = f"{buffer}\n{line}" if buffer else line
    if buffer.strip():
        pieces.append(buffer.strip())

    return [f"{head}\n{piece}" for piece in pieces if piece.strip()]


def split_markdown_into_chunks(markdown_text: str) -> list[str]:
    """마크다운 문서 전체를 받아서, 검색용 청크 리스트로 변환 (유의사항 섹션은 제외됨).
    일반 절은 문단 단위로 나눈 뒤, 그래도 긴 조각은 줄 단위로 한 번 더 나눔(2026-11-04 B20 — _split_into_line_pieces 참고).
    FAQ Q&A는 질문 하나 = 조각 하나가 이미 완결된 검색 단위라 더 나누지 않음."""
    sections = _split_by_headings(markdown_text)
    chunks = []

    for heading_path, body in sections:
        if META_INSTRUCTION_MARKER in heading_path:
            continue  # extract_meta_instructions()가 별도로 처리
        if "**Q." in body:
            chunks.extend(_split_qa_section(heading_path, body))
        else:
            for chunk in _split_long_section(heading_path, body):
                chunks.extend(_split_into_line_pieces(chunk))

    return chunks


def extract_meta_instructions(markdown_text: str) -> str | None:
    """"유의사항"이 제목에 포함된 섹션만 뽑아서 반환 (없으면 None).
    이 내용은 문서 청크가 아니라 system_prompt.py에 들어갈 챗봇 행동지침."""
    sections = _split_by_headings(markdown_text)
    for heading_path, body in sections:
        if META_INSTRUCTION_MARKER in heading_path:
            return body
    return None
