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


def test_widget_bundle_is_served_at_production_path(tmp_path):
    """[배포 0-7] 홈페이지에 넣을 주소를 운영용 /widget.js로 — 전엔 시험 페이지 폴더(/widget-test/widget.js)뿐이라
    "test"가 들어간 주소를 운영에 써야 했고, 시험 페이지를 정리하면 홈페이지 위젯이 깨질 수 있었음.
    "매번 바뀌었는지 확인"(no-cache)을 붙여 위젯을 다시 배포하면 지원자 브라우저가 옛 파일을 계속 쓰지 않게.
    대시보드("/" 캐치올)가 이 주소를 가로채면 안 됨."""
    from main import mount_widget_test

    dashboard = tmp_path / "dashboard"
    dashboard.mkdir()
    (dashboard / "index.html").write_text("<p>dashboard</p>", encoding="utf-8")
    widget = tmp_path / "widget"
    widget.mkdir()
    (widget / "index.html").write_text('<script src="widget.js"></script>', encoding="utf-8")
    (widget / "widget.js").write_text("console.log('widget')", encoding="utf-8")
    app = FastAPI()
    mount_widget_test(app, widget)
    mount_dashboard(app, dashboard)

    res = TestClient(app).get("/widget.js")

    assert res.status_code == 200
    assert res.text == "console.log('widget')"
    assert "javascript" in res.headers["content-type"]
    assert res.headers["cache-control"] == "no-cache"


def test_unchanged_widget_bundle_returns_304(tmp_path):
    """[배포 0-7] 브라우저가 "이 버전 갖고 있어"(If-None-Match: ETag)라고 하면, 안 바뀌었을 땐 파일을 다시 보내지 않고 304만.
    no-cache라 홈페이지 페이지를 옮길 때마다 확인하는데, 매번 16KB를 다시 받지 않게. 파일이 바뀌면 ETag가 달라져 새 파일을 받음."""
    from main import mount_widget_test

    widget = tmp_path / "widget"
    widget.mkdir()
    (widget / "index.html").write_text("<p>test</p>", encoding="utf-8")
    (widget / "widget.js").write_text("console.log('v1')", encoding="utf-8")
    app = FastAPI()
    mount_widget_test(app, widget)
    client = TestClient(app)

    etag = client.get("/widget.js").headers["etag"]
    again = client.get("/widget.js", headers={"If-None-Match": etag})
    (widget / "widget.js").write_text("console.log('v2 — 새로 배포')", encoding="utf-8")
    changed = client.get("/widget.js", headers={"If-None-Match": etag})

    assert again.status_code == 304 and again.content == b""
    assert changed.status_code == 200 and "v2" in changed.text
