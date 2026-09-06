import sys
import os
import shutil
import json
from datetime import datetime

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

os.environ["SQLITE_TMPDIR"] = "f:/Projects/assistant-ui/backend/tmp"
os.makedirs("f:/Projects/assistant-ui/backend/tmp", exist_ok=True)

from fastapi.testclient import TestClient
import main
from main import app, engine, Base, DATA_DIR, PROMPTS_DIR

# Backup operational files before test run
services_real_path = os.path.join(DATA_DIR, "services.json")
services_bak_path = os.path.join(DATA_DIR, "services.json.bak_test")
if os.path.exists(services_real_path):
    shutil.copyfile(services_real_path, services_bak_path)

template_real_path = os.path.join(PROMPTS_DIR, "sms_confirmation_template.txt")
template_bak_path = os.path.join(PROMPTS_DIR, "sms_confirmation_template.txt.bak_test")
if os.path.exists(template_real_path):
    shutil.copyfile(template_real_path, template_bak_path)

# Clear database tables
Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

client = TestClient(app)

def test_services_and_manual_bookings(monkeypatch, tmp_path):
    try:
        monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(main, "PROMPTS_DIR", str(tmp_path / "prompts"))
        monkeypatch.setattr(main, "booking_availability_error", lambda *_args: None)
        sent_messages = []
        booking_ids = iter(("test-booking-id-primary", "test-booking-id-natural", "test-booking-id-secondary"))
        created_bookings = []
        def create_booking(**kwargs):
            created_bookings.append(kwargs)
            return next(booking_ids)
        monkeypatch.setattr(main.calendar_service, "create_booking", create_booking)
        monkeypatch.setattr(
            main.mobilemessage_service,
            "send_sms",
            lambda phone, text, **kwargs: sent_messages.append((phone, text, kwargs.get("account_key"))) or {"status": "success"},
        )
        monkeypatch.setattr(main.mobilemessage_service, "delivery_error", lambda result: None)

        # 1. Test GET services
        response = client.get("/api/services")
        assert response.status_code == 200
        services_list = response.json()
        assert isinstance(services_list, list)

        # 2. Test POST services list
        test_services = {
            "services": [
                {
                    "id": "test_srv_1",
                    "name": "Luxury Deep Tissue Massage",
                    "description": "An intense, premium body massage.",
                    "price": 180,
                    "duration": 60
                }
            ]
        }
        response = client.post("/api/services", json=test_services)
        assert response.status_code == 200
        assert response.json()["status"] == "success"

        # Verify update persisted
        response = client.get("/api/services")
        services = response.json()
        assert len(services) == 1
        assert services[0]["id"] == "test_srv_1"
        assert services[0]["price"] == 180

        # 3. Test GET/POST SMS confirmation template
        response = client.get("/api/settings/sms-confirmation")
        assert response.status_code == 200
        assert "template" in response.json()

        test_template = {"template": "Hey {name}, you are confirmed for {service} at {time}! - Test signature"}
        response = client.post("/api/settings/sms-confirmation", json=test_template)
        assert response.status_code == 200
        assert response.json()["status"] == "success"

        # Verify template persisted
        response = client.get("/api/settings/sms-confirmation")
        assert response.json()["template"] == test_template["template"]

        # 4. Test POST manual booking
        booking_payload = {
            "serviceId": "test_srv_1",
            "name": "Alex Jones",
            "phone": "+61411222333",
            "startTime": "2026-08-09T05:00:00Z",
            "notes": "Prefers medium pressure."
        }
        response = client.post("/api/calendar/bookings", json=booking_payload)
        assert response.status_code == 200, response.text
        res_data = response.json()
        assert res_data["status"] == "success"
        assert "Alex Jones" in res_data["smsSent"]
        assert "Luxury Deep Tissue Massage" in res_data["smsSent"]
        assert "Sunday, Aug 09 at 03:00 PM" in res_data["smsSent"]
        assert res_data["arrivalLink"].startswith("http")
        assert "/a/" in res_data["arrivalLink"]
        assert res_data["arrivalLink"] in sent_messages[0][1]
        assert "When you arrive, tap:" in sent_messages[0][1]
        assert sent_messages[0][2] == "primary"
        assert res_data["extras"] == []
        assert res_data["amount"] == 180
        assert "extras" not in created_bookings[0]

        natural_payload = {
            **booking_payload,
            "startTime": "2026-08-09T05:30:00Z",
            "extras": ["natural"],
        }
        response = client.post("/api/calendar/bookings", json=natural_payload)
        assert response.status_code == 200
        assert response.json()["extras"] == [{"id": "natural", "name": "Natural", "price": 100}]
        assert response.json()["amount"] == 280
        assert created_bookings[1]["extras"] == [{"id": "natural", "name": "Natural", "price": 100}]

        natural_db = main.SessionLocal()
        try:
            natural_booking = natural_db.query(main.CalendarEvent).filter_by(
                id="test-booking-id-natural"
            ).one()
            assert json.loads(natural_booking.extras) == [{"id": "natural", "name": "Natural", "price": 100}]
            assert natural_booking.amount == 280
        finally:
            natural_db.close()

        # The selected sender must own both the SMS dispatch and conversation.
        secondary_payload = {
            **booking_payload,
            "startTime": "2026-08-09T06:00:00Z",
            "providerKey": "anonymous",
        }
        response = client.post("/api/calendar/bookings", json=secondary_payload)
        assert response.status_code == 200
        assert sent_messages[2][2] == "secondary"

        test_db = main.SessionLocal()
        try:
            account_threads = (
                test_db.query(main.Thread)
                .filter(main.Thread.customer_phone == "+61411222333")
                .order_by(main.Thread.sms_account_key)
                .all()
            )
            assert [thread.sms_account_key for thread in account_threads] == ["primary", "secondary"]
        finally:
            test_db.close()

    finally:
        # Restore operational files after test run
        if os.path.exists(services_bak_path):
            shutil.copyfile(services_bak_path, services_real_path)
            os.remove(services_bak_path)
        if os.path.exists(template_bak_path):
            shutil.copyfile(template_bak_path, template_real_path)
            os.remove(template_bak_path)
