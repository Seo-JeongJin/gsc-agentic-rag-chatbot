"""
관리자 대시보드 정적파일 연결(main.py의 mount_dashboard) 테스트 — 리뷰 4번.

대시보드 빌드 결과물(static_dashboard/)은 git에 안 올리기 때문에, GitHub에서 새로 받은
컴퓨터(학과 컴퓨터 등)에는 이 폴더가 없음. 그때도 챗봇 API 서버는 정상으로 켜져야 함.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from main import mount_dashboard


def test_missing_dashboard_dir_does_not_crash(tmp_path):
    """[4번 버그 재현] 빌드 폴더가 없으면 수정 전에는 StaticFiles가 서버 시작 단계에서
    에러를 내서 /api/*까지 통째로 안 켜졌음. 이제는 연결만 건너뛰고(False 반환) 서버는 계속 동작해야 함."""
    app = FastAPI()

    assert mount_dashboard(app, tmp_path / "없는폴더") is False


def test_existing_dashboard_dir_is_served(tmp_path):
    """빌드 폴더가 있으면 지금처럼 "/"에서 index.html을 내려줘야 함(같은 출처 서빙 — 쿠키 인증 유지)."""
    (tmp_path / "index.html").write_text("<p>dashboard</p>", encoding="utf-8")
    app = FastAPI()

    assert mount_dashboard(app, tmp_path) is True
    res = TestClient(app).get("/")
    assert res.status_code == 200
    assert "dashboard" in res.text
