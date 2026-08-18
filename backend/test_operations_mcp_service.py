import json

import pytest

from operations_mcp_service import (
    OperationsMCPClient,
    OperationsMCPError,
    mcp_result_value,
    redact_sensitive_text,
)


class FakeResponse:
    def __init__(self, lines=None):
        self.lines = list(lines or [])

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def readline(self):
        return self.lines.pop(0) if self.lines else b""

    def read(self, _limit=-1):
        return b""


class FakeOpener:
    def __init__(self, endpoint="/messages/session-1"):
        self.requests = []
        tool_response = json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "content": [{
                    "type": "text",
                    "text": json.dumps({"rootPath": r"F:\Projects\assistant-ui"}),
                }],
            },
        }).encode() + b"\n"
        self.stream = FakeResponse([
            b"event: endpoint\n",
            f"data: {endpoint}\n".encode(),
            b"event: message\n",
            b'data: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05"}}\n',
            b"event: message\n",
            b"data: " + tool_response,
        ])

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        return self.stream if request.get_method() == "GET" else FakeResponse()


def test_mcp_client_authenticates_initializes_and_calls_one_tool():
    opener = FakeOpener()
    client = OperationsMCPClient(
        "https://bridge.example.test/sse",
        "private-bridge-token",
        opener=opener,
    )

    result = client.call_tool("get_workspace_info", {})

    assert mcp_result_value(result) == {"rootPath": "F:\\Projects\\assistant-ui"}
    assert [request.get_method() for request, _ in opener.requests] == ["GET", "POST", "POST", "POST"]
    assert all(request.headers["Authorization"] == "Bearer private-bridge-token" for request, _ in opener.requests)
    posted = [json.loads(request.data) for request, _ in opener.requests if request.data]
    assert [item["method"] for item in posted] == [
        "initialize",
        "notifications/initialized",
        "tools/call",
    ]
    assert posted[-1]["params"] == {"name": "get_workspace_info", "arguments": {}}


def test_mcp_client_rejects_insecure_remote_url_and_cross_origin_endpoint():
    insecure = OperationsMCPClient("http://bridge.example.test/sse", "token", opener=FakeOpener())
    with pytest.raises(OperationsMCPError, match="HTTPS"):
        insecure.call_tool("get_workspace_info")

    unsafe_endpoint = OperationsMCPClient(
        "https://bridge.example.test/sse",
        "token",
        opener=FakeOpener("https://attacker.example/messages/1"),
    )
    with pytest.raises(OperationsMCPError, match="unsafe"):
        unsafe_endpoint.call_tool("get_workspace_info")


def test_bridge_output_is_bounded_and_common_secret_shapes_are_redacted():
    value = redact_sensitive_text(
        "Authorization: Bearer hidden-value password=also-hidden github_pat_abcdefghijklmnop"
    )

    assert "hidden-value" not in value
    assert "also-hidden" not in value
    assert "github_pat_abcdefghijklmnop" not in value
    assert value.count("[REDACTED]") == 3
