"""
카테고리 시드 SQL(backend/db/seed_categories.sql) 테스트 — 리뷰 6번.

새 컴퓨터(학과 컴퓨터)에서 DB를 처음 만들면 categories가 비어 있어서 카테고리 분류가 전부 실패했음.
이 SQL 파일이 docker-compose로 DB 최초 생성 시 자동 적용되고, 기존 DB에 손으로 다시 실행해도 안전해야 함.
"""

from pathlib import Path

SEED_PATH = Path(__file__).resolve().parents[2] / "db" / "seed_categories.sql"


async def _apply_seed(db) -> None:
    """시드 파일을 그대로 실행(실제로 docker-entrypoint-initdb.d가 하는 것과 같음)."""
    async with db.acquire() as conn:
        await conn.execute(SEED_PATH.read_text(encoding="utf-8"))


async def test_seed_inserts_confirmed_31_categories(db):
    """[6번] 확정된 카테고리 31행(7개 대분류)이 들어가고, 폴백 대상인 기타>기타가 반드시 포함돼야 함
    — 기타>기타가 없으면 category_tagging.py의 폴백이 category_id를 못 찾아서 분류가 NULL로 남음."""
    await db.execute("TRUNCATE categories RESTART IDENTITY CASCADE")  # 테스트용 최소 카테고리 제거 후 시드만

    await _apply_seed(db)

    assert await db.fetchval("SELECT COUNT(*) FROM categories") == 31
    assert await db.fetchval("SELECT COUNT(DISTINCT major) FROM categories") == 7
    assert await db.fetchval("SELECT 1 FROM categories WHERE major='기타' AND minor='기타'") == 1


async def test_seed_can_be_applied_twice_safely(db):
    """시드를 이미 카테고리가 있는 DB에 다시 실행해도 중복 행이나 에러가 없어야 함(ON CONFLICT DO NOTHING)
    — 개발 DB처럼 이미 채워진 DB에 손으로 실행하는 경우 대비."""
    await db.execute("TRUNCATE categories RESTART IDENTITY CASCADE")

    await _apply_seed(db)
    await _apply_seed(db)

    assert await db.fetchval("SELECT COUNT(*) FROM categories") == 31
