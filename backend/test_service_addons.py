import json

from fastapi.testclient import TestClient

import main
from main import app
from services.settings_service import get_live_services_context, load_line_services


client = TestClient(app)


def test_service_addons_persist_separately_and_are_shared_with_both_ai_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    for filename, service_name in (
        ("line_1_services.json", "Line one service"),
        ("line_2_services.json", "Line two service"),
    ):
        (tmp_path / filename).write_text(json.dumps([{
            "id": filename,
            "name": service_name,
            "description": "Bookable",
            "price": 100,
            "duration": 30,
        }]), encoding="utf-8")

    addon = {
        "id": "extra-care",
        "name": "Extended care",
        "description": "Optional finishing time.",
        "price": 25,
        "duration": 15,
    }
    response = client.post("/api/settings/service-addons", json={"addons": [addon]})

    assert response.status_code == 200
    assert json.loads((tmp_path / "service_addons.json").read_text(encoding="utf-8")) == [addon]
    assert all("extra-care" not in {item["id"] for item in load_line_services(line)} for line in ("primary", "secondary"))
    assert client.get("/api/settings/service-addons").json() == [addon]

    booking_response = client.post("/api/calendar/bookings", json={
        "serviceId": "extra-care",
        "name": "Test Customer",
        "phone": "+61412345678",
        "startTime": "2026-10-01T10:00:00+10:00",
    })
    assert booking_response.status_code == 422
    assert booking_response.json()["detail"] == "Add-ons cannot be booked as standalone services."

    for line in ("primary", "secondary"):
        context = get_live_services_context(line)
        assert "Add-on: Extended care" in context
        assert "Extra price: $25" in context
        assert "Extra time: 15 minutes" in context
        assert "informational only" in context
        assert "cannot be booked as standalone services" in context
        assert "Booking service ID: extra-care" not in context


def test_service_addon_schema_rejects_line_assignment(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    response = client.post("/api/settings/service-addons", json={"addons": [{
        "id": "line-specific",
        "name": "Must be shared",
        "description": "",
        "price": 10,
        "duration": 5,
        "lineKey": "primary",
    }]})

    # Pydantic ignores unknown fields, and the persisted canonical record has no
    # line ownership: both AI contexts consume the one shared file.
    assert response.status_code == 200
    assert "lineKey" not in client.get("/api/settings/service-addons").json()[0]
