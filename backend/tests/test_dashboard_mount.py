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


def test_widget_test_page_is_served_and_not_swallowed_by_dashboard(tmp_path):
    """위젯 테스트 페이지(/widget-test/)와 위젯 번들이 내려와야 하고, 대시보드("/" 캐치올)가 이 경로를
    가로채면 안 됨 — 학과 컴퓨터에서 Node.js 없이 위젯 화면을 확인하기 위한 페이지(2026-10-28)."""
    from main import mount_widget_test

    dashboard = tmp_path / "dashboard"
    dashboard.mkdir()
    (dashboard / "index.html").write_text("<p>dashboard</p>", encoding="utf-8")
    widget = tmp_path / "widget"
    widget.mkdir()
    (widget / "index.html").write_text('<script src="widget.js"></script>', encoding="utf-8")
    (widget / "widget.js").write_text("console.log('widget')", encoding="utf-8")
    app = FastAPI()

    assert mount_widget_test(app, widget) is True
    mount_dashboard(app, dashboard)
    client = TestClient(app)

    assert "widget.js" in client.get("/widget-test/").text
    assert "widget.js" in client.get("/widget-test").text  # 끝에 / 없이 쳐도 리다이렉트로 열림
    assert client.get("/widget-test/widget.js").status_code == 200
    assert "dashboard" in client.get("/").text


def test_missing_widget_dir_does_not_crash(tmp_path):
    """위젯 테스트 폴더가 없어도(로컬에서 uvicorn 직접 실행 등) 서버는 켜져야 함 — 연결만 건너뜀."""
    from main import mount_widget_test

    assert mount_widget_test(FastAPI(), tmp_path / "없는폴더") is False
