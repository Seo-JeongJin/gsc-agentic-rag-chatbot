"""
FastAPI(backend) 쪽 DB 연결 풀.

mcp_server/db.py와 내용이 비슷하지만 별개 파일 — 실제 배포 시 FastAPI와 MCP서버는
다른 컨테이너라 코드를 공유할 수 없어서 의도적으로 중복되어 있음.
"""

import json
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv
from pgvector.asyncpg import register_vector

# 비밀번호를 코드에 직접 쓰지 않기 위해 프로젝트 루트의 .env에서 접속 정보를 읽음.
# - 로컬에서 직접 실행(uvicorn main:app)할 때: 이 줄이 루트 .env를 읽어서 POSTGRES_* 값을 채움
# - Docker Compose 안에서 실행될 때: 컨테이너엔 .env 파일이 없어서 이 줄은 아무 일도 안 하고,
#   대신 docker-compose.yml이 넣어준 DATABASE_URL 환경변수를 그대로 씀
# 이미 설정된 환경변수는 덮어쓰지 않음(load_dotenv 기본 동작) — 컨테이너 설정이 항상 우선.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

_pool: asyncpg.Pool | None = None


def _database_url() -> str:
    """DB 접속 주소를 결정. DATABASE_URL이 명시돼 있으면(Docker Compose) 그걸 쓰고,
    없으면(로컬 직접 실행) .env의 POSTGRES_* 값으로 localhost 접속 주소를 조립함.

    import 시점이 아니라 "처음 연결할 때" 호출되는 함수로 둔 이유(2026-10-28 버그 수정):
    전엔 파일 맨 위에서 바로 os.environ['POSTGRES_USER']를 읽어서, .env가 없는 환경(새로 clone한 컴퓨터,
    CI)에서는 DB를 안 쓰는 코드·테스트까지 import 단계에서 원인 모를 KeyError로 죽었음.
    이제는 DB가 실제로 필요할 때만, 무엇을 해야 하는지 알려주는 에러를 냄."""
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
    await register_vector(conn)
    # asyncpg는 기본적으로 Python dict ↔ jsonb를 자동 변환 안 해줘서, 직접 인코더/디코더를
    # 등록해야 함 — 이게 없으면 dashboard_insights.content(JSONB)에 dict를 그냥 넣으려 할 때 에러.
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(_database_url(), init=_init_connection)
    return _pool
