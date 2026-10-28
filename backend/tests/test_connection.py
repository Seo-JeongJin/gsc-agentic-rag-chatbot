"""
DB 접속 주소 결정(db/connection.py의 _database_url) 테스트.

2026-10-28 발견 버그: 접속 주소를 "파일을 import하는 순간" 만들면서 os.environ['POSTGRES_USER']를
바로 읽어서, .env가 없는 환경(새로 clone한 학과 컴퓨터, CI)에서는 DB를 안 쓰는 단위 테스트까지
import 단계에서 KeyError로 전부 실패했음. 이 노트북엔 .env가 있어서 기존 테스트로는 안 잡혔음.
"""

import pytest

import db.connection as connection


def test_uses_database_url_when_given(monkeypatch):
    """DATABASE_URL이 있으면(Docker Compose 안) 그대로 씀 — POSTGRES_* 값은 보지도 않음."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db:5432/rag")
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        monkeypatch.delenv(key, raising=False)

    assert connection._database_url() == "postgresql://u:p@db:5432/rag"


def test_builds_localhost_url_from_postgres_vars(monkeypatch):
    """DATABASE_URL이 없으면(로컬 직접 실행) .env의 POSTGRES_* 값으로 localhost 주소를 만듦."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p")
    monkeypatch.setenv("POSTGRES_DB", "rag")

    assert connection._database_url() == "postgresql://u:p@localhost:5432/rag"


def test_missing_env_gives_clear_error_instead_of_keyerror(monkeypatch):
    """[버그 재현] 값이 하나도 없으면, 원인을 알 수 없는 KeyError 대신 ".env를 만들라"는 안내가 담긴
    에러가 나야 함. 그리고 이 에러는 import할 때가 아니라 DB 주소가 실제로 필요할 때(이 함수 호출 시)만 남
    — 이 테스트 파일이 import에 성공해서 여기까지 왔다는 것 자체가 "import는 안 깨진다"는 확인."""
    for key in ("DATABASE_URL", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(RuntimeError, match=r"\.env"):
        connection._database_url()
