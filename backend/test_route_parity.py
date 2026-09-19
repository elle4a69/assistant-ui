"""
Route Parity Test Suite
Validates that live routes registered in backend.main.app maintain 100% parity
with the authoritative Phase 0 baseline (docs/refactor/route_baseline.json).
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute
from starlette.routing import Mount, Route

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / "docs" / "refactor" / "route_baseline.json"


def extract_route_metadata(route: Any) -> Dict[str, Any]:
    """Extract structured metadata from a FastAPI / Starlette route."""
    route_type = type(route).__name__
    path = getattr(route, "path", "")
    name = getattr(route, "name", "")

    if isinstance(route, APIWebSocketRoute):
        methods = ["WEBSOCKET"]
    elif hasattr(route, "methods") and route.methods:
        methods = sorted(list(route.methods))
    else:
        methods = []

    if hasattr(route, "endpoint") and route.endpoint is not None:
        endpoint = getattr(route.endpoint, "__name__", str(route.endpoint))
    elif isinstance(route, Mount):
        endpoint = getattr(route.app, "__name__", type(route.app).__name__)
    else:
        endpoint = name or ""

    dep_names = set()
    if hasattr(route, "dependencies") and route.dependencies:
        for d in route.dependencies:
            call = getattr(d, "call", d)
            dep_names.add(getattr(call, "__name__", str(call)))

    if hasattr(route, "dependant") and route.dependant and hasattr(route.dependant, "dependencies"):
        for d in route.dependant.dependencies:
            call = getattr(d, "call", d)
            dep_names.add(getattr(call, "__name__", str(call)))

    dependencies = sorted(list(dep_names))

    response_model = None
    if hasattr(route, "response_model") and route.response_model is not None:
        if hasattr(route.response_model, "__name__"):
            response_model = route.response_model.__name__
        else:
            response_model = str(route.response_model)

    return {
        "path": path,
        "name": name,
        "route_type": route_type,
        "methods": methods,
        "endpoint": endpoint,
        "dependencies": dependencies,
        "response_model": response_model,
    }


def get_live_routes() -> List[Dict[str, Any]]:
    """Inspect and extract metadata for all live routes."""
    from backend.main import app

    def expand(routes: List[Any]):
        for route in routes:
            # FastAPI 0.116+ may retain include_router() calls as lazy
            # _IncludedRouter wrappers. The contract is the wrapped routes,
            # not those implementation-detail placeholders.
            original_router = getattr(route, "original_router", None)
            if original_router is not None:
                yield from expand(list(original_router.routes))
            else:
                yield route

    return [extract_route_metadata(r) for r in expand(list(app.routes))]


def load_baseline_routes() -> List[Dict[str, Any]]:
    """Load the recorded route baseline from JSON."""
    assert BASELINE_PATH.exists(), f"Route baseline file not found: {BASELINE_PATH}"
    with open(BASELINE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def test_baseline_file_exists():
    """Verify that docs/refactor/route_baseline.json exists and is valid JSON."""
    assert BASELINE_PATH.exists()
    baseline = load_baseline_routes()
    assert len(baseline) > 0, "Route baseline is empty"


def test_route_count_parity():
    """Verify that live route count matches the authoritative baseline exactly."""
    baseline = load_baseline_routes()
    live = get_live_routes()
    assert len(live) == len(
        baseline
    ), f"Route count mismatch: live={len(live)}, baseline={len(baseline)}"


def test_route_definitions_parity():
    """
    Assert that 100% of routes in the baseline exist in live routes
    with identical path, methods, endpoint, dependencies, and response_model.
    """
    baseline = load_baseline_routes()
    live = get_live_routes()

    # Index live routes by (path, tuple(methods))
    live_map = {}
    for r in live:
        key = (r["path"], tuple(sorted(r["methods"])))
        live_map[key] = r

    missing_routes = []
    mismatched_routes = []

    for b in baseline:
        key = (b["path"], tuple(sorted(b["methods"])))
        if key not in live_map:
            missing_routes.append(b)
        else:
            l = live_map[key]
            differences = {}
            if l["endpoint"] != b["endpoint"]:
                differences["endpoint"] = (b["endpoint"], l["endpoint"])
            if l["dependencies"] != b["dependencies"]:
                differences["dependencies"] = (b["dependencies"], l["dependencies"])
            if l["response_model"] != b["response_model"]:
                differences["response_model"] = (b["response_model"], l["response_model"])
            if l["route_type"] != b["route_type"]:
                differences["route_type"] = (b["route_type"], l["route_type"])

            if differences:
                mismatched_routes.append({
                    "route": key,
                    "differences": differences
                })

    assert not missing_routes, f"Missing routes from live app: {missing_routes}"
    assert not mismatched_routes, f"Route contract mismatches detected: {mismatched_routes}"


def test_no_untracked_live_routes():
    """Verify that no unexpected routes exist in the live app that are absent from the baseline."""
    baseline = load_baseline_routes()
    live = get_live_routes()

    baseline_keys = {(b["path"], tuple(sorted(b["methods"]))) for b in baseline}
    extra_routes = []

    for l in live:
        key = (l["path"], tuple(sorted(l["methods"])))
        if key not in baseline_keys:
            extra_routes.append(l)

    assert not extra_routes, f"Live app has untracked routes not in baseline: {extra_routes}"
