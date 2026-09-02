from starlette.requests import Request

import main


def request_for(path: str, method: str = "GET") -> Request:
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
    })


def test_portal_pages_can_load_the_login_shell_without_an_admin_cookie():
    expected_paths = {
        "/agent-console",
        "/arrivals",
        "/bookings",
        "/bootcamp",
        "/chat",
        "/settings",
        "/sim",
    }

    assert main.PORTAL_SPA_PATHS == expected_paths
    assert all(main.is_public_request(request_for(path)) for path in expected_paths)


def test_portal_write_requests_and_admin_apis_remain_protected():
    assert not main.is_public_request(request_for("/arrivals", "POST"))
    assert not main.is_public_request(request_for("/api/threads"))
    assert not main.is_public_request(request_for("/api/settings"))
