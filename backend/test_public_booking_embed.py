from starlette.requests import Request

from main import is_public_request


def request_for(path: str, method: str = "GET") -> Request:
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 443),
    })


def test_booking_embed_assets_are_public():
    assert is_public_request(request_for("/booking-inline.js"))
    assert is_public_request(request_for("/widget.js"))
    assert is_public_request(request_for("/v2"))
    assert is_public_request(request_for("/v2/"))
    assert is_public_request(request_for("/v2/booking"))


def test_live_booking_embed_api_is_public():
    assert is_public_request(request_for("/api/services"))
    assert is_public_request(request_for("/api/calendar/freebusy"))
    assert is_public_request(request_for("/api/calendar/bookings", method="POST"))
    assert is_public_request(request_for("/api/calendar/bookings", method="OPTIONS"))


def test_booking_admin_writes_remain_private():
    assert not is_public_request(request_for("/api/services", method="POST"))


def test_similarly_named_private_route_is_not_public():
    assert not is_public_request(request_for("/v2-admin"))
