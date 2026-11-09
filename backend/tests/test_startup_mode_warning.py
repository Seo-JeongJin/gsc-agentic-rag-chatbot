"""
서버 시작 시 LLM 모드 경고(main.py의 warn_if_mock_mode) 테스트 — 2026-11-09 배포 전 점검.

LLM_MODE=mock이면 지원자에게 "(가짜 답변) …"이 나가므로, 배포 서버가 실수로 mock으로 켜졌을 때 로그에서 바로 보이게 경고를 남김.
(docker-compose.yml은 .env에 LLM_MODE가 없으면 아예 안 켜지게 막고, 이 경고는 mock을 직접 적은 경우까지 잡는 두 번째 안전장치)
"""

import logging

from main import warn_if_mock_mode


def test_mock_mode_logs_warning(monkeypatch, caplog):
    """mock이면 경고 한 줄 — 서버는 그대로 켜짐(집 개발·테스트에선 정상 모드라서)."""
    monkeypatch.setenv("LLM_MODE", "mock")

    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        warn_if_mock_mode()

    assert any("가짜 답변 모드" in r.getMessage() for r in caplog.records)


def test_vllm_mode_logs_no_warning(monkeypatch, caplog):
    """실제 GPU 서버 모드(vllm)면 경고 없음 — 운영 로그에 불필요한 경고가 쌓이지 않게."""
    monkeypatch.setenv("LLM_MODE", "vllm")

    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        warn_if_mock_mode()

    assert not any("가짜 답변 모드" in r.getMessage() for r in caplog.records)


def test_missing_mode_counts_as_mock(monkeypatch, caplog):
    """LLM_MODE가 아예 없으면 llm_client가 mock으로 동작하므로 경고해야 함(compose 밖에서 직접 실행할 때)."""
    monkeypatch.delenv("LLM_MODE", raising=False)

    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        warn_if_mock_mode()

    assert any("가짜 답변 모드" in r.getMessage() for r in caplog.records)
