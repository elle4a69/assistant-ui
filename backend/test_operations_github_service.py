import base64
import io
import json
from urllib import error as url_error

import pytest

from operations_github_service import (
    OperationsGitHubClient,
    OperationsGitHubError,
    redact_sensitive_text,
)


class FakeResponse:
    def __init__(self, payload=None):
        if payload is None:
            self.body = b""
        elif isinstance(payload, bytes):
            self.body = payload
        else:
            self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, limit=-1):
        return self.body if limit < 0 else self.body[:limit]


class FakeOpener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        response = self.responses.pop(0) if self.responses else FakeResponse()
        if isinstance(response, Exception):
            raise response
        return response


def make_client(opener):
    return OperationsGitHubClient(
        "elle4a69/assistant-ui",
        "test-token",
        opener=opener,
    )


def test_read_file_decodes_content_from_main():
    source = "first line\nsecond line\n"
    opener = FakeOpener(
        FakeResponse({
            "type": "file",
            "encoding": "base64",
            "path": "backend/main.py",
            "sha": "a" * 40,
            "size": len(source),
            "content": base64.b64encode(source.encode("utf-8")).decode("ascii"),
        }),
    )
    client = make_client(opener)

    result = client.read_file("backend/main.py", ref="main")

    assert result["content"] == source
    assert result["path"] == "backend/main.py"
    assert "contents/backend/main.py?ref=main" in opener.requests[0][0].full_url


def test_workflow_and_branch_paths_are_url_encoded():
    opener = FakeOpener(
        FakeResponse({"workflow_runs": [{"id": 1, "display_title": "Operations coding task 123"}]}),
        FakeResponse({"name": "ops/task-123", "commit": {"sha": "c" * 40}}),
        FakeResponse({"id": 123, "status": "in_progress"}),
    )
    client = make_client(opener)

    runs = client.list_workflow_runs(limit=5, workflow="operations-code.yml", event="schedule")
    branch = client.get_branch("ops/task-123")
    workflow_run = client.get_workflow_run(123)

    assert runs[0]["id"] == 1
    assert "actions/workflows/operations-code.yml/runs?" in opener.requests[0][0].full_url
    assert "event=schedule" in opener.requests[0][0].full_url
    assert opener.requests[1][0].full_url.endswith("/branches/ops%2Ftask-123")
    assert branch["commit"]["sha"] == "c" * 40
    assert opener.requests[2][0].full_url.endswith("/actions/runs/123")
    assert workflow_run["status"] == "in_progress"


def test_configuration_and_http_errors_are_safe_and_never_include_token():
    client = OperationsGitHubClient("bad repository", "private-value", opener=FakeOpener())
    with pytest.raises(OperationsGitHubError, match="repository"):
        client.list_workflow_runs()

    forbidden = url_error.HTTPError(
        "https://api.github.com/repos/elle4a69/assistant-ui",
        403,
        "Forbidden private-value",
        {},
        io.BytesIO(b'{"message":"private-value"}'),
    )
    denied = OperationsGitHubClient(
        "elle4a69/assistant-ui",
        "private-value",
        opener=FakeOpener(forbidden),
    )
    with pytest.raises(OperationsGitHubError) as exc_info:
        denied.list_workflow_runs()
    assert "denied" in str(exc_info.value).lower()
    assert "private-value" not in str(exc_info.value)

    redacted = redact_sensitive_text("Authorization: Bearer hidden password=also-hidden")
    assert "hidden" not in redacted
    assert "also-hidden" not in redacted
