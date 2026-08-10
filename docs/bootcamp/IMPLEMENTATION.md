# Boot Camp Implementation Plan

## Phase 1: Backend foundation

- Add Boot Camp run, conversation, and message tables.
- Add personas and reviewed JSONL opening-message loader.
- Add style-profile rendering, apply, and undo storage.
- Add isolated Tori and persona generation functions.
- Add start, pause, resume, stop, reset, status, and thread APIs.
- Add regression tests for isolation, profile persistence, and handoff behavior.

Success: simulations persist and cannot send SMS or create bookings.

## Phase 2: Mobile-first interface

- Add Boot Camp navigation and route.
- Add persona selection, run controls, turn count, and trait sliders.
- Add live polling and persistent per-persona message threads.
- Add handoff and failure indicators.
- Add Apply to Tori and Undo controls.

Success: the full workflow is usable on phone and desktop.

## Phase 3: Verification and release

- Run backend tests and frontend type/build checks.
- Run a bounded local simulation.
- Verify no live customer, SMS, or calendar records change.
- Deploy and smoke-test production.

Success: production loads the page and existing messaging remains healthy.
