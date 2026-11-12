"""
바뀐 한글 글자 찾기 (2026-11-12 배포 후 버그 B25) — 두 곳에서 씀:
agent_loop._regenerate_if_odd(최종 답에 있으면 같은 요청으로 한 번 다시 만듦)와
memory_service(그래도 남은 답은 이전 대화에서 뺌 — 다음 답이 따라 쓰지 않게).

무슨 문제인가: Qwen3는 자주 안 쓰는 조합(예: 띄어쓰기 + "졸")을 한 글자가 아니라 바이트 조각 3개로 나눠 씀(GPU 서버 /tokenize로 확인).
마지막 조각을 잘못 고르면 "졸업"(EC A1 B8)이 "졡업"(EC A1 A1)처럼 **있긴 하지만 안 쓰는 글자**로 바뀌어 그대로 나감 — 깨진 표시(�)도 안 뜸.
처음 생기는 건 아주 드묾(학과 PC DB 답 1,400개·실험 120개에서 0건). 문제는 그 답이 다음 질문 때 이전 대화로 같이 가면
모델이 "졡업"을 계속 따라 쓴다는 것(실험 10/10, 새 대화면 0/10).

왜 LLM에게 고쳐 쓰라고 안 하나: 실험(11-12)에서 "졡업"을 고친 게 20번 중 0번, 정상 답을 괜히 다시 쓰게 하면 "ㅎㅎ"처럼 망가진 게 38번 중 13번.
→ 대신 "틀렸다"는 말 없이 같은 요청을 다시 보내면 15/15 정상(우연한 실수라). 찾는 건 판단 없는 단순 검사라 코드 몫.

어떻게 찾나: known_syllables.txt(학과 문서·프롬프트·학습노트·코드 주석·과거 답 1,400개에 나온 한글 841종)에 없는 한글이 있으면 "이상함".
흔한 다른 글자로 바뀐 경우(예: 졸→족)는 못 찾음 — 대부분은 안 쓰는 글자가 돼서 이 방법으로 잡힘.
잘못 걸리는 정상 답: 시험 답 696개 중 11개(약 1.6% — "멘토링", "기쁩니다" 등). 걸려도 그 한 턴이 이전 대화에서 빠질 뿐이라 손해가 작음.
목록에 없는 정상 글자가 자주 걸리면 known_syllables.txt에 그 글자를 추가하면 됨(한 줄로 이어 적은 글자 목록, 순서 상관없음).
"""

import re
from pathlib import Path

_HANGUL = re.compile("[가-힣]")   # 완성형 한글 한 글자(자모 낱자 ㄱ·ㅏ 등은 안 봄)

# 모듈을 불러올 때 한 번만 읽음 — 질문마다 파일을 다시 읽지 않게
KNOWN_SYLLABLES = frozenset(_HANGUL.findall(
    (Path(__file__).with_name("known_syllables.txt")).read_text(encoding="utf-8")
))


def odd_syllables(text: str | None) -> list[str]:
    """text 안의 한글 중 목록(KNOWN_SYLLABLES)에 없는 글자를 중복 없이 정렬해 돌려줌. 없으면 빈 리스트."""
    if not text:
        return []
    return sorted({c for c in _HANGUL.findall(text) if c not in KNOWN_SYLLABLES})
