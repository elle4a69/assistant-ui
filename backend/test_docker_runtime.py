from pathlib import Path


def test_container_starts_the_packaged_backend_module_from_app_root():
    dockerfile = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text(encoding="utf-8")

    assert "WORKDIR /app/backend" not in dockerfile
    assert '"backend.main:app"' in dockerfile
