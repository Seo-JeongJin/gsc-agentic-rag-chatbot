"""
실습용 가짜 데이터 SQL(backend/db/seed_demo.sql) 테스트 — 2026-11-26.

팀원 실습용 빈 DB에 넣으면 대시보드 API가 볼 만한 JSON을 돌려주는지, 그리고 질문이 이미 있는 DB(운영·개발)에
실수로 넣으면 아무것도 들어가지 않고 멈추는지 확인. 시드 파일을 docker-compose가 아니라 손으로 넣는 것과 같은 방식(파일 통째로 실행)으로 돌림.
"""

from pathlib import Path

import asyncpg
import pytest

from db.admin_service import create_admin

DB_DIR = Path(__file__).resolve().parents[2] / "db"


async def _run_sql_file(db, name: str) -> None:
    """SQL 파일을 통째로 실행. 실패하면 열린 트랜잭션을 되돌려 연결을 깨끗이 돌려줌(seed_demo.sql은 BEGIN…COMMIT 한 덩어리)."""
    async with db.acquire() as conn:
        try:
            await conn.execute((DB_DIR / name).read_text(encoding="utf-8"))
        except asyncpg.PostgresError:
            await conn.execute("ROLLBACK")
            raise


@pytest.fixture
async def demo_db(db):
    """실습 PC의 새 DB와 같은 상태(카테고리 31개만 있음)를 만든 뒤 가짜 데이터를 넣음."""
    await db.execute("TRUNCATE categories RESTART IDENTITY CASCADE")   # 테스트용 최소 카테고리 대신 실제 31개
    await _run_sql_file(db, "seed_categories.sql")
    await _run_sql_file(db, "seed_demo.sql")
    return db


async def test_seed_inserts_the_planned_mix(demo_db):
    """파일 맨 위 설명에 적은 구성 그대로 들어가는지 — 숫자가 어긋나면 실습 지침서의 예시와도 어긋남."""
    q = "SELECT COUNT(*) FROM questions"
    assert await demo_db.fetchval("SELECT COUNT(*) FROM sessions") == 25
    assert await demo_db.fetchval("SELECT COUNT(*) FROM sessions WHERE is_returning") == 6
    assert await demo_db.fetchval(q) == 100
    assert await demo_db.fetchval(q + " WHERE needs_review") == 12
    assert await demo_db.fetchval(q + " WHERE category_id IS NULL") == 5
    assert await demo_db.fetchval(
        q + " WHERE category_id = (SELECT id FROM categories WHERE major='기타' AND minor='기타')") == 8
    assert await demo_db.fetchval(q + " WHERE guardrail_passed = FALSE") == 5
    assert await demo_db.fetchval(q + " WHERE user_rating IS NOT NULL") == 30
    # 카테고리 이름 오타가 있으면 LEFT JOIN이라 조용히 NULL이 됨 → 분류 안 된 질문이 일부러 넣은 5개(인사·잡담)뿐인지 확인
    assert await demo_db.fetchval(q + " WHERE category_id IS NULL AND tool_call_count > 0") == 0
    # id 순서 = 시간 순서(실제 서비스처럼) — 질문 로그를 id로 정렬해도 들어온 순서대로 보이게
    assert await demo_db.fetchval(
        "SELECT bool_and(created_at >= prev) FROM (SELECT created_at, lag(created_at) OVER (ORDER BY id) AS prev FROM questions) t"
    )


async def test_seed_refuses_a_database_that_already_has_questions(demo_db):
    """두 번째로 넣으면(= 질문이 있는 DB) 오류로 멈추고, 아무것도 더 들어가지 않아야 함 — 운영·개발 DB 보호."""
    with pytest.raises(asyncpg.RaiseError, match="질문이 이미 있는 DB"):
        await _run_sql_file(demo_db, "seed_demo.sql")

    assert await demo_db.fetchval("SELECT COUNT(*) FROM questions") == 100
    assert await demo_db.fetchval("SELECT COUNT(*) FROM sessions") == 25


async def test_dashboard_apis_return_data_after_seeding(demo_db, client):
    """가짜 데이터를 넣은 뒤 관리자 API가 빈 목록·0이 아닌 실습할 거리를 돌려주는지."""
    await create_admin("admin", "test-password")
    await client.post("/admin/login", json={"username": "admin", "password": "test-password"})

    assert len((await client.get("/admin/questions", params={"limit": 100})).json()) == 100
    assert len((await client.get("/admin/questions", params={"needs_review": "true"})).json()) == 12

    overview = (await client.get("/admin/stats/overview")).json()
    assert overview["total_questions"] == 100 and overview["returning_rate_percent"] == 24.0
    assert overview["needs_review_by_reason"] == {"insufficient_info": 10, "guardrail_failed": 2}

    analyzed = (await client.post("/admin/analyze")).json()
    assert analyzed["content"]["new_since_last_count"] == 12

    suggestion = (await client.post("/admin/category-suggestions/analyze")).json()
    assert suggestion["status"] == "proposed"   # 기타>기타 포트폴리오 질문 8개 → 가짜 LLM이 제안을 만듦
