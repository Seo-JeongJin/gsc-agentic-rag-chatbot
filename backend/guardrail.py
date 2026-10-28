"""
입력 가드레일 — 규칙적으로 판별 가능한 것만 코드로 거른다.

프롬프트 인젝션·욕설·시스템프롬프트 유출처럼 "표현이 불규칙한" 것은 여기서 다루지 않음
→ 그런 건 system_prompt.py의 지침으로 LLM이 직접 판단·거부하게 함 (2026-10-26 교수 피드백).
이 파일은 길이/형식/빈도처럼 "기계적으로 딱 떨어지는" 규칙만 담당한다.

하네스(테스트케이스+FPR/FNR 측정)는 2026-10-26 기준 추후 구현으로 확정, 지금은 미작성.
"""

import re
import time
import unicodedata
from collections import defaultdict

# ---------------------------------------------------------------------------
# 상수 — 규칙의 기준값. 나중에 실제 질문 패턴 보고 조정 가능.
# ---------------------------------------------------------------------------

MIN_LENGTH = 1          # 공백만 있거나 완전히 빈 질문은 통과 못 함
MAX_LENGTH = 1000       # FAQ 질문이 에세이 수준으로 길 필요는 없음. 너무 길면 거부(프롬프트 스터핑/자원낭비 방지)

REPEATED_CHAR_THRESHOLD = 30   # 같은 문자가 이 횟수 이상 연속되면 "의미없는 스팸"으로 간주

RATE_LIMIT_WINDOW_SECONDS = 60  # 요청 빈도 제한을 재는 시간 창(초)
RATE_LIMIT_MAX_REQUESTS = 10    # 그 시간 창 안에 허용하는 최대 요청 수 — 넘으면 일시 차단

DUPLICATE_WINDOW_SECONDS = 10   # 완전히 같은 질문이 이 시간 안에 또 오면 중복 요청으로 간주(이중클릭 등)

# 스크립트/XSS 패턴 — 주 방어는 프론트엔드의 출력 escape(관리자 대시보드가 질문 로그를
# 그대로 렌더링할 때 저장형 XSS 위험), 여기는 보조 레이어로 명백한 패턴만 차단.
DANGEROUS_PATTERN = re.compile(r"<script|javascript:|on\w+\s*=", re.IGNORECASE)

# 한국 개인정보 패턴 — 사용자가 질문에 실수로(또는 의도적으로) 민감정보를 그대로 적는 걸 방지.
# 주의: 이건 "답변/문서에 있는 개인정보를 가리는 마스킹"(요구사항 아님, 전면 제거됨)과는 다른 목적.
#       여기는 "사용자가 입력한 질문 안에 민감정보가 있는지" 체크하는 것.
PII_PATTERNS = {
    "주민등록번호": re.compile(r"\d{6}-\d{7}"),                     # 예: 010101-1234567 형식
    "카드번호": re.compile(r"\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}"),  # 4자리씩 4묶음
    "전화번호": re.compile(r"01[0-9][-\s]?\d{3,4}[-\s]?\d{4}"),       # 010-1234-5678 형식
}

# ---------------------------------------------------------------------------
# 요청 빈도 제한용 임시 저장소.
# 주의: 이건 메모리(dict)에만 저장되는 상태라, FastAPI 프로세스가 재시작되면 초기화됨.
#       서버가 한 대뿐인 지금 규모에서는 이 정도로 충분 — 여러 대로 늘어나면 Redis 등 공유 저장소 필요.
# ---------------------------------------------------------------------------
_request_timestamps: dict[str, list[float]] = defaultdict(list)

# 세션별 "마지막으로 보낸 질문 + 그 시각"을 기억해두는 저장소 (중복요청 체크용).
# rate limit과 마찬가지로 메모리 저장이라 서버 재시작 시 초기화됨.
_last_question: dict[str, tuple[str, float]] = {}


def _check_control_characters(question_text: str) -> tuple[bool, str | None]:
    """null byte 등 눈에 안 보이는 제어문자가 섞여있는지 확인.
    일반 줄바꿈(\\n)/탭(\\t)은 정상 입력으로 허용하고, 그 외 제어문자(ASCII 0~31)만 차단.
    이런 문자가 섞여 들어오면 나중에 로그 저장이나 LLM 전달 과정에서 예상 못 한 오류를 일으킬 수 있음."""
    allowed_control = {"\n", "\t"}
    for ch in question_text:
        if ord(ch) < 32 and ch not in allowed_control:
            return False, "질문에 허용되지 않는 문자가 포함되어 있습니다."
    return True, None


def _check_length(question_text: str) -> tuple[bool, str | None]:
    """질문 길이가 허용 범위(MIN_LENGTH~MAX_LENGTH) 안에 있는지 확인."""
    stripped = question_text.strip()
    if len(stripped) < MIN_LENGTH:
        return False, "질문이 비어있습니다."
    if len(question_text) > MAX_LENGTH:
        return False, f"질문이 너무 깁니다. {MAX_LENGTH}자 이내로 입력해주세요."
    return True, None


def _check_repeated_spam(question_text: str) -> tuple[bool, str | None]:
    """같은 문자가 비정상적으로 길게 반복되는지 확인 — 의미없는 스팸/자원낭비성 입력 차단."""
    pattern = re.compile(r"(.)\1{" + str(REPEATED_CHAR_THRESHOLD - 1) + r",}")
    if pattern.search(question_text):
        return False, "반복된 문자가 너무 많습니다."
    return True, None


def _check_pii(question_text: str) -> tuple[bool, str | None]:
    """질문 안에 주민번호/카드번호/전화번호 형식의 문자열이 있는지 확인.
    발견되면 민감정보가 로그(questions 테이블)에 그대로 남는 걸 막기 위해 차단.
    전각숫자 등 유니코드 우회를 막기 위해 NFKC 정규화 후 검사(아래 _normalize 참고)."""
    normalized = _normalize(question_text)
    for name, pattern in PII_PATTERNS.items():
        if pattern.search(normalized):
            return False, f"질문에 개인정보({name} 형식)로 보이는 내용이 포함되어 있어 처리할 수 없습니다."
    return True, None


def _normalize(text: str) -> str:
    """전각문자 등 "겉보기엔 같아 보이지만 코드값이 다른" 유니코드를 표준 형태로 변환.
    이게 없으면 예를 들어 전각숫자(０１０)로 정규식 패턴매칭(전화번호 등)을 우회할 수 있음.
    주의: 이 결과는 "검사용"으로만 쓰고, 실제 저장/LLM 전달에는 원본 텍스트를 그대로 씀
    (정규화 과정에서 사용자가 입력한 원문이 미묘하게 바뀔 수 있어, 로그는 원문 그대로 남기는 게 맞음)."""
    return unicodedata.normalize("NFKC", text)


def _check_dangerous_patterns(question_text: str) -> tuple[bool, str | None]:
    """스크립트 태그/javascript: URI/이벤트핸들러 속성 같은 명백한 XSS 패턴 차단.
    이게 질문 로그에 그대로 저장됐다가 관리자 대시보드에서 이스케이프 없이 렌더링되면
    저장형 XSS로 이어질 수 있음 — 프론트엔드 출력 escape가 주 방어, 여기는 보조 레이어.
    PII 체크와 마찬가지로 유니코드 우회 방지를 위해 정규화 후 검사."""
    normalized = _normalize(question_text)
    if DANGEROUS_PATTERN.search(normalized):
        return False, "허용되지 않는 형식의 입력입니다."
    return True, None


def _check_duplicate_request(question_text: str, session_id: str) -> tuple[bool, str | None]:
    """같은 세션에서 완전히 동일한 질문이 짧은 시간(DUPLICATE_WINDOW_SECONDS) 안에 또 오면 차단.
    이중클릭 같은 실수나 단순 반복 스팸으로 인한 불필요한 LLM 호출(자원낭비)을 막기 위함."""
    now = time.time()
    last = _last_question.get(session_id)
    if last is not None:
        last_text, last_time = last
        if question_text == last_text and now - last_time < DUPLICATE_WINDOW_SECONDS:
            return False, "동일한 질문이 너무 빠르게 반복 전송되었습니다."
    _last_question[session_id] = (question_text, now)
    return True, None


def _check_rate_limit(session_id: str) -> tuple[bool, str | None]:
    """같은 세션이 짧은 시간 안에 너무 많은 요청을 보내는지 확인 — 자원 고갈 공격/스팸 방지."""
    now = time.time()
    timestamps = _request_timestamps[session_id]

    # 시간 창(RATE_LIMIT_WINDOW_SECONDS)보다 오래된 기록은 버림
    timestamps[:] = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW_SECONDS]

    if len(timestamps) >= RATE_LIMIT_MAX_REQUESTS:
        return False, "요청이 너무 많습니다. 잠시 후 다시 시도해주세요."

    timestamps.append(now)
    return True, None


def check_input(question_text: str, session_id: str) -> tuple[bool, str | None]:
    """
    입력 가드레일의 진입점 — agent_loop 3번 단계에서 LLM 호출 전에 이 함수부터 실행.

    위의 규칙들을 순서대로 체크하고, 하나라도 걸리면 즉시 멈추고 (False, 사유)를 반환.
    전부 통과하면 (True, None) 반환 — 이 경우에만 다음 단계(LLM 호출)로 진행.

    여기서 거르지 못하는 것(프롬프트 인젝션/욕설/시스템프롬프트 유출 시도)은
    의도적으로 이 함수의 책임이 아님 — system_prompt.py의 지침으로 LLM이 직접 처리.
    """
    checks = (
        _check_control_characters(question_text),
        _check_length(question_text),
        _check_repeated_spam(question_text),
        _check_dangerous_patterns(question_text),
        _check_pii(question_text),
        _check_duplicate_request(question_text, session_id),
        _check_rate_limit(session_id),
    )

    for passed, reason in checks:
        if not passed:
            return False, reason

    return True, None
