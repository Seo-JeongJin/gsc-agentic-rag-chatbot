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

# DATABASE_URL이 있으면(Docker Compose) 그대로, 없으면(로컬) .env의 POSTGRES_* 값으로 localhost 주소 조립
DATABASE_URL = os.environ.get("DATABASE_URL") or (
    f"postgresql://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
    f"@localhost:5432/{os.environ['POSTGRES_DB']}"
)

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    """각 커넥션이 pgvector의 vector 타입을 이해하게 등록.
    이게 없으면 asyncpg가 vector 컬럼을 파이썬 리스트로 못 주고받음."""
    await register_vector(conn)


async def get_pool() -> asyncpg.Pool:
    """커넥션 풀을 한 번만 만들고 재사용 — 매 요청마다 새로 연결하면 느리고 자원 낭비."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, init=_init_connection)
    return _pool
