"""
MCP 서버 전용 DB 연결 풀.

backend/db/connection.py와는 별개의 파일 — 실제 배포 시 MCP 서버는 FastAPI와 다른
컨테이너로 뜨기 때문에 코드를 공유할 수 없어서, 연결 설정이 의도적으로 중복되어 있음.
"""

import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv
from pgvector.asyncpg import register_vector

# backend/db/connection.py와 같은 방식 — 비밀번호를 코드에 쓰지 않고 프로젝트 루트 .env에서 읽음.
# 로컬 직접 실행 시엔 루트 .env를 읽고, 컨테이너 안에서는 .env가 없으니 아무 일도 안 함
# (docker-compose.yml이 준 DATABASE_URL이 그대로 쓰임).
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

_pool: asyncpg.Pool | None = None


def _database_url() -> str:
    """DB 접속 주소 결정 — backend/db/connection.py의 _database_url과 같은 규칙.
    DATABASE_URL이 있으면(Docker Compose) 그대로, 없으면(로컬) .env의 POSTGRES_* 값으로 localhost 주소 조립.
    import 시점이 아니라 처음 연결할 때 호출 — .env가 없는 환경에서 import만으로 KeyError가 나던 버그 수정(2026-10-28)."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    missing = [k for k in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB") if not os.environ.get(k)]
    if missing:
        raise RuntimeError(
            f"DB 접속 정보가 없음({', '.join(missing)}) — 프로젝트 루트에서 `cp .env.example .env` 후 값을 채우거나, "
            "DATABASE_URL 환경변수를 지정할 것"
        )
    return (
        f"postgresql://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@localhost:5432/{os.environ['POSTGRES_DB']}"
    )


async def _init_connection(conn: asyncpg.Connection) -> None:
    """각 커넥션이 pgvector의 vector 타입을 이해하게 등록.
    이게 없으면 asyncpg가 vector 컬럼을 파이썬 리스트로 못 주고받음."""
    await register_vector(conn)


async def get_pool() -> asyncpg.Pool:
    """커넥션 풀을 한 번만 만들고 재사용 — 매 요청마다 새로 연결하면 느리고 자원 낭비."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(_database_url(), init=_init_connection)
    return _pool
