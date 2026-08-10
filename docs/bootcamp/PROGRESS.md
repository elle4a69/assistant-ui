# Boot Camp Progress

## Status: Released

## Decisions

- Current app is the owner; old projects are reference material only.
- Simulations use separate tables and a side-effect-free generation path.
- Style settings are session-local until an explicit atomic Apply.
- Undo restores the immediately previous production profile.
- Default concurrency is capped at six personas.

## Phase Progress

### Phase 1: Backend foundation

Status: Complete

- Separate SQLite simulation store and side-effect-free model path.
- Twelve strong personas, reviewed JSONL openings, pause/resume/stop controls.
- Explicit uncertainty handoff and atomic production profile apply/undo.
- Unapplied slider defaults are provably excluded from live Tori instructions.

### Phase 2: Mobile-first interface

Status: Complete

- Responsive persona selector, independent message threads, live polling and controls.
- Eight behavioral sliders with deliberate Apply to Tori and Undo actions.

### Phase 3: Verification and release

Status: Complete

- 13 focused backend tests pass.
- Production frontend build passes.
- One-turn live-model simulation completed with a correct human handoff.
- Main inbox thread count remained unchanged (zero before and after simulation).
- Deployed to `assistant-ui-hub` in Sydney; Fly machine health check passed.
- Production `/bootcamp`, persona API and profile API return successfully.
- Production reports `isApplied: false`, so the visible defaults have not changed live Tori.
- Message commits are globally rate-limited to one every 2.5 seconds by default, preventing multi-persona UI bursts.

## Files Changed

- `docs/bootcamp/RESEARCH.md`
- `docs/bootcamp/IMPLEMENTATION.md`
- `docs/bootcamp/PROGRESS.md`
- `backend/bootcamp.py`
- `backend/test_bootcamp.py`
- `backend/main.py`
- `frontend/src/api.ts`
- `frontend/src/BootcampView.tsx`
- `frontend/src/App.tsx`
