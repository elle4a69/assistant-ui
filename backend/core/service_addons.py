"""Formatting helpers for the live add-on catalogue."""

from __future__ import annotations

import json
import os
from typing import Any


def service_addons_variable_value(data_dir: str) -> str:
    """Return a current, template-ready summary of configured service add-ons."""
    try:
        with open(os.path.join(data_dir, "service_addons.json"), "r", encoding="utf-8") as handle:
            addons = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return ""
    if not isinstance(addons, list):
        return ""

    try:
        with open(os.path.join(data_dir, "services.json"), "r", encoding="utf-8") as handle:
            services = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        services = []
    service_names = {
        str(item.get("id") or "").strip(): str(item.get("name") or "").strip()
        for item in services
        if isinstance(item, dict) and item.get("isActive", True) is not False
    } if isinstance(services, list) else {}

    rendered: list[str] = []
    for addon in addons:
        if not isinstance(addon, dict):
            continue
        name = str(addon.get("name") or "").strip()
        if not name:
            continue
        try:
            price = max(0, int(addon.get("price") or 0))
            extra_time = max(0, int(addon.get("extraTime", addon.get("duration", 0)) or 0))
        except (TypeError, ValueError):
            continue
        details = [f"{name}: ${price}"]
        if extra_time:
            details.append(f"+{extra_time} minutes")
        description = " ".join(str(addon.get("description") or "").split())
        if description:
            details.append(description)
        compatible = [
            service_names.get(str(service_id).strip(), "")
            for service_id in addon.get("serviceIds", [])
        ]
        compatible = [name for name in compatible if name]
        if compatible:
            details.append(f"Available with: {', '.join(compatible)}")
        rendered.append(" — ".join(details))
    return "\n".join(rendered)
