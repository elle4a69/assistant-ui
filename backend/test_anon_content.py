from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import anon_content


def test_anon_content_is_persisted_and_shared(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ANON_CONTENT_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(anon_content.router)
    client = TestClient(app)

    initial = client.get("/api/anon/content")
    assert initial.status_code == 200
    assert initial.json()["imageUrl"] is None

    png = b"\x89PNG\r\n\x1a\n" + b"test-image-data"
    saved = client.put(
        "/api/anon/content",
        data={"heading": "A real heading", "body": "A real paragraph."},
        files={"image": ("photo.png", png, "image/png")},
    )
    assert saved.status_code == 200
    assert saved.json()["heading"] == "A real heading"
    assert saved.json()["imageUrl"] == "/api/anon/image"

    reloaded = client.get("/api/anon/content")
    assert reloaded.json()["body"] == "A real paragraph."
    assert (tmp_path / "content.json").is_file()
    assert client.get("/api/anon/image").content == png


def test_anon_content_rejects_non_image_upload(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ANON_CONTENT_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(anon_content.router)
    client = TestClient(app)

    response = client.put(
        "/api/anon/content",
        data={"heading": "Heading", "body": "Paragraph"},
        files={"image": ("bad.txt", b"not an image", "text/plain")},
    )
    assert response.status_code == 415


def test_anon_content_accepts_long_form_copy(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ANON_CONTENT_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(anon_content.router)
    client = TestClient(app)
    long_body = "First paragraph.\n\n" + ("Long-form copy with room to breathe. " * 2000)

    response = client.put(
        "/api/anon/content",
        data={"heading": "Long read", "body": long_body},
    )

    assert response.status_code == 200
    assert response.json()["body"] == long_body.strip()
    assert client.get("/api/anon/content").json()["body"] == long_body.strip()
