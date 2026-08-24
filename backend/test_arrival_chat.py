import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import urlparse

from fastapi.testclient import TestClient

import main
from main import (
    ArrivalChatMessage,
    ArrivalSession,
    CalendarEvent,
    Message,
    SessionLocal,
    Thread,
    ThreadEvent,
    app,
)


client = TestClient(app)
TEST_PHONE = "+61400000001"


def _booking_payload(account: str = "primary", thread_id: str | None = None):
    start = datetime.utcnow() + timedelta(hours=2)
    return {
        "summary": "Arrival flow test (Tori)" if account == "primary" else "Arrival flow test (Anonymous)",
        "customerPhone": TEST_PHONE,
        "smsAccountKey": account,
        "threadId": thread_id,
        "startTime": start.isoformat() + "Z",
        "endTime": (start + timedelta(hours=1)).isoformat() + "Z",
    }


def _cleanup(*booking_ids: str):
    db = SessionLocal()
    try:
        session_ids = [
            row.id for row in db.query(ArrivalSession).filter(ArrivalSession.booking_id.in_(booking_ids))
        ] if booking_ids else []
        if session_ids:
            db.query(ArrivalChatMessage).filter(ArrivalChatMessage.session_id.in_(session_ids)).delete(
                synchronize_session=False
            )
            db.query(ArrivalSession).filter(ArrivalSession.id.in_(session_ids)).delete(synchronize_session=False)
        if booking_ids:
            db.query(CalendarEvent).filter(CalendarEvent.id.in_(booking_ids)).delete(synchronize_session=False)

        thread_ids = [
            row.id for row in db.query(Thread).filter(Thread.customer_phone == TEST_PHONE).all()
        ]
        if thread_ids:
            db.query(Message).filter(Message.thread_id.in_(thread_ids)).delete(synchronize_session=False)
            db.query(ThreadEvent).filter(ThreadEvent.thread_id.in_(thread_ids)).delete(synchronize_session=False)
            db.query(Thread).filter(Thread.id.in_(thread_ids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_arrival_link_reopens_without_duplicate_check_in_and_acknowledges_from_bound_thread():
    booking_id = "arrival-reusable-test"
    _cleanup(booking_id)
    try:
        invite_response = client.post(
            f"/api/arrival/admin/bookings/{booking_id}/invite",
            json=_booking_payload(),
        )
        assert invite_response.status_code == 200
        invite_body = invite_response.json()
        short_link = invite_body["link"]
        invite = urlparse(short_link).path.rsplit("/", 1)[1]
        thread_id = invite_body["session"]["threadId"]
        assert invite_body["session"]["smsAccountKey"] == "primary"

        status_before_arrival = client.post("/api/arrival/status", json={"inviteToken": invite})
        assert status_before_arrival.status_code == 200
        assert status_before_arrival.json()["active"] is False
        assert client.post(
            f"/api/threads/{thread_id}/arrivals/{invite_body['session']['id']}/acknowledge"
        ).status_code == 409

        for _ in range(2):
            redirect = client.get(urlparse(short_link).path, follow_redirects=False)
            assert redirect.status_code == 302
            assert redirect.headers["location"] == f"/arrival#invite={invite}"

        activation = client.post("/api/arrival/activate", json={"inviteToken": invite})
        assert activation.status_code == 200
        body = activation.json()
        session_id = body["session"]["id"]
        token = body["clientToken"]
        assert body["alreadyActivated"] is False
        assert body["session"]["status"] == "active"
        assert body["session"]["threadId"] == thread_id
        assert body["session"]["alertCount"] == 1
        activated_at = body["session"]["activatedAt"]

        second_activation = client.post("/api/arrival/activate", json={"inviteToken": invite})
        assert second_activation.status_code == 200
        assert second_activation.json()["alreadyActivated"] is True
        assert second_activation.json()["session"]["activatedAt"] == activated_at
        reopened_status = client.post("/api/arrival/status", json={"inviteToken": invite})
        assert reopened_status.status_code == 200
        assert reopened_status.json()["active"] is True
        assert reopened_status.json()["session"]["id"] == session_id

        missing_token = client.get(f"/api/arrival/client/{session_id}")
        assert missing_token.status_code == 401
        assert client.get(
            f"/api/arrival/client/{session_id}",
            headers={"Authorization": f"Arrival {token}"},
        ).status_code == 200

        db = SessionLocal()
        try:
            arrival_events = db.query(ThreadEvent).filter(
                ThreadEvent.thread_id == thread_id,
                ThreadEvent.type == "customer-arrived",
            ).all()
            assert len(arrival_events) == 1
            assert json.loads(arrival_events[0].meta)["arrival_session_id"] == session_id
            assert db.query(ArrivalChatMessage).filter(
                ArrivalChatMessage.session_id == session_id,
                ArrivalChatMessage.sender == "system",
            ).count() == 1
        finally:
            db.close()

        thread_item = next(item for item in client.get("/api/threads").json() if item["id"] == thread_id)
        assert thread_item["pendingArrivalSessionId"] == session_id
        assert thread_item["pendingArrivalEventId"] == body["session"]["arrivalEventId"]
        assert thread_item["lastArrivalSessionId"] == session_id

        acknowledge_path = f"/api/threads/{thread_id}/arrivals/{session_id}/acknowledge"
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(lambda _index: client.post(acknowledge_path).status_code, range(2)))
        assert statuses == [200, 200]
        assert client.post(acknowledge_path).status_code == 200
        assert client.get(urlparse(short_link).path, follow_redirects=False).status_code == 302
        reopened_after_ack = client.post("/api/arrival/status", json={"inviteToken": invite})
        assert reopened_after_ack.status_code == 200
        assert reopened_after_ack.json()["session"]["acknowledgedAt"] is not None
        thread_item = next(item for item in client.get("/api/threads").json() if item["id"] == thread_id)
        assert thread_item["pendingArrivalSessionId"] is None
        assert thread_item["lastArrivalSessionId"] == session_id
        db = SessionLocal()
        try:
            assert db.query(ThreadEvent).filter(
                ThreadEvent.thread_id == thread_id,
                ThreadEvent.type == "customer-arrival-acknowledged",
            ).count() == 1
            booking = db.query(CalendarEvent).filter(CalendarEvent.id == booking_id).one()
            assert booking.sms_account_key == "primary"
            assert booking.thread_id == thread_id
        finally:
            db.close()
    finally:
        _cleanup(booking_id)


def test_reissuing_invite_revokes_previous_link():
    booking_id = "arrival-reissue-test"
    _cleanup(booking_id)
    try:
        first = client.post(
            f"/api/arrival/admin/bookings/{booking_id}/invite", json=_booking_payload()
        ).json()
        second = client.post(
            f"/api/arrival/admin/bookings/{booking_id}/invite",
            json=_booking_payload(thread_id=first["session"]["threadId"]),
        ).json()
        first_token = urlparse(first["link"]).path.rsplit("/", 1)[1]
        second_token = urlparse(second["link"]).path.rsplit("/", 1)[1]

        assert client.post("/api/arrival/activate", json={"inviteToken": first_token}).status_code == 410
        assert client.post("/api/arrival/activate", json={"inviteToken": second_token}).status_code == 200
    finally:
        _cleanup(booking_id)


def test_same_phone_on_two_sms_lines_never_crosses_arrival_or_acknowledgement():
    booking_id = "arrival-account-isolation-test"
    _cleanup(booking_id)
    db = SessionLocal()
    now = datetime.utcnow()
    try:
        primary = Thread(
            id="arrival-primary-thread", customer_phone=TEST_PHONE, sms_account_key="primary",
            state="resolved", priority="medium", sla_due_at=now + timedelta(hours=3),
            unread_count=0, created_at=now, updated_at=now,
        )
        secondary = Thread(
            id="arrival-secondary-thread", customer_phone=TEST_PHONE, sms_account_key="secondary",
            state="resolved", priority="medium", sla_due_at=now + timedelta(hours=3),
            unread_count=0, created_at=now, updated_at=now,
        )
        db.add_all([primary, secondary])
        db.commit()
    finally:
        db.close()

    try:
        invite_response = client.post(
            f"/api/arrival/admin/bookings/{booking_id}/invite",
            json=_booking_payload("secondary", "arrival-secondary-thread"),
        )
        assert invite_response.status_code == 200
        invite = urlparse(invite_response.json()["link"]).path.rsplit("/", 1)[1]
        activation = client.post("/api/arrival/activate", json={"inviteToken": invite}).json()
        session_id = activation["session"]["id"]
        assert activation["session"]["threadId"] == "arrival-secondary-thread"
        assert activation["session"]["smsAccountKey"] == "secondary"

        assert client.post(
            f"/api/threads/arrival-primary-thread/arrivals/{session_id}/acknowledge"
        ).status_code == 404

        db = SessionLocal()
        try:
            assert db.query(ThreadEvent).filter(
                ThreadEvent.thread_id == "arrival-primary-thread",
                ThreadEvent.type == "customer-arrived",
            ).count() == 0
            assert db.query(ThreadEvent).filter(
                ThreadEvent.thread_id == "arrival-secondary-thread",
                ThreadEvent.type == "customer-arrived",
            ).count() == 1
            assert db.query(ArrivalSession).filter(ArrivalSession.id == session_id).one().acknowledged_at is None
        finally:
            db.close()

        assert client.post(
            f"/api/threads/arrival-secondary-thread/arrivals/{session_id}/acknowledge"
        ).status_code == 200
    finally:
        _cleanup(booking_id)


def test_reopened_legacy_active_link_is_safely_bound_to_its_unique_thread():
    booking_id = "arrival-legacy-active-test"
    invite = "LegacyArrival1234"
    _cleanup(booking_id)
    now = datetime.utcnow()
    db = SessionLocal()
    try:
        db.add(Thread(
            id="arrival-legacy-thread", customer_phone=TEST_PHONE, sms_account_key="primary",
            state="resolved", priority="medium", sla_due_at=now + timedelta(hours=3),
            unread_count=0, created_at=now, updated_at=now,
        ))
        db.add(CalendarEvent(
            id=booking_id, summary="Legacy arrival", customer_phone=TEST_PHONE,
            start_time=now, end_time=now + timedelta(hours=1), status="scheduled", notes="",
        ))
        db.add(ArrivalSession(
            id="arrival-legacy-session", booking_id=booking_id,
            invite_token_hash=main._hash_arrival_token(invite), status="active",
            expires_at=now + timedelta(hours=2), activated_at=now - timedelta(minutes=2),
            created_at=now - timedelta(minutes=5), last_activity_at=now - timedelta(minutes=2),
        ))
        db.commit()
    finally:
        db.close()

    try:
        response = client.post("/api/arrival/status", json={"inviteToken": invite})
        assert response.status_code == 200
        body = response.json()
        assert body["active"] is True
        assert body["session"]["threadId"] == "arrival-legacy-thread"
        assert body["session"]["smsAccountKey"] == "primary"
        assert body["session"]["arrivalEventId"]
        assert body["session"]["nextAlertAt"]
    finally:
        _cleanup(booking_id)
