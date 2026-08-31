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


@pytest.mark.parametrize("response", [None, {"workflow_run_id": 123}])
def test_dispatch_wakes_only_operations_on_main_with_bounded_timeout(response):
    opener = FakeOpener(FakeResponse(response))

    assert make_client(opener).dispatch_workflow() is None

    request, timeout = opener.requests[0]
    assert request.method == "POST"
    assert request.full_url == (
        "https://api.github.com/repos/elle4a69/assistant-ui"
        "/actions/workflows/operations-code.yml/dispatches"
    )
    assert json.loads(request.data) == {"ref": "main"}
    assert request.get_header("Authorization") == "Bearer test-token"
    assert request.get_header("Content-type") == "application/json"
    assert timeout == 5.0


@pytest.mark.parametrize("error", [
    url_error.HTTPError("https://api.github.com", 403, "test-token", {}, io.BytesIO(b"test-token")),
    url_error.URLError("test-token"),
    TimeoutError("test-token"),
])
def test_dispatch_transport_errors_do_not_expose_credentials(error):
    with pytest.raises(OperationsGitHubError) as exc_info:
        make_client(FakeOpener(error)).dispatch_workflow()
    assert "test-token" not in str(exc_info.value)


def test_dispatch_requires_configuration_before_any_network_request():
    opener = FakeOpener()
    with pytest.raises(OperationsGitHubError, match="token"):
        OperationsGitHubClient("elle4a69/assistant-ui", "", opener=opener).dispatch_workflow()
    assert not opener.requests


@pytest.fixture
def queued_actions(tmp_path, monkeypatch):
    import main
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path / 'dispatch-test.db'}")
    main.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(main, "operations_code_access_available", lambda: True)
    monkeypatch.setattr(main, "operations_deployment_enabled", lambda: True)
    monkeypatch.setattr(main.mobilemessage_service, "send_sms", lambda *_a, **_k: pytest.fail("SMS must not be sent"))
    with factory() as db:
        yield main, db, factory
    engine.dispose()


@pytest.mark.parametrize("dispatch_error", [None, OperationsGitHubError("GitHub denied the Operations AI request."), RuntimeError("private-token")])
def test_coding_dispatch_follows_durable_commit_and_preserves_fallback(queued_actions, monkeypatch, dispatch_error):
    main, db, factory = queued_actions
    calls = []

    def dispatch():
        # A separate connection must see the committed task before dispatch.
        with factory() as observer:
            task = observer.query(main.OperationsAction).one()
            assert task.status == "queued"
            assert task.action_type == "coding_task"
            calls.append(task.id)
        if dispatch_error:
            raise dispatch_error

    monkeypatch.setattr(main.operations_github_client, "dispatch_workflow", dispatch)
    arguments = ("Repair ordering", "Repair the ordering with focused coverage for equal timestamps.", "Ordering tests pass.")
    result = main._operations_start_coding_task(db, *arguments)
    duplicate = main._operations_start_coding_task(db, *arguments)

    assert result["status"] == "started"
    assert calls == [result["task_id"]]
    assert result["worker_requested"] is (dispatch_error is None)
    assert "five-minute scheduled queue" in result["next_step"]
    assert "private-token" not in json.dumps(result)
    assert duplicate["status"] == "already_running"
    assert db.query(main.OperationsAction).one().status == "queued"


@pytest.mark.parametrize("dispatch_fails", [False, True])
def test_deployment_dispatch_requires_exact_confirmation_and_keeps_queued_work(queued_actions, monkeypatch, dispatch_fails):
    import uuid

    main, db, factory = queued_actions
    task_id = str(uuid.uuid4())
    branch = f"ops/task-{task_id}"
    task = main.OperationsAction(
        id=task_id, action_type="coding_task", status="completed", reason="Approved coding task",
        payload=json.dumps({"title": "Repair", "branch": branch, "commit_sha": "b" * 40, "base_sha": "a" * 40}),
    )
    db.add(task)
    db.commit()
    calls = []
    client = main.operations_github_client
    monkeypatch.setattr(client, "get_ref", lambda _ref: {"object": {"sha": "a" * 40}})
    monkeypatch.setattr(client, "get_branch", lambda _branch: {"commit": {"sha": "b" * 40}})
    monkeypatch.setattr(client, "get_git_commit", lambda _sha: {"parents": [{"sha": "a" * 40}]})
    monkeypatch.setattr(client, "compare", lambda *_a: {
        "status": "ahead", "ahead_by": 1, "files": [{"filename": "backend/main.py"}],
    })

    def dispatch():
        with factory() as observer:
            action = observer.query(main.OperationsAction).filter_by(action_type="code_deployment").one()
            assert action.status == "queued"
            calls.append(action.id)
        if dispatch_fails:
            raise OperationsGitHubError("GitHub could not be reached (TimeoutError).")

    monkeypatch.setattr(client, "dispatch_workflow", dispatch)
    proposed = main._operations_propose_code_deployment(db, task_id, "Checks passed.")
    assert proposed["status"] == "pending_confirmation"
    action_id = proposed["action_id"]
    assert not calls
    rejected = main._operations_execute_code_deployment(db, action_id, "yes deploy it")
    assert rejected["status"] == "rejected"
    assert not calls

    result = main._operations_execute_code_deployment(db, action_id, proposed["confirmation_phrase"])
    repeated = main._operations_execute_code_deployment(db, action_id, proposed["confirmation_phrase"])
    assert result["status"] == "deployment_queued"
    assert result["worker_requested"] is not dispatch_fails
    assert calls == [action_id]
    assert db.get(main.OperationsAction, action_id).status == "queued"
    assert repeated["status"] == "rejected"
