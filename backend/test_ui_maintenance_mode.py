from fastapi.testclient import TestClient

import main


client = TestClient(main.app)


def test_ui_documents_are_offline_while_backend_remains_available():
    ui_response = client.get(
        "/bookings",
        headers={"Accept": "text/html", "Sec-Fetch-Dest": "document"},
    )
    assert ui_response.status_code == 503
    assert "Temporarily offline" in ui_response.text
    assert ui_response.headers["cache-control"] == "no-store"

    health_response = client.get("/api/health")
    assert health_response.status_code == 200
    assert health_response.json()["status"] == "ok"
