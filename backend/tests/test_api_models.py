"""
API 요청 형식(main.py의 Pydantic 모델) 검증 테스트.

FastAPI는 요청 body를 이 모델로 먼저 검사하고, 형식이 틀리면 엔드포인트 코드(=DB 접근)까지
가지 않고 바로 422로 돌려보냄. 그래서 "잘못된 요청은 DB까지 안 간다"는 걸 모델 단위로 확인.
(없는 질문 id → 404, 남의 질문 평가 차단은 DB가 필요해서 2단계 통합 테스트에서 확인)
"""

import pytest
from pydantic import ValidationError

from main import RateRequest


def test_rate_request_accepts_rating_1_to_5_with_session():
    """정상 범위(1~5) 평가 + session_id가 있으면 그대로 통과 — 위젯이 실제로 보내는 형태(👎=1, 👍=5)."""
    for rating in (1, 5):
        req = RateRequest(question_id=1, rating=rating, session_id="s1")
        assert req.rating == rating


@pytest.mark.parametrize("rating", [0, 6, -1, 100])
def test_rate_request_rejects_out_of_range_rating(rating):
    """[9번 버그 재현] 1~5 밖의 평가는 모델 단계에서 거절돼야 함.
    수정 전에는 아무 정수나 통과해서 DB의 CHECK 제약에 걸려 500(서버 오류)이 났음 —
    잘못된 요청은 서버 고장이 아니라 422(요청이 잘못됨)로 응답해야 맞음."""
    with pytest.raises(ValidationError):
        RateRequest(question_id=1, rating=rating, session_id="s1")


def test_rate_request_requires_session_id():
    """[9번 버그 재현] 평가 요청에 session_id가 반드시 있어야 함 — 이 값으로 "질문한 본인 세션인지"
    (소유 확인)를 DB에서 대조하기 때문. 없으면 남의 질문 번호만으로 평가를 조작할 수 있음."""
    with pytest.raises(ValidationError):
        RateRequest(question_id=1, rating=5)
