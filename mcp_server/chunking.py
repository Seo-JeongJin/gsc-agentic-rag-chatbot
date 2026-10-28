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

HEADING_PATTERN = re.compile(r"^(#{2,3})\s+(.+)$", re.MULTILINE)
QA_SPLIT_PATTERN = re.compile(r"(?=\*\*Q\.)")  # **Q. 로 시작하는 지점마다 쪼갬

# 이 문구가 제목에 포함된 섹션은 "검색용 문서"가 아니라 "챗봇 행동지침"으로 취급.
META_INSTRUCTION_MARKER = "유의사항"


def _split_by_headings(markdown_text: str) -> list[tuple[str, str]]:
    """##/### 제목 경계로 (제목경로, 본문) 리스트로 분리."""
    matches = list(HEADING_PATTERN.finditer(markdown_text))
    sections = []
    h2_title = ""

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


def split_markdown_into_chunks(markdown_text: str) -> list[str]:
    """마크다운 문서 전체를 받아서, 검색용 청크 리스트로 변환 (유의사항 섹션은 제외됨)."""
    sections = _split_by_headings(markdown_text)
    chunks = []

    for heading_path, body in sections:
        if META_INSTRUCTION_MARKER in heading_path:
            continue  # extract_meta_instructions()가 별도로 처리
        if "**Q." in body:
            chunks.extend(_split_qa_section(heading_path, body))
        else:
            chunks.extend(_split_long_section(heading_path, body))

    return chunks


def extract_meta_instructions(markdown_text: str) -> str | None:
    """"유의사항"이 제목에 포함된 섹션만 뽑아서 반환 (없으면 None).
    이 내용은 문서 청크가 아니라 system_prompt.py에 들어갈 챗봇 행동지침."""
    sections = _split_by_headings(markdown_text)
    for heading_path, body in sections:
        if META_INSTRUCTION_MARKER in heading_path:
            return body
    return None
