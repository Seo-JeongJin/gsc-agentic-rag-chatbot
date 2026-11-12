"""syllable_check.odd_syllables 테스트 (2026-11-12 B25) — 바뀐 글자를 잡고, 평소 답은 안 잡는지."""

from syllable_check import KNOWN_SYLLABLES, odd_syllables


def test_flags_the_corrupted_syllable_from_the_field():
    """실제로 나간 "졡업"(졸업의 마지막 바이트가 바뀐 글자)을 잡아야 함."""
    assert odd_syllables("고등학교를 졡업했거나 다른 대학을 졸업한 경우") == ["졡"]


def test_does_not_flag_a_normal_answer():
    """평소 답(문서 문장 그대로)은 안 걸려야 함 — 걸리면 정상 답이 이전 대화에서 빠짐."""
    answer = ("네. 고등학교를 졸업했거나 다른 대학에 재학·졸업한 경우에도 지원할 수 있습니다. "
              "궁금한 점은 인스타그램 DM이나 학과 사무실로 문의해 주세요! 😊")
    assert odd_syllables(answer) == []


def test_empty_or_missing_text():
    """답이 비었거나 없으면(None) 검사할 게 없음 — 예외 없이 빈 리스트."""
    assert odd_syllables("") == []
    assert odd_syllables(None) == []


def test_list_file_is_loaded():
    """목록 파일이 실제로 읽혔는지 — 비어 있으면 모든 답이 걸려서 숏텀 메모리가 통째로 꺼짐."""
    assert len(KNOWN_SYLLABLES) > 800
    assert "졸" in KNOWN_SYLLABLES and "졡" not in KNOWN_SYLLABLES
