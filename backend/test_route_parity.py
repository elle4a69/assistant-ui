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
    # Recent FastAPI versions represent routes from ``include_router`` as
    # effective route contexts instead of copying them into ``app.routes``.
    # The context contains the combined prefix/dependencies, while the
    # original route still identifies the public route type recorded in the
    # baseline.
    original_route = getattr(route, "original_route", route)
    metadata_route = route
    if not isinstance(original_route, APIRoute):
        metadata_route = getattr(route, "starlette_route", None) or original_route
    route_type = type(original_route).__name__
    path = getattr(metadata_route, "path", "")
    name = getattr(metadata_route, "name", "")

    if isinstance(original_route, APIWebSocketRoute):
        methods = ["WEBSOCKET"]
    elif hasattr(metadata_route, "methods") and metadata_route.methods:
        methods = sorted(list(metadata_route.methods))
    else:
        methods = []

    if hasattr(metadata_route, "endpoint") and metadata_route.endpoint is not None:
        endpoint = getattr(metadata_route.endpoint, "__name__", str(metadata_route.endpoint))
    elif isinstance(metadata_route, Mount):
        endpoint = getattr(metadata_route.app, "__name__", type(metadata_route.app).__name__)
    else:
        endpoint = name or ""

    dep_names = set()
    if hasattr(metadata_route, "dependencies") and metadata_route.dependencies:
        for d in metadata_route.dependencies:
            call = getattr(d, "call", d)
            dep_names.add(getattr(call, "__name__", str(call)))

    if hasattr(metadata_route, "dependant") and metadata_route.dependant and hasattr(metadata_route.dependant, "dependencies"):
        for d in metadata_route.dependant.dependencies:
            call = getattr(d, "call", d)
            dep_names.add(getattr(call, "__name__", str(call)))

    dependencies = sorted(list(dep_names))

    response_model = None
    if hasattr(metadata_route, "response_model") and metadata_route.response_model is not None:
        if hasattr(metadata_route.response_model, "__name__"):
            response_model = metadata_route.response_model.__name__
        else:
            response_model = str(metadata_route.response_model)

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

    live_routes = []
    for route in app.routes:
        effective_route_contexts = getattr(route, "effective_route_contexts", None)
        if callable(effective_route_contexts):
            live_routes.extend(
                extract_route_metadata(context)
                for context in effective_route_contexts()
            )
        else:
            live_routes.append(extract_route_metadata(route))
    return live_routes


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
