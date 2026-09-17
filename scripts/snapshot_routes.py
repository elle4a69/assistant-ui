#!/usr/bin/env python3
"""
Route Snapshot Utility
Captures the authoritative baseline of all registered routes in backend.main.app.
Exports the snapshot to docs/refactor/route_baseline.json.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

# Ensure repository root and backend directory are in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "backend"))

from fastapi.routing import APIRoute, APIWebSocketRoute
from starlette.routing import Mount, Route


def extract_route_metadata(route: Any) -> Dict[str, Any]:
    """Extract structured metadata from a FastAPI / Starlette route."""
    route_type = type(route).__name__
    path = getattr(route, "path", "")
    name = getattr(route, "name", "")

    # Methods
    if isinstance(route, APIWebSocketRoute):
        methods = ["WEBSOCKET"]
    elif hasattr(route, "methods") and route.methods:
        methods = sorted(list(route.methods))
    else:
        methods = []

    # Endpoint name
    if hasattr(route, "endpoint") and route.endpoint is not None:
        endpoint = getattr(route.endpoint, "__name__", str(route.endpoint))
    elif isinstance(route, Mount):
        endpoint = getattr(route.app, "__name__", type(route.app).__name__)
    else:
        endpoint = name or ""

    # Dependencies (route-level and parameter-level)
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

    # Response model
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


def snapshot_routes() -> List[Dict[str, Any]]:
    """Load backend.main.app and snapshot all routes."""
    from backend.main import app

    routes_data = []
    for r in app.routes:
        routes_data.append(extract_route_metadata(r))
    return routes_data


def main():
    output_dir = REPO_ROOT / "docs" / "refactor"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "route_baseline.json"

    print(f"Inspecting routes from backend.main.app...")
    routes_data = snapshot_routes()
    print(f"Total routes captured: {len(routes_data)}")

    # Method and type breakdown
    type_counts: Dict[str, int] = {}
    method_counts: Dict[str, int] = {}
    for r in routes_data:
        t = r["route_type"]
        type_counts[t] = type_counts.get(t, 0) + 1
        for m in r["methods"]:
            method_counts[m] = method_counts.get(m, 0) + 1

    print("\nRoute type breakdown:")
    for t, c in sorted(type_counts.items()):
        print(f"  {t}: {c}")

    print("\nHTTP method breakdown:")
    for m, c in sorted(method_counts.items()):
        print(f"  {m}: {c}")

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(routes_data, f, indent=2, ensure_ascii=False)

    print(f"\nSuccessfully wrote route snapshot to {output_file}")


if __name__ == "__main__":
    main()
