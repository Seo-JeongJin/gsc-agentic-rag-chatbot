"""
mcp_server 통합 테스트 공통 준비 — backend/tests/integration/conftest.py와 같은 방식.

차이점:
- DB 이름을 `rag_test_mcp`로 따로 씀 — backend 테스트(`rag_test`)와 동시에 돌려도 서로 지우지 않게.
- 스키마는 backend/db/schema.sql을 그대로 적용(두 서버가 같은 DB를 공유하는 실제 구조와 동일하게).
- 임베딩 모델(BGE-M3)은 가짜로 — 진짜를 쓰면 모델 로딩에 수십 초가 걸리고, 여기서 확인하려는 건
  "SQL 조건/저장 로직"이지 임베딩 품질이 아님.
"""

import asyncio
import os
from pathlib import Path

import asyncpg
import pytest

import db as mcp_db

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "backend" / "db" / "schema.sql"
TEST_DB_NAME = "rag_test_mcp"
ALL_TABLES = [
    "sessions", "documents", "categories", "questions",
    "category_proposals", "admins", "admin_sessions", "dashboard_insights",
]


def fake_vector(seed: float = 0.1) -> list[float]:
    """BGE-M3와 같은 1024차원 가짜 벡터. 같은 seed면 같은 벡터 → 코사인 유사도 1."""
    return [seed] * 1024


def _db_url(dbname: str) -> str:
    """.env(db.py import 시 로드됨)의 POSTGRES_* 값으로 localhost 주소 생성. 값이 없으면 KeyError → skip 처리됨."""
    host = os.environ.get("TEST_DB_HOST", "localhost")
    port = os.environ.get("TEST_DB_PORT", "5432")
    return (
        f"postgresql://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@{host}:{port}/{dbname}"
    )


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """테스트 세션 시작 시 1회: rag_test_mcp를 새로 만들고 schema.sql 적용. 접속 불가면 통합 테스트 skip."""

    async def setup() -> tuple[str | None, Exception | None]:
        try:
            admin_conn = await asyncpg.connect(_db_url("postgres"), timeout=3)
        except Exception as e:  # noqa: BLE001 — 접속 불가(.env 없음 포함)면 skip
            return None, e
        try:
            await admin_conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)")
            await admin_conn.execute(f"CREATE DATABASE {TEST_DB_NAME}")
        finally:
            await admin_conn.close()
        conn = await asyncpg.connect(_db_url(TEST_DB_NAME))
        try:
            await conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        finally:
            await conn.close()
        return _db_url(TEST_DB_NAME), None

    url, error = asyncio.run(setup())
    if url is None:
        pytest.skip(f"테스트 DB에 접속할 수 없어서 통합 테스트를 건너뜀(docker compose up -d 필요): {error}")
    return url


@pytest.fixture
async def db(test_database_url, monkeypatch):
    """매 테스트마다: MCP 서버의 DB 연결을 rag_test_mcp로 돌리고 테이블을 전부 비움."""
    assert test_database_url.endswith(f"/{TEST_DB_NAME}"), test_database_url  # 개발 DB 보호
    monkeypatch.setattr(mcp_db, "_database_url", lambda: test_database_url)
    monkeypatch.setattr(mcp_db, "_pool", None)
    pool = await mcp_db.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"TRUNCATE {', '.join(ALL_TABLES)} RESTART IDENTITY CASCADE")
    yield pool
    await pool.close()
