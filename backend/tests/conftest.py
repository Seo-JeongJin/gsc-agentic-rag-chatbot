"""
backend 테스트 전체(단위 + 통합)가 공통으로 쓰는 준비/정리 장치.

pytest는 tests/ 폴더의 conftest.py를 자동으로 읽어서, 여기 정의된 fixture를 그 아래
모든 테스트 파일에서 import 없이 쓸 수 있게 해줌.
"""

import pytest

import guardrail


@pytest.fixture(autouse=True)
def force_mock_llm(monkeypatch):
    """모든 테스트에서 LLM은 항상 mock 모드 — .env에 LLM_MODE=vllm이 들어 있는 컴퓨터(학과 컴퓨터 등)에서
    테스트를 돌려도 실제 GPU 서버를 부르지 않게. 실제 vLLM 요청 형식은 test_llm_client.py가 가짜 네트워크로 따로 확인함."""
    monkeypatch.setenv("LLM_MODE", "mock")


@pytest.fixture(autouse=True)
def reset_guardrail_state():
    """매 테스트마다 입력 가드레일의 메모리 기록(rate limit 횟수·마지막 질문)을 초기화.
    가드레일은 모듈 전역 dict에 기록을 쌓기 때문에, 안 비우면 앞 테스트의 요청이 뒤 테스트의
    rate limit에 섞여서 실행 순서에 따라 결과가 달라짐. 단위 테스트(test_guardrail.py)뿐 아니라
    /api/ask를 호출하는 통합 테스트도 가드레일을 거치므로 전체 테스트 공통으로 둠(autouse)."""
    guardrail._request_timestamps.clear()
    guardrail._last_question.clear()
    yield
    guardrail._request_timestamps.clear()
    guardrail._last_question.clear()
