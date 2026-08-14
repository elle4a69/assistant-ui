from datetime import datetime, timedelta
from urllib.parse import urlparse

from fastapi.testclient import TestClient

from main import ArrivalChatMessage, ArrivalSession, CalendarEvent, SessionLocal, app


client = TestClient(app)


def _booking_payload():
    start = datetime.utcnow() + timedelta(hours=2)
    return {
        "summary": "Arrival flow test",
        "customerPhone": "+61400000001",
        "startTime": start.isoformat() + "Z",
        "endTime": (start + timedelta(hours=1)).isoformat() + "Z",
    }


def _cleanup(booking_id: str):
    db = SessionLocal()
    try:
        session_ids = [row.id for row in db.query(ArrivalSession).filter(ArrivalSession.booking_id == booking_id)]
        if session_ids:
            db.query(ArrivalChatMessage).filter(ArrivalChatMessage.session_id.in_(session_ids)).delete(
                synchronize_session=False
            )
        db.query(ArrivalSession).filter(ArrivalSession.booking_id == booking_id).delete(synchronize_session=False)
        db.query(CalendarEvent).filter(CalendarEvent.id == booking_id).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_arrival_invite_is_single_use_and_opens_private_chat():
    booking_id = "arrival-single-use-test"
    _cleanup(booking_id)
    try:
        invite_response = client.post(
            f"/api/arrival/admin/bookings/{booking_id}/invite", json=_booking_payload()
        )
        assert invite_response.status_code == 200
        short_link = invite_response.json()["link"]
        invite = urlparse(short_link).path.rsplit("/", 1)[1]
        redirect = client.get(urlparse(short_link).path, follow_redirects=False)
        assert redirect.status_code == 302
        assert redirect.headers["location"] == f"/arrival#invite={invite}"

        activation = client.post("/api/arrival/activate", json={"inviteToken": invite})
        assert activation.status_code == 200
        body = activation.json()
        session_id = body["session"]["id"]
        token = body["clientToken"]
        assert body["session"]["status"] == "active"

        second_activation = client.post("/api/arrival/activate", json={"inviteToken": invite})
        assert second_activation.status_code == 410

        missing_token = client.get(f"/api/arrival/client/{session_id}")
        assert missing_token.status_code == 401

        customer_message = client.post(
            f"/api/arrival/client/{session_id}/messages",
            headers={"Authorization": f"Arrival {token}"},
            json={"text": "I am by the front door."},
        )
        assert customer_message.status_code == 200
        provider_message = client.post(
            f"/api/arrival/admin/sessions/{session_id}/messages",
            json={"text": "Please wait there for five minutes."},
        )
        assert provider_message.status_code == 200

        chat = client.get(
            f"/api/arrival/client/{session_id}", headers={"Authorization": f"Arrival {token}"}
        ).json()
        assert [message["sender"] for message in chat["messages"]] == ["system", "client", "provider"]
    finally:
        _cleanup(booking_id)


def test_reissuing_invite_revokes_previous_link():
    booking_id = "arrival-reissue-test"
    _cleanup(booking_id)
    try:
        first = client.post(f"/api/arrival/admin/bookings/{booking_id}/invite", json=_booking_payload()).json()
        second = client.post(f"/api/arrival/admin/bookings/{booking_id}/invite", json=_booking_payload()).json()
        first_token = urlparse(first["link"]).path.rsplit("/", 1)[1]
        second_token = urlparse(second["link"]).path.rsplit("/", 1)[1]

        assert client.post("/api/arrival/activate", json={"inviteToken": first_token}).status_code == 410
        assert client.post("/api/arrival/activate", json={"inviteToken": second_token}).status_code == 200
    finally:
        _cleanup(booking_id)
