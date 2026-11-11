"""
배포 설정 단위 테스트 (2026-11-11 배포 0-6) — CORS 허용 사이트 읽기, 관리자 비밀번호 길이 검사.
"""

import create_admin_cli
from main import cors_allow_origins


def test_cors_allows_all_when_not_set(monkeypatch):
    """CORS_ALLOW_ORIGINS가 없으면 지금처럼 전체 허용 — 학과 PC 실측·집 개발에서 위젯 시험 페이지가 그대로 동작하게."""
    monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
    assert cors_allow_origins() == ["*"]


def test_cors_reads_comma_separated_sites(monkeypatch):
    """[CORS 재현] 전엔 모든 사이트 허용(*)으로 고정이라, 다른 사이트가 자기 페이지에 우리 챗봇을 붙여 GPU를 쓸 수 있었음.
    배포 서버 .env에 학과 홈페이지만 적으면 그 사이트만 허용 — 쉼표로 여러 개, 앞뒤 공백·빈 칸은 무시."""
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", " https://gsc.yju.ac.kr , https://www.yju.ac.kr ,")
    assert cors_allow_origins() == ["https://gsc.yju.ac.kr", "https://www.yju.ac.kr"]


def test_short_admin_password_is_rejected():
    """[⑤] 관리자 비밀번호가 10자 미만이면 계정을 만들지 않음 — 배포 서버에서 실수로 "test1234" 같은 계정을 만드는 걸 막음
    (대시보드는 학교 IP만 열리지만, 학교 안에서의 비밀번호 대입까지 막기 위해)."""
    assert create_admin_cli.password_problem("test1234") is not None
    assert create_admin_cli.password_problem("Gsc-chatbot-2026!") is None
