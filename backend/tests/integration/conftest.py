"""
통합 테스트 공통 준비 — 실제 PostgreSQL(테스트 전용 DB `rag_test`)에 연결해서 API를 끝에서 끝까지 호출.

단위 테스트와 다른 점: 함수 하나만 떼어보는 게 아니라 "HTTP 요청 → FastAPI → DB 쓰기/읽기"가
실제로 이어지는지 확인함(지금까지 curl로 손으로 하던 확인을 자동화).

안전장치 3가지:
1. 개발 DB(`rag`)가 아니라 별도 DB(`rag_test`)를 매번 새로 만들어서 씀 — 개발 데이터(색인된 문서 73청크,
   카테고리 등)를 절대 건드리지 않기 위함. 테이블을 비우기 직전에 DB 이름을 한 번 더 확인함.
2. DB에 접속할 수 없으면(Docker가 꺼져 있는 등) 통합 테스트만 skip — 단위 테스트는 그대로 돌아감.
3. MCP 서버는 가짜로 바꿔치기 — 통합 테스트의 대상은 "API ↔ DB" 구간이고, 검색 품질이 아님.
"""

import asyncio
import os
from pathlib import Path

import asyncpg
import httpx
import pytest

import db.connection as connection

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "db" / "schema.sql"
TEST_DB_NAME = "rag_test"

# schema.sql의 테이블 전부 — 매 테스트 시작 전에 비움
ALL_TABLES = [
    "sessions", "documents", "categories", "questions",
    "category_proposals", "admins", "admin_sessions", "dashboard_insights",
]

# 실제 31행 대신 쓰는 최소 카테고리 — 가짜 분류 LLM(category_tagging.py의 mock)이 고르는 값과
# 폴백 대상(기타>기타)만 있으면 흐름 테스트에 충분함
SEED_CATEGORIES = [
    ("입학전형", "일반"),
    ("입학전형", "원서접수·전형료"),
    ("캠퍼스생활·비용", "기숙사"),
    ("학과소개", "일반"),
    ("기타", "기타"),
]

# 가짜 MCP가 돌려줄 검색 결과 — 실제 search_documents 반환 형식과 동일
FAKE_SEARCH_RESULT = [
    {"id": 1, "chunk_text": "기숙사 신청은 입학 후 학생생활관 홈페이지에서 합니다.", "source": "faq.md", "similarity": 0.65}
]


def _db_url(dbname: str) -> str:
    """POSTGRES_* 값(.env, db.connection import 시 로드됨)으로 localhost 접속 주소를 만듦.
    호스트/포트는 기본 localhost:5432(docker-compose가 호스트에 열어둔 포트), 필요하면 환경변수로 변경.
    .env가 없으면 KeyError — 아래 test_database_url이 이걸 잡아서 통합 테스트를 skip 처리함."""
    host = os.environ.get("TEST_DB_HOST", "localhost")
    port = os.environ.get("TEST_DB_PORT", "5432")
    return (
        f"postgresql://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@{host}:{port}/{dbname}"
    )


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """테스트 세션 시작 시 1회: rag_test DB를 지우고 새로 만든 뒤 schema.sql을 적용.

    매번 새로 만드는 이유: schema.sql이 바뀌어도(컬럼 추가 등) 테스트는 항상 최신 스키마로 돌아가게.
    sync fixture + asyncio.run()을 쓰는 이유: 이 준비 작업은 테스트마다 새로 만들어지는 이벤트 루프와
    상관없이 딱 한 번만 실행되면 되고, 연결도 여기서 바로 닫으므로 별도 루프에서 돌려도 안전함."""

    async def setup() -> tuple[str | None, Exception | None]:
        try:
            # 관리용 기본 DB(postgres)에 접속해서 DB 자체를 만들고 지움.
            # .env가 없어서 _db_url()이 KeyError를 내는 경우도 여기서 같이 잡아서 skip(에러로 끝나지 않게).
            admin_conn = await asyncpg.connect(_db_url("postgres"), timeout=3)
        except Exception as e:  # noqa: BLE001 — 어떤 이유든 접속 불가면 skip 처리
            return None, e
        try:
            # WITH (FORCE): 이전 테스트 실행에서 남은 연결이 있어도 강제로 끊고 지움(PostgreSQL 13+)
            await admin_conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)")
            await admin_conn.execute(f"CREATE DATABASE {TEST_DB_NAME}")
        finally:
            await admin_conn.close()

        conn = await asyncpg.connect(_db_url(TEST_DB_NAME))
        try:
            # 인자 없이 execute하면 여러 SQL문을 한 번에 실행 가능 — schema.sql 파일 전체를 그대로 적용
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
    """매 테스트마다: 앱의 DB 연결을 rag_test로 돌리고, 테이블을 비운 뒤 최소 카테고리만 넣음.

    db.connection의 접속 주소 결정 함수(_database_url)를 바꿔치기하는 방식 — 앱 코드의 모든 DB 함수가
    get_pool()을 통해 연결하고, get_pool()은 처음 연결할 때 이 함수로 주소를 정하므로 여기만 바꾸면 전부
    테스트 DB를 보게 됨. 커넥션 풀(_pool)도 테스트마다 새로 만들고 끝나면 닫음(pytest-asyncio가 테스트마다
    이벤트 루프를 새로 만들어서, 풀을 재사용하면 "다른 루프의 연결"이라 에러가 남)."""
    # 안전장치: 혹시라도 개발 DB를 가리키고 있으면 TRUNCATE 전에 멈춤
    assert test_database_url.endswith(f"/{TEST_DB_NAME}"), test_database_url

    monkeypatch.setattr(connection, "_database_url", lambda: test_database_url)
    monkeypatch.setattr(connection, "_pool", None)
    pool = await connection.get_pool()

    async with pool.acquire() as conn:
        # RESTART IDENTITY: id 번호도 1부터 다시 — 테스트 안에서 id 값을 예측 가능하게
        await conn.execute(f"TRUNCATE {', '.join(ALL_TABLES)} RESTART IDENTITY CASCADE")
        await conn.executemany("INSERT INTO categories (major, minor) VALUES ($1, $2)", SEED_CATEGORIES)

    yield pool

    await pool.close()


@pytest.fixture
async def client(db, monkeypatch):
    """FastAPI 앱에 직접 HTTP 요청을 보내는 테스트용 클라이언트(서버를 띄우지 않음).

    httpx의 ASGITransport는 네트워크 없이 앱 함수를 직접 호출함. 이 방식은 앱의 lifespan(시작 시
    MCP 서버 연결)을 실행하지 않으므로, 대신 MCP 클라이언트의 함수들을 가짜로 바꿔 끼움:
    도구목록/도구호출(Agent 루프용) + 내부 임베딩 API(정상 답변 후 백그라운드로 호출됨). 임베딩까지 가짜로
    하지 않으면, 이 노트북에 MCP 컨테이너가 떠 있는지에 따라 테스트가 실제 네트워크를 타거나 안 타서 결과가 달라짐.
    쿠키도 클라이언트가 자동으로 기억하므로 관리자 로그인 → 이후 요청 흐름도 그대로 테스트 가능."""
    import agent_loop
    import main
    import mcp_client

    async def fake_call_tool(name, arguments):
        return FAKE_SEARCH_RESULT

    async def fake_embed_text(text):
        return [0.1] * 1024

    monkeypatch.setattr(agent_loop.mcp_client, "get_cached_tools", lambda: [])
    monkeypatch.setattr(agent_loop.mcp_client, "call_tool", fake_call_tool)
    monkeypatch.setattr(mcp_client, "embed_text", fake_embed_text)

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ----- 테스트 데이터를 DB에 직접 넣는 도우미 (API를 거치지 않고 특정 상태를 빠르게 만들 때) -----


async def insert_question(
    pool,
    question_text: str,
    session_id: str = "seed-session",
    needs_review: bool = False,
    review_reason: str | None = None,
    category: tuple[str, str] | None = None,
    answer_text: str = "테스트 답변",
) -> int:
    """questions에 한 행을 직접 넣고 id를 반환. 세션 행이 없으면 같이 만듦(FK 때문에 필수).
    category를 주면 그 카테고리 id로 바로 분류된 상태로 넣음(예: 기타>기타 질문 여러 개 만들기)."""
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO sessions (session_id) VALUES ($1) ON CONFLICT DO NOTHING", session_id
        )
        category_id = None
        if category is not None:
            category_id = await conn.fetchval(
                "SELECT id FROM categories WHERE major=$1 AND minor=$2", *category
            )
        return await conn.fetchval(
            """
            INSERT INTO questions (session_id, question_text, answer_text, needs_review, review_reason, category_id)
            VALUES ($1, $2, $3, $4, $5, $6) RETURNING id
            """,
            session_id, question_text, answer_text, needs_review, review_reason, category_id,
        )
