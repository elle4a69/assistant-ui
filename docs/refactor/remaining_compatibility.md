# Architectural Record: Remaining Compatibility Bridges & Dynamic Reflection

**Date:** 2026-09-17  
**Status:** Canonical & Active  
**Author:** Strangler Fig Refactoring Team  
**Governing Refactor Brief:** Sections 15 & 16, Tactical Refinements (Section 45)  

---

## 1. Executive Summary

As part of the Strangler Fig refactoring of `backend/main.py` (reducing it from 16,446 lines to 223 lines), all domain logic, models, schemas, routers, and infrastructure clients were extracted into modular packages:
- `backend/core/` (config, constants, database, clients, state, utils)
- `backend/models/` (SQLAlchemy ORM models)
- `backend/schemas/` (Pydantic models)
- `backend/services/` (domain services)
- `backend/routes/` (domain routers)
- `backend/curator/` (knowledge curation & governance)

Across the repository's 576 automated test suites (comprising over 660+ assertions), many legacy unit tests monkeypatch globals directly on the top-level module (e.g., `monkeypatch.setattr(main, "openai_client", mock_client)`, `monkeypatch.setattr("main.LINE_PROFILES_PATH", tmp_path)`).

To guarantee **100% backward compatibility** and **zero regression** without rewriting hundreds of historical unit tests, targeted compatibility bridges have been retained. This document provides the formal architectural rationale, inventory, and lifecycle guidance for these bridges.

---

## 2. Inventory of Compatibility Bridges

### 2.1 Re-Export Façade in `backend/main.py`
`backend/main.py` acts as a pure composition root (< 300 lines) and re-exports all extracted symbols:
- ORM Models (`Thread`, `Message`, `CalendarEvent`, `ArrivalSession`, etc.)
- Pydantic Schemas (`ReplyInput`, `WebhookSMSInput`, `SettingsUpdateInput`, etc.)
- Core Infrastructure Singletons (`engine`, `SessionLocal`, `get_db`, `openai_client`, `calendar_service`, `mobilemessage_service`)
- Concurrency Locks & Global State (`LEARNED_INFORMATION_LOCK`, `SMS_REPLY_THREAD_LOCKS`, `OUTBOUND_SMS_SEND_LOCK`, `KNOWLEDGE_CHUNKS`)
- Domain Service Functions (`run_sms_reply_logic`, `load_knowledge_base`, `_upsert_learned_information_entry`, etc.)

**Rationale:**  
External consumers and test runners importing `from backend.main import ...` or `import backend.main as main` continue to observe the exact object identities (`is` identity) and module attributes they expect.

### 2.2 Bidirectional Module Aliasing (`main` <-> `backend.main`)
In `backend/main.py`:
```python
if "main" not in sys.modules:
    sys.modules["main"] = sys.modules[__name__]
if "backend.main" not in sys.modules:
    sys.modules["backend.main"] = sys.modules[__name__]
```
**Rationale:**  
Different test runners and pytest execution contexts invoke tests either with the project root in `PYTHONPATH` (`import main`) or with package-relative imports (`import backend.main`). The bidirectional alias ensures that mutations or `monkeypatch.setattr` calls applied to `main` are immediately visible to `backend.main` and vice-versa.

### 2.3 Dynamic Resolution Bridge: `_dyn(name, fallback)` (`backend/core/utils.py`)
In `backend/services/` (e.g., `booking_service.py`, `sms_service.py`, `operations_service.py`):
```python
def _dyn(name: str, fallback: object = None) -> object:
    # Priority 1: Check if main or backend.main was monkeypatched (val differs from fallback)
    for mod_name in ("main", "backend.main"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, name):
            val = getattr(mod, name)
            if fallback is not None and val is not fallback:
                return val
    # Priority 2: Return from main or backend.main if attribute exists
    for mod_name in ("main", "backend.main"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, name):
            return getattr(mod, name)
    # Priority 3: Fallback if supplied
    if fallback is not None:
        return fallback
    # Priority 4: Search service modules
    ...
```
**Rationale:**  
In production, services execute their canonical implementations directly (passed as `fallback`). If a test monkeypatches `main.openai_client` or a service function, `_dyn()` detects that the attribute on `main` differs from the fallback and routes to the monkeypatched mock. This cleanly separates production execution from test harness overrides without tight coupling.

### 2.4 Curator Dynamic Compatibility: `get_main_attr(name, fallback)` (`backend/curator/compat.py`)
In `backend/curator/`:
`get_main_attr` checks `sys.modules["main"]` and `sys.modules["backend.main"]` for overridden configuration paths or functions (such as `KNOWLEDGE_DIR`, `FIRST_CONTACT_ACCOUNT_KEYS`, `KNOWLEDGE_CURATOR_LOCK`). If not overridden, it falls back to canonical modules (`backend.core.config`, `backend.core.state`, `backend.core.clients`, `backend.services.learning_service`).

---

## 3. Invariants & Safety Guarantees

1. **Neutral Leaf Invariant (Section 45 Tactical Refinement 1):**
   None of the leaf modules (`backend/core/config.py`, `backend/core/database.py`, `backend/models/domain.py`, `backend/schemas/domain.py`) use `_dyn()` or import from `backend.main`. They remain pure DAG leaf nodes.
2. **Production Zero-Overhead:**
   In normal production execution, `sys.modules["main"]` points to the composition root, whose attributes match the canonical service objects. Dynamic lookup incurs sub-microsecond dictionary retrieval overhead with zero observable latency impact.
3. **Determinism:**
   When no monkeypatches exist, the canonical functions in `backend/services/` execute deterministically with direct references.

---

## 4. Migration & Deprecation Roadmap

These compatibility shims are intentional and permanent for the lifespan of legacy tests that rely on root-level monkeypatching. If future refactor phases modernize the unit tests to mock via dependency injection or direct service patching (e.g., `monkeypatch.setattr(backend.services.booking_service, ...)`), the dynamic fallbacks can be progressively phased out. Until then, they serve as the load-bearing bridge ensuring continuous 576/576 test pass rates.
