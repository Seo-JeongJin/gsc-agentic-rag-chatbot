"""
Swagger(/docs) 명세서에 API마다 응답 모양이 나오는지 확인하는 테스트.

FastAPI는 응답 모델(response_model)이 붙은 API만 명세서에 "이런 칸이 온다"를 적어 줌.
전엔 /api/ask 하나만 모델이 있어서, 나머지 15개는 Swagger에 응답이 빈 객체({})로만 보였음
(2026-11-26 — 팀원 실습용으로 Swagger를 API 명세서로 쓰려고 응답 모델을 붙임).
"""

import pytest

from main import app

# 명세서에 응답 모양이 나와야 하는 JSON API 16개 (경로, 메서드)
JSON_APIS = [
    ("/api/ask", "post"),
    ("/api/rate", "post"),
    ("/api/reset_conversation", "post"),
    ("/admin/login", "post"),
    ("/admin/logout", "post"),
    ("/admin/me", "get"),
    ("/admin/questions", "get"),
    ("/admin/stats/categories", "get"),
    ("/admin/stats/overview", "get"),
    ("/admin/analyze", "post"),
    ("/admin/insights", "get"),
    ("/admin/insights/history", "get"),
    ("/admin/category-suggestions/analyze", "post"),
    ("/admin/category-suggestions", "get"),
    ("/admin/category-suggestions/{proposal_id}/approve", "post"),
    ("/admin/category-suggestions/{proposal_id}/reject", "post"),
]


def _refers_to_a_model(schema: dict) -> bool:
    """응답 스키마가 이름 붙은 모델(#/components/schemas/…)을 가리키는지.
    목록(list[모델])이면 items 안을, "모델 또는 null"이면 anyOf 안을 봄. 모델이 없으면 {}나 {"type": "object"}뿐임."""
    if "$ref" in schema:
        return True
    if schema.get("type") == "array":
        return _refers_to_a_model(schema.get("items", {}))
    return any(_refers_to_a_model(s) for s in schema.get("anyOf", []))


@pytest.mark.parametrize("path,method", JSON_APIS)
def test_every_json_api_documents_its_response_shape(path, method):
    """API 16개 모두 성공 응답(200)의 스키마가 모델을 가리켜야 Swagger에서 칸 이름·타입이 보임."""
    spec = app.openapi()
    response_schema = spec["paths"][path][method]["responses"]["200"]["content"]["application/json"]["schema"]
    assert _refers_to_a_model(response_schema), f"{method.upper()} {path}: {response_schema}"
