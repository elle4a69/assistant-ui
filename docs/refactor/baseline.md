# Phase 0 Baseline Report & Route Contract Verification

## Executive Summary
This document establishes the authoritative, empirical, and non-negotiable behavioural baseline for the strangler fig refactor of `backend/main.py`, executed in accordance with Phase 0 of `Anti-Gravity_Strangler_Refactor_Brief.md`.

No existing production or test code was altered. All baseline measurements reflect the exact state of the repository prior to structural strangulation.

---

## 1. Repository & Runtime Metadata

- **Date of Baseline Freeze:** 2026-09-16
- **Repository Root:** `F:\Projects\assistant-ui`
- **Git Commit Hash (`HEAD`):** `c9f6eb3fed0061693683a285b0b3774fadf19072`
- **Python Runtime:** Python 3.11.9 (tags/v3.11.9:de5405b, Apr 2 2024, 10:12:12) [MSC v.1938 64 bit (AMD64)]
- **Platform:** Windows 11
- **Key Test Frameworks:**
  - `pytest` 8.3.3
  - `pytest-asyncio` 0.24.0
  - `hypothesis` 6.165.10
  - `pydantic` 2.13.0
  - `fastapi` 0.115.0+
  - `sqlalchemy` 2.0.x
  - `psycopg2` / `pgvector`

### Pre-Refactor Working Directory Status (`git status -s`)
```text
 M backend/booking_tools.py
 M backend/main.py
 M backend/test_phase7_ui_visibility.py
 M backend/test_rag_architecture.py
?? backend/config/
?? backend/curator/
?? backend/knowledge/
?? backend/observability/
?? backend/test_curator_autonomy.py
?? backend/test_knowledge_gaps.py
?? backend/test_knowledge_migration.py
?? backend/test_knowledge_repository.py
?? backend/test_observability.py
?? backend/test_route_parity.py
?? backend/test_semantic_retrieval.py
?? backend/test_tenant_timezone.py
?? docs/refactor/
?? scripts/
```

---

## 2. Test Suite Execution & Verification

### Complete Repository Test Discovery
- **Discovery Command:** `python -m pytest --collect-only`
- **Pre-parity Test Count:** 572 tests
- **Post-parity Test Count:** 576 tests (including 4 new route parity tests in `backend/test_route_parity.py`)

### Full Test Suite Execution Results
- **Execution Command:** `python -m pytest -v`
- **Execution Duration:** 40.55s
- **Total Executed:** 576
- **Passed:** 576 (100%)
- **Failed:** 0
- **Skipped:** 0
- **Warnings:** 19

### Modular & Domain Sub-Suites Verification
Command:
```bash
python -m pytest backend/test_curator_autonomy.py \
                 backend/test_knowledge_gaps.py \
                 backend/test_semantic_retrieval.py \
                 backend/test_knowledge_repository.py \
                 backend/test_knowledge_migration.py \
                 backend/test_tenant_timezone.py \
                 backend/test_observability.py \
                 backend/test_knowledge_curator.py \
                 backend/test_knowledge_integrity.py \
                 backend/test_knowledge_hygiene_guardrails.py \
                 backend/test_rag_architecture.py \
                 backend/test_conversational_booking.py \
                 backend/test_route_parity.py -v
```
- **Execution Duration:** 17.54s
- **Total Domain Tests:** 254 passed, 0 failed

#### Breakdown by Domain Suite
| Test Module | Tests Passed | Status |
| :--- | :--- | :--- |
| `backend/test_curator_autonomy.py` | 12 | PASS |
| `backend/test_knowledge_gaps.py` | 6 | PASS |
| `backend/test_semantic_retrieval.py` | 14 | PASS |
| `backend/test_knowledge_repository.py` | 7 | PASS |
| `backend/test_knowledge_migration.py` | 13 | PASS |
| `backend/test_tenant_timezone.py` | 7 | PASS |
| `backend/test_observability.py` | 21 | PASS |
| `backend/test_knowledge_curator.py` | 74 | PASS |
| `backend/test_knowledge_integrity.py` | 22 | PASS |
| `backend/test_knowledge_hygiene_guardrails.py` | 9 | PASS |
| `backend/test_rag_architecture.py` | 15 | PASS |
| `backend/test_conversational_booking.py` | 50 | PASS |
| `backend/test_route_parity.py` | 4 | PASS |
| **Total Domain Suite** | **254** | **PASS** |

---

## 3. Route Inventory & Parity Contract

### Route Baseline Snapshot
An automated introspection script (`scripts/snapshot_routes.py`) was executed to inspect `app.routes` on `backend.main.app` and export the complete route table to `docs/refactor/route_baseline.json`.

- **Total Registered Routes:** **125**
- **Snapshot File:** `docs/refactor/route_baseline.json`

### Route Type Distribution
| Route Type | Count | Description |
| :--- | :--- | :--- |
| `fastapi.routing.APIRoute` | 119 | Standard REST endpoints |
| `fastapi.routing.APIWebSocketRoute` | 1 | Realtime WebSocket endpoint (`/api/settings/operations-chat/realtime/ws`) |
| `starlette.routing.Mount` | 1 | Static file mount (`/assets`) |
| `starlette.routing.Route` | 4 | Built-in OpenAPI & Swagger documentation routes (`/openapi.json`, `/docs`, `/docs/oauth2-redirect`, `/redoc`) |
| **Total** | **125** | |

### HTTP Method Breakdown
| HTTP Method | Count |
| :--- | :--- |
| `POST` | 65 |
| `GET` | 44 |
| `DELETE` | 9 |
| `PUT` | 4 |
| `HEAD` | 4 |
| `PATCH` | 1 |
| `WEBSOCKET` | 1 |

### Route Dependencies & Response Models
- **Routes with Injected Dependencies (`get_db`):** 52 endpoints
- **Explicit Route-Level `response_model`:** 0 (all routes return direct dictionaries, standard FastAPI/Starlette Responses, or StreamingResponses)

### Automated Route Parity Suite (`backend/test_route_parity.py`)
Four automated contract tests now gate all refactoring batches:
1. `test_baseline_file_exists`: Validates presence and non-emptiness of `route_baseline.json`.
2. `test_route_count_parity`: Asserts exact equality between live route count and the 125 baseline routes.
3. `test_route_definitions_parity`: Verifies path, HTTP methods, endpoint handler callable name, dependencies, and response models.
4. `test_no_untracked_live_routes`: Guarantees no unexpected or phantom routes are registered without documentation.

---

## 4. System Invariants Verification

### 1. Deterministic Knowledge Authority Hierarchy
- Hierarchy strictly enforced:
  `owner_override` (1.0) > `operator_entry` (0.8) > `canonical_knowledge` (0.6) > `conversation_learned` (0.4) > `external_spec` (0.2)
- Verified by: `test_knowledge_curator.py`, `test_semantic_retrieval.py`

### 2. Fail-Closed Conflict Handling
- If two or more active facts of equal authority conflict on a canonical key, both are excluded from retrieval context and an alert is flagged.
- Verified by: `test_semantic_retrieval.py::test_conflicting_active_records_result_in_none_fail_closed`

### 3. Tenant & Account Isolation
- Multi-tenant data segregation verified across lines (`primary`, `secondary`, `shared`).
- Verified by: `test_knowledge_repository.py`, `test_tenant_timezone.py::test_6_cross_timezone_tenant_isolation_across_midnight`

### 4. Australia/Melbourne & Tenant Timezone Boundaries
- IANA timezone validation (`Australia/Melbourne`, `Australia/Sydney`, etc.) with strict rejection of unconfigured tenants (no silent UTC fallback).
- Tested across Southern and Northern Hemisphere DST transitions (e.g. AEDT <-> AEST).
- Verified by: `backend/test_tenant_timezone.py` (7/7 passing), `backend/test_booking_timezone.py` (3/3 passing)

### 5. PostgreSQL 16 + pgvector Live Integration
- Container `chatwoot-postgres-1` (`pgvector/pgvector:pg16`) active on `127.0.0.1:5432`.
- Extension `vector` (version `0.8.4`) installed on PostgreSQL 16.14.
- Live DDL creation of `knowledge_records` with `VECTOR(1536)`, vector insertion, round-trip retrieval, and deletion executed successfully.

### 6. Staged Migration & Dual-Write Parity
- Dual-write manager (`KnowledgeDualWriteManager`) tested with simulated failures, anti-PII scrubbing in failure telemetry, thread-safe concurrent failures across 20 worker threads, and reconciliation against JSONL archives.
- Verified by: `backend/test_knowledge_migration.py` (13/13 passing)

### 7. Conversational Booking Safety
- Strict prohibition of secondary booking confirmations without real-time calendar availability verification.
- Prohibits fake composite time slot assembly (e.g. combining two 30-minute slots to fulfill a 60-minute request).
- Verified by: `backend/test_conversational_booking.py` (50/50 passing)

---

## 5. Startup & Import Behaviour Analysis

- **Import Cleanliness:** `from backend.main import app` imports cleanly and registers 125 routes when project root and `backend` are in `sys.path`.
- **Database Bootstrap:** SQLite tables and seed data initialize automatically on import.
- **Service Account & API Clients:** Google Calendar API and OpenAI clients initialize on module import.
- **Architectural Debt Identified:**
  - `backend/main.py` is currently ~16,750 lines.
  - 4 fragmented `@app.on_event("startup")` decorators exist in `main.py` (lines 8293, 13292, 13318, 15500) rather than a unified `lifespan(app: FastAPI)` context manager.
  - `backend/curator/compat.py` bridges into `sys.modules["main"]` for compatibility access.

---

## 6. Deprecation Warnings Inventory

The test suite emits **19 deprecation warnings** during execution. These are recorded here to ensure they are tracked and addressed during the strangler refactoring:

| Location | Warning Type | Description / Remediation |
| :--- | :--- | :--- |
| `starlette.formparsers:12` | `PendingDeprecationWarning` | Recommends `import python_multipart` instead of `multipart`. |
| `backend/main.py:4514` | `PydanticDeprecatedSince20` | `WebhookSMSInput(BaseModel)` uses class-based `config`. Must migrate to `model_config = ConfigDict(...)`. |
| `backend/main.py:8293` | `DeprecationWarning` | `@app.on_event("startup")` is deprecated. Must migrate to FastAPI `lifespan`. |
| `fastapi/applications.py:4495` | `DeprecationWarning` | Router-level `on_event` invocations (4 occurrences). Must migrate to unified lifespan. |
| `backend/main.py:13292` | `DeprecationWarning` | `@app.on_event("startup")` is deprecated. Must migrate to FastAPI `lifespan`. |
| `backend/main.py:13318` | `DeprecationWarning` | `@app.on_event("startup")` is deprecated. Must migrate to FastAPI `lifespan`. |
| `backend/main.py:15500` | `DeprecationWarning` | `@app.on_event("startup")` is deprecated. Must migrate to FastAPI `lifespan`. |
| `pytest_asyncio/plugin.py:208` | `PytestDeprecationWarning` | Unset `asyncio_default_fixture_loop_scope`. Can be set in `pytest.ini`. |

---

## 7. Baseline Invariant Gate

This baseline establishes the invariant:

$$\text{Post-Refactor Passing Tests} \ge 576$$
$$\text{Active Routes} = 125$$
$$\text{Failing Tests} = 0$$

Any refactoring batch that violates this gate blocks progression until resolved.
