# Boot Camp Research

## Overview

Build an admin-only multi-persona simulator inside the current assistant UI. Each
persona holds a persistent, mobile-style thread against the real Tori response
pipeline. Simulations are isolated from SMS and calendar side effects.

## Problem Statement

Manual simulator testing is repetitive and does not cover varied customer styles.
Earlier projects contained useful persona concepts and interfaces, but their
training engines were either demos, used a generic prompt, or did not score results.

## User Stories

- Select several difficult customer personas and watch their threads update live.
- Seed realistic opening messages from the reviewed JSONL corpus.
- Adjust Tori style traits for a test run without changing production.
- Pause, resume, stop, and reset a run.
- See refusals, contradictions, and uncertainty handoffs clearly.
- Apply a successful complete style profile to Tori and undo it later.

## Recommended Approach

Add isolated Boot Camp tables and APIs to the existing FastAPI/SQLite backend, plus
a responsive React page. Use the existing Responses API client with manually managed
thread history and `store=False`. Use Tori's current system prompt, live service
settings, business context, and reviewed style examples. Do not call SMS or booking
tools during simulations.

Alternative approaches rejected:

- Repair `bookings_ai_agent`: it tests a separate generic agent and has approval-mode bugs.
- Restore the original `bookings` UI: its main training endpoint is explicitly a demo.
- Reuse live SMS threads: risks real sends, bookings, and polluted customer history.

## Data Requirements

- Persona definitions (name, description, prompt, category).
- Run configuration and status.
- One conversation and ordered messages per selected persona.
- Session style profile and evaluation flags.
- Active and previous production style profiles in persistent JSON files.

## UI/UX

- Mobile: persona list, then one full conversation thread.
- Desktop: controls beside a responsive thread grid.
- One slider set for flirtiness, cheerfulness, wit, sarcasm, warmth, directness,
  chattiness, and patience.
- Explicit Apply to Tori and Undo actions.

## Risks and Mitigations

- Real-world side effects: use a pure simulation response path with no tools.
- API cost: selectable personas, bounded turns, maximum six concurrent workers.
- False learning claims: label this as testing; it does not update model weights.
- Unsafe guessing: flag uncertainty and stop that persona thread for human review.
- Weak retrieval: use reviewed opening-message records and no random style fallback.

## Decisions

- Boot Camp lives in `assistant-ui`.
- Tori's base prompt remains unchanged; traits are a separate overlay.
- Apply publishes the entire tested profile atomically; Undo restores the previous one.
- Missing or uncertain business answers stop and request human review.
- No real SMS or calendar actions occur in Boot Camp.

## Reference

- OpenAI Responses API supports developer instructions and manually supplied message
  history; the application already uses this pattern with `store=False`.
