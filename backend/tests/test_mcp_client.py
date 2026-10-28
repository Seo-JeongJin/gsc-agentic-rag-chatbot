"""
MCP 클라이언트(mcp_client.py) 연결 관리 테스트 — 리뷰 15번.

실제 MCP 서버 대신, fastmcp.Client를 흉내 내는 가짜 클래스(FakeClient)로 바꿔 끼워서
"연결이 몇 번째에 성공하는지", "호출이 실패하면 어떻게 되는지"를 마음대로 만들어 확인함.
"""

import pytest
from fastmcp.exceptions import ToolError

import mcp_client


class FakeResult:
    """fastmcp의 call_tool 결과 객체 흉내 — mcp_client는 .data만 꺼내 씀."""

    def __init__(self, data):
        self.data = data


class FakeTool:
    """list_tools()가 돌려주는 도구 정보 흉내 — name/description/input_schema만 쓰임."""

    name = "search_documents"
    description = "문서 검색"
    input_schema = {"type": "object"}


class FakeClient:
    """fastmcp.Client 흉내. 클래스 변수로 "앞으로 몇 번 연결 실패할지", "호출 시 어떤 예외를 낼지"를
    테스트에서 미리 정해두고, 실제로 몇 번 연결/종료됐는지 기록함."""

    connect_failures_left = 0      # 이 횟수만큼 __aenter__가 실패한 뒤 성공
    call_errors: list = []         # call_tool이 차례로 던질 예외들(비면 정상 결과)
    opened = 0                     # 연결 성공 횟수
    closed = 0                     # 연결 종료 횟수

    def __init__(self, url):
        self.url = url

    async def __aenter__(self):
        if FakeClient.connect_failures_left > 0:
            FakeClient.connect_failures_left -= 1
            raise RuntimeError("Client failed to connect: All connection attempts failed")
        FakeClient.opened += 1
        return self

    async def __aexit__(self, *args):
        FakeClient.closed += 1

    async def list_tools(self):
        return [FakeTool()]

    async def call_tool(self, name, arguments):
        if FakeClient.call_errors:
            raise FakeClient.call_errors.pop(0)
        return FakeResult([{"id": 1, "name": name}])


@pytest.fixture(autouse=True)
def fake_mcp_client(monkeypatch):
    """매 테스트마다 FakeClient 상태를 초기화하고, mcp_client가 진짜 Client 대신 이걸 쓰게 함.
    재시도 대기시간은 0초로 — 테스트가 실제로 2초씩 기다리지 않게."""
    FakeClient.connect_failures_left = 0
    FakeClient.call_errors = []
    FakeClient.opened = 0
    FakeClient.closed = 0
    monkeypatch.setattr(mcp_client, "Client", FakeClient)
    monkeypatch.setattr(mcp_client, "MCP_CONNECT_RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(mcp_client, "_client", None)
    monkeypatch.setattr(mcp_client, "_cached_tools", None)


async def test_startup_retries_until_mcp_server_is_ready():
    """[15번 버그 재현] MCP 서버가 아직 안 떠서 처음 몇 번 연결이 실패해도, 재시도하다가 뜨면
    정상 연결돼야 함. 수정 전에는 첫 실패에서 바로 예외 → backend 기동 실패로 컨테이너가 종료됐음
    (docker compose 재빌드할 때마다 실제로 재현됐던 문제)."""
    FakeClient.connect_failures_left = 3

    tools = await mcp_client.connect_and_cache_tools()

    assert FakeClient.opened == 1
    assert tools[0]["function"]["name"] == "search_documents"


async def test_startup_gives_up_after_max_retries():
    """끝까지 연결이 안 되면 무한정 기다리지 않고 정해진 횟수 후 에러로 멈춰야 함 — 설정 오류
    (주소 오타 등)를 빨리 알아챌 수 있게. 이때 docker의 restart 정책이 컨테이너를 다시 띄움."""
    FakeClient.connect_failures_left = mcp_client.MCP_CONNECT_RETRIES

    with pytest.raises(RuntimeError, match="MCP 서버에 연결할 수 없음"):
        await mcp_client.connect_and_cache_tools()


async def test_call_reconnects_once_after_connection_loss():
    """[15번 버그 재현] 실행 중에 MCP 서버가 재시작되면 기존 연결이 끊김. 수정 전에는 backend를
    재시작하기 전까지 모든 질문이 실패했음. 이제는 연결 오류가 나면 한 번 다시 연결해서 재시도해야 함."""
    await mcp_client.connect_and_cache_tools()
    FakeClient.call_errors = [RuntimeError("connection closed")]

    result = await mcp_client.call_tool("search_documents", {"query": "q"})

    assert result == [{"id": 1, "name": "search_documents"}]
    assert FakeClient.opened == 2   # 처음 연결 + 재연결
    assert FakeClient.closed == 1   # 끊긴 옛 연결은 정리


async def test_tool_error_is_not_retried():
    """도구 자체가 에러를 낸 경우(ToolError — 예: 잘못된 인자)는 연결 문제가 아니라서 재연결해도
    똑같이 실패함. 쓸데없이 재연결하지 말고 그대로 에러를 올려보내야 함."""
    await mcp_client.connect_and_cache_tools()
    FakeClient.call_errors = [ToolError("invalid arguments")]

    with pytest.raises(ToolError):
        await mcp_client.call_tool("search_documents", {"query": "q"})
    assert FakeClient.opened == 1


# ----- 내부 임베딩 API 주소 (리뷰 1번, 3단계) -----


def test_internal_api_url_is_derived_from_mcp_url(monkeypatch):
    """MCP 서버 주소(…/mcp)에서 같은 서버의 내부 API 주소(…/internal/embed)를 만들어야 함 — 주소 설정을
    하나(MCP_SERVER_URL)만 관리하기 위함. 로컬(127.0.0.1)과 Docker(서비스명 mcp_server) 둘 다 확인."""
    monkeypatch.setattr(mcp_client, "MCP_SERVER_URL", "http://mcp_server:9000/mcp")
    assert mcp_client._internal_api_url("/internal/embed") == "http://mcp_server:9000/internal/embed"

    monkeypatch.setattr(mcp_client, "MCP_SERVER_URL", "http://127.0.0.1:9000/mcp")
    assert mcp_client._internal_api_url("/internal/embed") == "http://127.0.0.1:9000/internal/embed"
