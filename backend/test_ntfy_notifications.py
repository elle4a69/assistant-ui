from backend.services import notification_service


class _Response:
    def __init__(self, fail=False):
        self.fail = fail

    def raise_for_status(self):
        if self.fail:
            raise RuntimeError("delivery failed")


def test_ntfy_disabled_without_topic(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    called = []

    def fake_post(*args, **kwargs):
        called.append((args, kwargs))
        return _Response()

    monkeypatch.setattr(notification_service.requests, "post", fake_post)

    assert notification_service.send_ntfy_notification(
        title="New SMS",
        message="hello",
        click_url="https://example.test/chat?thread=abc",
    ) is False
    assert called == []


def test_ntfy_posts_click_target_and_auth(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "assistant ui/private")
    monkeypatch.setenv("NTFY_BASE_URL", "https://notify.example.test/")
    monkeypatch.setenv("NTFY_TOKEN", "secret-token")
    sent = {}

    def fake_post(url, **kwargs):
        sent["url"] = url
        sent.update(kwargs)
        return _Response()

    monkeypatch.setattr(notification_service.requests, "post", fake_post)

    assert notification_service.send_ntfy_notification(
        title="New SMS · Line 1",
        message="+61400000000\nHello",
        click_url="https://assistant-ui-hub.fly.dev/chat?thread=thread-1",
        priority=4,
    ) is True

    assert sent["url"] == "https://notify.example.test/assistant%20ui%2Fprivate"
    assert sent["headers"]["Title"] == "New SMS · Line 1"
    assert sent["headers"]["Priority"] == "4"
    assert sent["headers"]["Click"] == "https://assistant-ui-hub.fly.dev/chat?thread=thread-1"
    assert sent["headers"]["Authorization"] == "Bearer secret-token"
    assert sent["data"] == b"+61400000000\nHello"
    assert sent["timeout"] == 5


def test_ntfy_failure_is_non_fatal(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "topic")

    def fake_post(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(notification_service.requests, "post", fake_post)

    assert notification_service.send_ntfy_notification(
        title="New SMS",
        message="hello",
        click_url="https://example.test/chat?thread=abc",
    ) is False
