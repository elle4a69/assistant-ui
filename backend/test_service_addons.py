import json

from fastapi.testclient import TestClient

import main


def test_shared_service_addons_persist_and_are_available_to_both_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    (tmp_path / "line_1_services.json").write_text("[]", encoding="utf-8")
    (tmp_path / "line_2_services.json").write_text("[]", encoding="utf-8")
    client = TestClient(main.app)

    payload = {
        "addons": [{
            "id": "addon-hot-stones",
            "name": "Hot stones",
            "description": "Heated stones added to the treatment.",
            "price": 35,
            "duration": 15,
        }]
    }
    response = client.post("/api/service-addons", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "success"}
    assert client.get("/api/service-addons").json() == payload["addons"]
    assert json.loads((tmp_path / "service_addons.json").read_text(encoding="utf-8")) == payload["addons"]

    for account_key in ("primary", "secondary"):
        context = main.get_live_services_context(account_key)
        assert "Shared service add-ons" in context
        assert "Hot stones" in context
        assert "Additional price: $35" in context
        assert "Additional time: 15 minutes" in context


def test_service_addons_allow_free_or_no_extra_time_items(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    main.save_service_addons([{
        "id": "addon-fragrance-free",
        "name": "Fragrance-free products",
        "description": "",
        "price": 0,
        "duration": 0,
    }])

    context = main.get_live_services_context("primary")
    assert "Fragrance-free products" in context
    assert "Additional price: $0" in context
    assert "Additional time:" not in context


def test_service_addons_reject_negative_price_or_time(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    client = TestClient(main.app)

    response = client.post("/api/service-addons", json={"addons": [{
        "id": "invalid-addon",
        "name": "Invalid",
        "price": -1,
        "duration": -5,
    }]})

    assert response.status_code == 422
