import json
from datetime import datetime, timedelta

import main


def _active(record_id, text, *, scope="primary", key="rule"):
    return {
        "id": record_id, "text": text, "canonical_key": key,
        "scope": scope, "sms_account_key": scope, "status": "active",
        "review_status": "approved", "retrieval_enabled": True,
        "revision": 1,
    }


def test_provider_context_binds_the_line_before_catalogue_and_knowledge(monkeypatch, tmp_path):
    (tmp_path / "line_1_services.json").write_text(json.dumps([{"id": "a", "name": "A", "price": 100, "duration": 30}]))
    (tmp_path / "line_2_services.json").write_text(json.dumps([{"id": "b", "name": "B", "price": 200, "duration": 60}]))
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "KNOWLEDGE_CHUNKS", [_active("a-rule", "Use A guidance."), _active("b-rule", "Use B guidance.", scope="secondary")])
    assert main.resolve_provider_context("primary")["sms_line"] == "primary"
    assert "B guidance" not in main.build_business_context("guidance", account_key="primary")
    assert "B" not in main.get_booking_tool_suite("primary").provider.list_services()[0]["name"]


def test_current_provider_template_resolves_only_its_catalogue(monkeypatch, tmp_path):
    (tmp_path / "line_1_services.json").write_text(json.dumps([{"id": "a", "name": "A", "price": 100, "duration": 30}]))
    (tmp_path / "line_2_services.json").write_text(json.dumps([{"id": "b", "name": "B", "price": 200, "duration": 60}]))
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    assert main.resolve_knowledge_template(
        "{service_name} costs {service_price} for {service_duration}.", "primary",
        conversation_values={"service_name": "A"},
    ) == "A costs 100 for 30."
    assert main.resolve_knowledge_template("{service_name}", "primary", conversation_values={"service_name": "B"}) is None


def test_fastapi_discovery_filters_catalogue_and_availability_to_bound_provider(monkeypatch):
    provider = main.FastAPIBookingsDiscoveryProvider("https://bookings.example", provider_id="provider-a")

    def payload(method, path, body=None):
        if path.endswith("bootstrap"):
            return {"ok": True, "data": {"services": [
                {"id": 1, "name": "A", "active": True, "provider_ids": ["provider-a"]},
                {"id": 2, "name": "B", "active": True, "provider_ids": ["provider-b"]},
            ]}}
        return {"ok": True, "data": [
            {"service": {"id": 1, "name": "A"}, "provider": {"id": "provider-a"}, "start_time": "2030-01-01T10:00:00+11:00", "end_time": "2030-01-01T10:30:00+11:00"},
            {"service": {"id": 2, "name": "B"}, "provider": {"id": "provider-b"}, "start_time": "2030-01-01T10:00:00+11:00", "end_time": "2030-01-01T11:00:00+11:00"},
        ]}

    monkeypatch.setattr(provider, "_request", payload)
    assert [item["name"] for item in provider.list_services()] == ["A"]
    slots = provider.search_availability("1", main.datetime(2030, 1, 1), main.datetime(2030, 1, 2), 10)
    assert [item["provider_id"] for item in slots] == ["provider-a"]


def test_sanitisation_whitelist_and_uncertain_literals_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    converted = main.sanitise_reusable_knowledge_template("The price is $120 for 45 minutes on Monday at 2pm.", "primary")
    assert converted == "The price is {service_price} for {service_duration} on {requested_date} at {requested_time}."
    assert main.sanitise_reusable_knowledge_template("Use {{ customer.name }}", "primary") is None
    assert main.sanitise_reusable_knowledge_template("Alice Smith is available at 2pm.", "primary") is None
    assert main.sanitise_reusable_knowledge_template("We are closed Monday at 2pm.", "shared") is None
    assert main.resolve_knowledge_template("We are closed Monday at 2pm.", "shared") is None


def test_shared_room_availability_requires_an_account_but_returns_the_same_busy_slots(monkeypatch):
    calendar = object.__new__(main.GoogleCalendarService)
    slots = [{"start": datetime(2030, 1, 1, 10), "end": datetime(2030, 1, 1, 11)}]
    calls = []

    def get_busy_slots(start, end, *, require_authoritative=False):
        calls.append(require_authoritative)
        return slots

    monkeypatch.setattr(calendar, "get_busy_slots", get_busy_slots)
    start = datetime(2030, 1, 1, 9)
    end = start + timedelta(hours=3)
    assert calendar.get_busy_slots_for_account(start, end, "primary", require_authoritative=True) == slots
    assert calendar.get_busy_slots_for_account(start, end, "secondary", require_authoritative=True) == slots
    assert calls == [True, True]
    try:
        calendar.get_busy_slots_for_account(start, end, "unknown", require_authoritative=True)
    except OSError:
        pass
    else:
        raise AssertionError("Unknown accounts must not query the shared calendar.")


def test_shared_calendar_booking_records_the_selected_account(monkeypatch):
    captured = {}

    class InsertRequest:
        def execute(self):
            return {"id": "shared-room-event"}

    class Events:
        def insert(self, *, calendarId, body):
            captured["calendar_id"] = calendarId
            captured["body"] = body
            return InsertRequest()

    class Service:
        def events(self):
            return Events()

    class Session:
        def merge(self, booking):
            captured["local_account"] = booking.sms_account_key

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    calendar = object.__new__(main.GoogleCalendarService)
    calendar._cache = {}
    calendar.service = Service()
    calendar.db_session_factory = Session
    start = datetime(2030, 1, 1, 10)
    assert calendar.create_booking("Customer - Service", start, start + timedelta(minutes=30), "+61400000000", "secondary") == "shared-room-event"
    private = captured["body"]["extendedProperties"]["private"]
    assert private["sms_account_key"] == "secondary"
    assert private["provider_name"] == main.resolve_provider_context("secondary")["provider_name"]
    assert captured["local_account"] == "secondary"


def test_equivalent_meaning_creates_no_candidate_or_queue_item(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "KNOWLEDGE_DIR", str(tmp_path))
    (tmp_path / main.LEARNED_INFORMATION_FILENAME).write_text(json.dumps(
        _active("day-rule", "Ask for the customer's preferred appointment day.")
    ) + "\n")
    assert main._upsert_learned_information_entry({
        "id": "duplicate", "source_type": "sms_pair_template", "scope": "primary", "sms_account_key": "primary",
        "canonical_key": "day-rule", "text": "What day suits you?", "status": "quarantined",
        "review_status": "pending", "retrieval_enabled": False,
    }) is False
    assert len(main.list_learned_information()) == 1


def test_new_addition_conflict_and_replacement_are_pending_actions(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "KNOWLEDGE_DIR", str(tmp_path))
    (tmp_path / main.LEARNED_INFORMATION_FILENAME).write_text(json.dumps(
        _active("policy", "Ask for the customer's preferred appointment day.", key="booking-policy")
    ) + "\n")
    base = {"scope": "primary", "sms_account_key": "primary", "canonical_key": "booking-policy"}
    assert main.classify_knowledge_candidate({**base, "text": "Also ask whether mornings or afternoons are preferred."}) == "material_addition"
    assert main.classify_knowledge_candidate({**base, "text": "Never ask for the preferred appointment day."}) == "conflict"
    assert main.classify_knowledge_candidate({**base, "text": "Replace the day question with a date question."}) == "replacement"
    assert main.classify_knowledge_candidate({**base, "canonical_key": "new", "text": "Thank customers after a confirmed booking."}) == "genuinely_new"


def test_maintenance_preview_does_not_mutate_active_knowledge(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "KNOWLEDGE_DIR", str(tmp_path))
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "KNOWLEDGE_CURATOR_STATE_PATH", str(tmp_path / "curator.json"))
    source = tmp_path / main.LEARNED_INFORMATION_FILENAME
    source.write_text(json.dumps(_active("dynamic", "The service costs $120.")) + "\n")
    before = source.read_text()
    monkeypatch.setattr(main, "openai_client", None)
    result = main.run_knowledge_curator()
    assert result["run"]["safe_repairs_completed"] == 0
    assert source.read_text() == before
