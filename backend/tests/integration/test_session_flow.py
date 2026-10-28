"""
세션 처리(db/session_service.py) 통합 테스트 — 리뷰 10번 + 재방문 기준(2026-10-28 결정: 30분).
"""

import asyncio

from db.session_service import process_session


async def _is_returning(db, session_id: str) -> bool:
    return await db.fetchval("SELECT is_returning FROM sessions WHERE session_id=$1", session_id)


async def test_first_visit_is_not_returning(db):
    """처음 보는 세션은 행이 새로 생기고 재방문이 아님."""
    assert await process_session("s1") is False
    assert await _is_returning(db, "s1") is False


async def test_second_question_within_30_minutes_is_not_returning(db):
    """[10번 버그 재현] 같은 대화창에서 연달아 질문한 건 재방문이 아님. 수정 전에는 두 번째 질문만
    해도 바로 is_returning=TRUE가 돼서, 대시보드 "재방문율"이 사실상 "질문 2개 이상 한 세션 비율"이었음."""
    await process_session("s1")

    assert await process_session("s1") is False
    assert await _is_returning(db, "s1") is False


async def test_question_after_30_minutes_is_returning(db):
    """마지막 활동 후 30분 넘게 지나서 다시 질문하면 재방문(웹 분석 표준 기준).
    실제로 30분을 기다릴 수 없으니, DB의 마지막 활동 시각을 31분 전으로 직접 돌려놓고 확인."""
    await process_session("s1")
    await db.execute(
        "UPDATE sessions SET last_seen_at = now() - interval '31 minutes' WHERE session_id='s1'"
    )

    assert await process_session("s1") is True
    assert await _is_returning(db, "s1") is True


async def test_returning_flag_stays_true_after_quick_followup(db):
    """한 번 재방문으로 판정된 세션은, 그 뒤 30분 안에 질문을 이어가도 다시 FALSE로 돌아가면 안 됨
    (is_returning은 "이 사람이 재방문한 적 있는지"라는 누적 플래그)."""
    await process_session("s1")
    await db.execute(
        "UPDATE sessions SET last_seen_at = now() - interval '31 minutes' WHERE session_id='s1'"
    )
    await process_session("s1")

    await process_session("s1")  # 바로 이어서 한 질문

    assert await _is_returning(db, "s1") is True


async def test_concurrent_first_requests_do_not_crash(db):
    """[10번 버그 재현] 새 세션으로 요청 2개가 동시에 오면(이중 클릭, 탭 2개 등), 수정 전엔
    "SELECT로 없음 확인 → INSERT"를 둘 다 해서 하나가 중복키 에러(500)로 실패했음.
    한 문장짜리 upsert로 바꾸면 둘 다 성공하고 행은 하나만 남아야 함."""
    await asyncio.gather(process_session("s1"), process_session("s1"))

    assert await db.fetchval("SELECT COUNT(*) FROM sessions WHERE session_id='s1'") == 1
