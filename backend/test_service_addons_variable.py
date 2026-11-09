from backend.core.service_addons import service_addons_variable_value


def test_service_addons_variable_uses_the_live_catalogue(tmp_path):
    (tmp_path / "services.json").write_text(
        '[{"id":"service-a","name":"Service A"},{"id":"service-b","name":"Hidden","isActive":false}]',
        encoding="utf-8",
    )
    (tmp_path / "service_addons.json").write_text(
        '[{"id":"addon-a","name":"Add-on A","description":"Extra detail","price":25,"extraTime":15,"serviceIds":["service-a","service-b"]}]',
        encoding="utf-8",
    )

    assert service_addons_variable_value(str(tmp_path)) == (
        "Add-on A: $25 — +15 minutes — Extra detail — Available with: Service A"
    )
