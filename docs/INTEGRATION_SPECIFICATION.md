# Assistant UI — Integration Specification

**Purpose:** technical specification for a system that needs to evaluate, integrate with, or replace part of Assistant UI.

**Evidence basis:** source implementation in this repository as inspected on 23 September 2026. This document describes implemented behaviour. Where a capability is configured, partial, or planned, it is identified explicitly rather than presented as a production guarantee.

## 1. Product definition

Assistant UI is a single-tenant, authenticated operations application for a service business. Its principal job is to receive customer SMS messages, retain a conversation record, use an AI responder for customer support and appointment discovery, and give staff a controlled console to review, intervene, book, and audit activity.

It also includes:

- a public booking form/embed and calendar management workspace;
- an SMS simulator and a Boot Camp simulator for safe testing/training;
- appointment-arrival links and two-way arrival chat;
- knowledge/RAG administration and an autonomous knowledge curator;
- an owner-facing business/operations AI chat, optional realtime voice, and a bounded coding-agent console;
- configuration for services, availability, prompts, SMS transport, customer controls, and exports.

The application is deliberately not a general CRM or an unrestricted autonomous agent. Customer-facing answers are constrained by live availability, knowledge-scoping rules, human review modes, and explicit booking confirmation. Operations autonomy is separately restricted by authenticated, allowlisted actions, persistent audit events, sanitisation, and human-controlled release paths.

## 2. Architecture at a glance

```text
Customer / SMS provider                 Staff browser / public visitor
          |                                         |
          | POST /webhooks/sms                       | React SPA / public booking or arrival route
          v                                         v
  SMS orchestration  <---------------------->  FastAPI HTTP + WebSocket API
          |                                           |
          +--> SQLite conversation/audit store <------+--> persistent files (settings/prompts/knowledge)
          |                 |
          |                 +--> calendar service (Google Calendar, with local SQLite fallback)
          |                 +--> MobileMessage SMS gateway (when configured)
          |                 +--> OpenAI Responses / Realtime APIs (when configured)
          |                 +--> optional FastAPI Bookings service for discovery only
          |
          +--> staff review, learning, curator, notifications and bounded operations flows
```

### Technology and deployment

| Layer | Implemented technology | Integration implications |
|---|---|---|
| Front end | React 18, TypeScript, Vite 5, Tailwind 4, `@assistant-ui/react`, Lucide | Browser integration is an SPA plus a separately built `booking-inline.js` embed. |
| API | Python 3.11, FastAPI, Pydantic, Uvicorn | JSON HTTP endpoints, one authenticated WebSocket (`/ws/agent`), no generated external SDK. |
| Persistence | SQLAlchemy with SQLite by default | SQLite WAL, FK enforcement and a 30-second busy timeout; can use `DATABASE_URL` for another SQLAlchemy-supported database. |
| AI | OpenAI Responses API; Realtime session relay | Uses structured tool calls, `store=False`, and server-held credentials. No API key is sent to the browser. |
| Calendar | Google Calendar API when service-account credentials are available; SQLite `calendar_events` fallback otherwise | A caller must not infer that a missing Google credential means live availability is empty; the service uses the local fallback. |
| SMS | MobileMessage Australia adapter; admin simulator bypasses provider sending | Inbound webhook requires a configured destination line. Manual/bulk sends use idempotency keys. |
| Hosting | Docker builds frontend then serves FastAPI; Fly configuration is included | The production process listens on `8080`; Fly mounts `/data` as durable storage. |

### State and persistence locations

On a mounted deployment volume, the application uses `/data`; otherwise it uses the backend directory. Important durable assets are:

- `assistant.db`: threads, messages, notes, events, calendar mirror/fallback, arrival sessions, notification dedupe rows, operations chat/runs/actions/tickets/memory.
- `data/`: service catalogue, add-ons, business variables, working hours, first-contact responder, quick replies, booking reminder, UI settings, line profiles, prompt-related settings.
- `knowledge/`: uploaded knowledge, learned-information records, curator state, and associated material.
- `prompts/`: system/user/SMS confirmation templates.
- `bootcamp.db`: simulated training and applied style profile data.
- `agent-runs/`: bounded operations-agent scratch workspaces only.

At start-up, the service creates additive database schema, migrates persisted state, reconstructs the process-local knowledge index, recovers interrupted operations runs, and starts arrival-alert, booking-reminder, curator, and operations-retention workers.

## 3. Personas and access model

| Persona | Surfaces | Permissions and boundaries |
|---|---|---|
| Customer | SMS, public booking form/embed, an arrival invite route | No admin session. Can submit a customer message, use the booking UI, and activate/use their scoped arrival session token. |
| Staff/operator | SMS Triage Dashboard, bookings, arrivals | Can take over/escalate/resolve threads, add notes, send replies, edit/approve/discard AI drafts, manage bookings and customer arrival chats. The current UI identifies the staff actor with a configured/static client-side agent identifier. |
| Owner/admin | All authenticated SPA workspaces, settings, operations chat and agent console | Can change business configuration, prompts/knowledge, review curator proposals, export messages, run operations investigations and explicitly approve protected actions. |
| External system | Webhook/API caller, FastAPI Bookings discovery provider, GitHub worker | Uses server-side contracts only. It never receives application AI keys, booking tokens, SMS credentials, or persistent cookies. |

If `APP_PASSWORD` is configured, non-public HTTP routes require either a valid signed HttpOnly admin-session cookie or HTTP Basic credentials. The login endpoint creates the cookie; the browser uses cookie-authenticated same-origin requests. If no password is configured, the admin gate is intentionally open for local/development use.

Public paths include the health check, SMS webhook, public/static booking content, arrival/public content, and static assets. The exact public-path allowlist is application configuration, so an integration should authenticate by default and expose only deliberately public routes.

The current CORS middleware permits all origins but does not permit credentialed CORS. A cross-origin administrative integration should therefore use an authenticated server-to-server path or be introduced with a reviewed CORS/authentication change, not assume browser-cookie access will work cross-origin.

## 4. Front-end experience

### 4.1 Routing and main workspaces

The SPA uses client-side path selection rather than a routing framework. On desktop it provides a sidebar; on small screens it provides a bottom navigation. Core paths are:

| Path | Workspace | Intended behaviour |
|---|---|---|
| `/` | SMS Triage Dashboard | Operational inbox, thread detail, AI-draft moderation and appointment context. |
| `/agent-console` | Operations Coding Agent | Bounded autonomous operations/coding run console, run history, streamed event terminal and owner chat. |
| `/chat` | Business Assistant | Persistent owner-facing operations chat, optional realtime voice controls, and support/curator context. |
| `/sim` | Customer SMS Simulator | Simulated customer handset for creating/test-driving inbound conversations without provider sends. |
| `/bootcamp` | Tori Boot Camp | Simulated persona training. Explicitly does not create SMS or bookings. |
| `/arrivals` | Customer Arrivals | Provider/admin alert queue and two-way arrival chats. |
| `/bookings` | Calendar Manager | Day schedule, availability, appointment editing/cancellation and arrival-link creation. |
| `/booking` | Public booking page | Standalone booking form. |
| `/arrival` | Customer arrival page | Standalone invite activation/check-in experience. |
| `/settings` | Settings | Business, responder, knowledge, message, transport and catalogue controls. |

The root can also serve a public landing page when built production assets are present. The SPA requests current inbox, booking and arrival snapshots on a cadence, manages notification sound/alerts, and writes browser history when switching workspaces.

### 4.2 SMS Triage Dashboard

This is the primary message UI. It consists of a searchable/filterable inbox list, thread workspace, customer/appointment context and internal-notes panel.

Inbox data includes customer phone, last-message snippet/role/time, priority, state, unread count, due/SLA status, pinned/blocked state, booking/arrival indicators, and message-review flags. Filters include at least all, needs review, auto reply, taken over, escalated and resolved; the user can search inbox-wide.

The selected conversation renders a chronological mixed timeline:

- customer inbound messages;
- agent manual replies;
- AI auto-replies;
- unsent AI drafts, visually identified as requiring approval;
- operational thread events such as takeover, escalation, resolution, booking/arrival and draft lifecycle events;
- internal staff notes, separated from customer-visible messages.

Conversation actions include taking over, escalating with a reason, resolving with a resolution note, toggling thread autoresponder, pinning, blocking/unblocking, composing a manual reply, adding notes, and acknowledging arrival-related items. A manual reply is an outbound action and is recorded with an event and idempotency support; the UI only exposes it when the thread is under appropriate human control.

The dashboard also provides per-thread appointment/availability context and a cancellation confirmation flow. It is not merely a visual chat transcript: its state transitions drive the responder and human handoff policy.

### 4.3 Message-level auditing and moderation controls

The message UI is designed around reviewability. These controls are material integration requirements:

| Control | UI effect | Server-side effect / audit trail |
|---|---|---|
| AI draft status | Clearly labels a `draft` as unsent and awaiting approval | Drafts are stored as messages with role `draft`; they are not transmitted. |
| Edit draft | Prompts/edits the proposed reply before send | `PATCH /api/messages/{id}/draft` changes only a draft. A later approval is still required. |
| Approve & Send | Sends the approved draft | Converts the review action into an outbound send, records the delivery/message lifecycle and may trigger controlled learning curation. |
| Discard | Removes an unsent draft | Deletes the draft and writes a `draft-discarded` thread event. If no drafts remain, the thread can return to automatic reply state. |
| Correction | Allows staff to record what was wrong and optionally propose replacement wording | Creates a learning record and a new review-only draft; logs `ai-response-correction-queued`; it never silently changes live knowledge. |
| Previous/next review navigation | Traverses flagged/reviewable messages in a thread | Client computes reviewed/flagged navigation; it does not alter history. |
| Clear all flags | Removes stale review markers from the selected thread | `DELETE /api/threads/{id}/review-flags` clears review-only status while preserving unsent drafts. |
| Clear all pending drafts | Settings-wide bulk moderation action | Deletes all draft messages, normalises affected states, and creates `drafts-cleared` events. |
| Clear review-only threads | Settings-wide cleanup | Clears only threads in `needs-review` with no draft; it does not send or delete drafts. |
| Notes | Private operator annotations adjacent to a thread | Stored in `notes` with staff ID and timestamp; not part of customer messages. |
| Timeline events | Shows operational history inline | `thread_events` preserves event type, actor, timestamp and bounded metadata. |
| CSV export | Exports message history from Settings | `GET /api/settings/messages/export.csv`; intended for review/reporting. |

The distinction between `draft`, `agent`, `customer`, and automatic/system reply is central. An integration must preserve it; collapsing all roles into one message type would remove the key audit/approval guardrail.

### 4.4 Settings workspace

Settings is a collection of administrative controls rather than a generic key-value editor. Implemented panels include:

- system and user prompt templates, global autoreply and training-mode switches, UI avatar visibility, and catch-up lookback;
- business variables with validation against reserved template names;
- service catalogue management: ordered services, descriptions, prices, durations, display duration, active/hidden status and service-to-add-on mapping;
- add-on catalogue with price, extra time and compatible services;
- weekly working-hours editor;
- booking confirmation template and booking-reminder configuration;
- MobileMessage credentials/status and inbound webhook guidance;
- manual Q&A rules and first-contact delayed responder settings;
- quick replies and SMS line profile/autoreply configuration;
- blocked contacts, bulk SMS with recipient validation/deduplication and a client request ID, plus message CSV export;
- push notification subscription/settings;
- knowledge upload/list/edit/delete, search-and-purge moderation and credentials upload;
- learned-information review/approval/redraft tools and Knowledge Curator audit/proposal resolution;
- RAG status and training-data moderation/pruning.

Changes to service data, prompts and many settings are file-backed. Updates need to be treated as durable configuration mutations and should be synchronised with any integrating system’s authoritative catalogue rather than overwritten opportunistically.

### 4.5 Other user interfaces

- **Public booking:** presents active services, pricing and configured duration; loads current availability and supports a native, non-iframe embedding build. The separate `booking-inline.js` entry is intended to render booking cards directly on a website.
- **Calendar Manager:** day-oriented timeline respecting configured working hours; lists/edits/deletes/creates bookings and can create an arrival invite.
- **Arrival client/provider:** an invitation becomes a customer-scoped session with idempotent activation, check-in status, a short-lived client token and two-way messages. Provider view lists waiting/active arrivals, acknowledges them, sends staff replies and closes sessions. The shell raises periodic visual/audio alerts for due customer arrivals.
- **SMS Simulator:** sends simulated inbound messages through the same backend responder pipeline but reports zero provider sends. It is suitable for safe functional testing.
- **Boot Camp:** runs simulated conversations against personas, lets an operator apply/undo a style profile and pause/resume/stop/reset a run. It is explicitly non-production: no SMS or bookings are made.
- **Business Assistant:** is a persistent owner-only operations conversation with current system snapshot, constrained tools and optional realtime voice session. It is distinct from the autonomous coding agent.

## 5. Customer messaging lifecycle

### 5.1 Inbound SMS processing

1. The SMS provider calls `POST /webhooks/sms` with `from`, `to`, `body`, optional provider message ID and received time. Compatible aliases such as `sender`, `message`, `message_id` and `received_at` are normalised.
2. The destination number must match an enabled/configured SMS account. Unknown/missing destinations fail with HTTP 422 rather than defaulting to a line.
3. The service atomically claims provider message IDs through `inbound_webhook_receipts` to prevent duplicate webhook processing.
4. It obtains or creates the `(sms_account_key, customer_phone)` thread, records the customer message, increments relevant state, and emits thread events.
5. It blocks auto-reply if the contact is blocked, the global/per-thread switch is off, human takeover/escalation is active, or safety/availability conditions demand review.
6. If automatic reply is enabled, a per-thread lock serialises active responder work. Newer customer turns supersede stale in-progress work so the system does not send a response to an obsolete message.
7. The responder builds a timestamp-aware conversation window, renders business variables, retrieves scoped knowledge/style examples, obtains live booking evidence when the user asks about availability/booking, and invokes the model with restricted booking tools.
8. The generated text passes safety gates. Unsafe output, unverified availability claims, internal-instruction leakage, unsafe holding replies, duplicate AI reply content or model failures fail closed into `needs-review`; it does not fabricate an answer.
9. In training mode, a valid AI response is persisted as role `draft` and awaits human approval. Otherwise it is sent through the SMS provider and stored as an agent/system reply. Both paths write thread events.

### 5.2 Reply and transport controls

- Message bodies are limited to 1,600 characters at key manual/bulk API boundaries.
- The system avoids re-sending a previously sent URL unless the customer explicitly asks for it, strips trailing URL punctuation, and avoids volunteering payment/deposit terms unless asked.
- Existing drafts are included to the model only as explicitly unapproved material; they are not treated as factual authority.
- Manual replies have a five-minute deduplication window/client request identifier logic.
- Bulk SMS accepts up to 100 validated Australian mobile recipients, normalises/deduplicates recipients, serialises each provider dispatch, uses a deterministic per-recipient message UUID derived from the client request ID, and stores successful deliveries in normal thread history. A partial failure returns individual recipient results.
- First-contact autoresponder configuration supports enablement, cooldown days, delay seconds and text. It is separately persisted and guarded from duplicate contact/reply behaviour.

### 5.3 Thread state model

The practical thread states are `auto-reply`, `needs-review`, `taken-over`, `escalated`, and `resolved`; priority is `low`, `medium`, or `high`. A thread also tracks assigned staff, SLA due time, unread count, autoresponder enablement, pin state, pending booking proposal, and message/notes/events relations.

`taken-over` and `escalated` are important control states: they prevent the responder from continuing autonomous customer handling until the human workflow returns the thread to a suitable state. An integration should use the thread endpoints rather than writing a state directly.

## 6. Booking and calendar behaviour

### 6.1 Calendar authority and availability

The calendar service uses Google Calendar when service-account credentials work. It falls back to local SQLite calendar events where Google is unavailable or unconfigured. The calendar service supports busy-slot lookup, booking creation/listing/update/deletion, provider context and local event mirroring.

The service catalogue and working hours are local authoritative configuration for the legacy calendar path. Services carry a stable string ID, name, description, price, duration, display-duration flag, visibility/active state and optional add-on IDs.

Availability is treated as dynamic. Durable knowledge cannot be used to claim a specific current slot. Customer-facing availability must result from a fresh calendar/booking lookup; failures should be surfaced as review or a safe unable-to-confirm response, not as empty-calendar availability.

### 6.2 Conversational booking state machine

The customer booking flow is intentionally two-stage:

```text
service/time discovery
  -> customer picks an offered time (not a write)
  -> assistant gathers name, exact service, exact time and optional notes
  -> propose_booking validates the exact slot and stores a pending proposal
  -> assistant sends full booking summary
  -> later explicit confirmation only
  -> recheck availability
  -> create booking once, persist result, clear pending proposal
```

Key invariants:

- Numeric replies such as `1`, `2` or `3` select a presented option only; they never directly make a booking.
- A proposal has a short expiry (two hours in the current implementation).
- An ambiguous answer, refusal, detail change (for example, “yes, but 4 pm”), or expired proposal cannot authorise the old write.
- The system rechecks the exact availability before create.
- Duplicate confirmation/retry is treated idempotently where the legacy booking flow can establish that the booking already exists.
- A just-taken slot produces a conflict and fresh options rather than a false confirmation.
- The system may only state booking success on a confirmed/already-confirmed tool result.

### 6.3 FastAPI Bookings integration boundary

There is an explicit adapter boundary in `backend/booking_tools.py`. The model-facing discovery tool names and payload concepts are intended to remain stable:

| Tool | Current purpose | Write? |
|---|---|---:|
| `get_current_time` | business-local current date/time/day/timezone | No |
| `list_booking_services` | active service IDs, names, descriptions, prices and duration | No |
| `get_times_today` | remaining same-day slots for a service | No |
| `get_times_tomorrow` | next-day slots for a service | No |
| `get_next_available` | earliest slot after now or a supplied time | No |
| `propose_booking` | server-side validation/staging | No external write |
| `confirm_booking` | final create after confirmation/recheck | Yes |

When `BOOKING_BACKEND=fastapi`, the application can call the sibling FastAPI Bookings service for **service and availability discovery** using server-only `FASTAPI_BOOKINGS_URL`, `FASTAPI_BOOKINGS_TENANT` and optional token. Its expected upstream endpoints are `GET /api/public/bootstrap` and `POST /api/public/search-availability`.

**Critical current limitation:** final conversational creation is still the legacy Google Calendar/SQLite path. It does not yet identify/create the FastAPI Bookings client, preserve provider/location IDs in a staged proposal, recheck FastAPI availability, call its booking-create API, or obtain a first-class upstream idempotency result. An integrating application must not claim the FastAPI Bookings write path is live until those pieces are implemented together.

The required cutover contract is a server-side command provider accepting only a stored proposal, resolving the customer client by canonical phone, preserving exact service/provider/location/start/end identity, requiring an upstream idempotency key/external reference, handling HTTP 409 as a conflict, storing returned booking ID/status, and using customer language appropriate to `pending` versus confirmed status.

### 6.4 Appointment reminders and arrival links

Booking reminders run in the background from persisted enablement, lead-time and template configuration, retaining sent-reminder markers to avoid repeats. Notification deliveries use a dedupe ledger that stores stable event identifiers rather than message body content.

Staff can create an arrival invite for a booking. The system stores hashes of invite/client tokens; plaintext tokens are not retained. Arrival status is idempotent, session-expiring, alert-counted and auditable. Messages are separate `arrival_chat_messages`; staff acknowledgment/close status and alert schedule are stored on the session.

## 7. Knowledge, retrieval and autonomous curator

### 7.1 Knowledge model and retrieval

Knowledge is dual-purpose: durable business response guidance and style examples. Durable records contain a canonical key, scoped content/instruction/example reply, authority level, source/evidence, confidence, effective dates, review/retrieval status and supersession relation. Aliases associate customer utterances with a record. The repository supports active-record query, save, supersede, aliases and JSONL migration/synchronisation.

Retrieval is hybrid lexical plus embedding similarity. If an embedding API cannot be used, it has deterministic pseudo-embedding behaviour to preserve testable ranking/fallback. The retriever checks active status, account scope/shared applicability, effective dates and authority conflict before returning an authoritative fact. Style examples are a separate validated SMS example index, used for rhythm only and explicitly forbidden as a factual source.

Prompt templates can use only recognised variables. Rendering rejects unresolved critical placeholders. Business variables are stored separately and names are validated; reserved application variables cannot be redefined.

### 7.2 Knowledge safety and scope rules

The curator classifies knowledge into `shared`, `primary`, or `internal`, and categories including generic, service-specific, availability/booking state, customer-specific and internal/uncertain. The following categories are never retrievable: availability/booking state, customer-specific, internal/uncertain, unclassified and quarantined material.

Before a learned candidate can be persisted for future replies, reusable text is sanitised. The pipeline removes copied customer/owner source fields, rejects unsafe literal data and either tokenises safe scoped details (provider name, service name, price, duration, requested date/time, information URL) or declines the candidate. It rejects unsafe placeholders, cross-provider details, names, phone/email-like data, current availability claims, literal price/time/date/payment/address facts, links and uncertain scope. Shared knowledge must be generic and provider-neutral.

This means a staff edit can be useful audit evidence without becoming a live rule. A correction that changes a time, price, URL or similar dynamic data is classified as dynamic-data failure and is not a business-knowledge candidate.

### 7.3 Curator workflow

The curator runs on a manual Settings action and a background worker. It inspects records for integrity, metadata/schema errors, expiry, duplicate/redundant knowledge, scope/authority conflicts, references, gaps and possible supersession. It saves bounded run/proposal state for operator review.

Each proposal exposes a type/reason, record references/previews, current/stale reference status, safe suggested resolution and lifecycle status. Staff can accept, reject, dismiss, or resolve with an explicit action such as retaining/reviewing/superseding selected records. Proposal resolution is validated against current records so a stale proposal does not modify changed knowledge.

Maintenance also has bounded backup/metadata-repair routines and records autonomous maintenance audit entries. It does not give the model an unrestricted write path to source knowledge.

### 7.4 Graduated autonomy policy

The autonomous curator policy is explicit and should be preserved by an integration:

| Level | Permitted autonomous action | Mandatory review conditions |
|---|---|---|
| 0 — proposal only (default) | None; every action becomes a proposal | All changes |
| 1 — housekeeping | Exact duplicate suppression, alias attachment, question clustering, invalid-record quarantine | Any business-fact modification |
| 2 — trusted answers | Level 1 actions plus safe high-confidence trusted owner/admin answers | Non-owner/admin actor, confidence below 0.80, dynamic fact, unclear scope, active conflict/replacement, cross-scope change |
| 3 — approval required | Represents the escalation boundary for conflicts/replacements/dynamic/cross-scope mutations | All such mutations require human decision |

For trusted answer auto-ingestion, the actor must be `owner` or `admin`, scope must be recognised, confidence must be at least 0.80, answer must be non-empty and durable/non-dynamic, and no active canonical-key conflict may exist. Safe answers become active `owner_instruction` knowledge with a source-evidence record and question alias. Conflicts do not overwrite an active rule.

### 7.5 Learning feedback loop and observability

When staff edit/reject/correct an AI response, the system classifies the difference: style-only, operator preference, missing/stale/incorrect knowledge, wrong scope, retrieval failure, reasoning failure or dynamic-data failure. Only appropriate factual policy changes may enter pending learning curation; dynamic changes and style edits are excluded.

For every responder execution, the observability design supports a privacy-safe decision trace: hashed/sanitised customer query, detected intent/confidence, dynamic systems queried, retrieved/rejected candidates, authority winner/reason, conflict status, style examples and final response. Phone/email/card-like data are redacted before trace storage. Outcome signals are bounded and correlate trace/thread/tenant/account to events such as unedited send, edit, rejection, repeated question, takeover, booking success/failure and knowledge-gap lifecycle.

## 8. Operations AI and autonomous coding agent

### 8.1 Business Assistant

The Business Assistant is an authenticated owner conversation stored in `operations_chat_messages`. It sends a current operational snapshot and durable non-secret memory to the model, supports a bounded number of tool-call rounds per request, and stores the owner/assistant turns. It reports capabilities including live snapshot, log/diagnostic tools, message self-diagnosis, web research, persistent memory and controlled actions. It explicitly reports that controlled actions require confirmation.

Optional realtime voice starts through an SDP exchange endpoint. The browser sends the SDP to the backend, which creates the realtime session server-side; individual transcript turns are persisted through a separate endpoint. Realtime tools remain a reviewed backend dispatch, not arbitrary browser execution.

### 8.2 Operations Coding Agent protocol

The `/agent-console` UI opens a same-origin WebSocket at `/ws/agent`. The browser first receives a `ready` frame with protocol version, enablement and limits, then sends one of:

- `start`: UUID-like idempotent request ID plus a natural-language objective;
- `attach`/`resume`: a persisted run ID plus an event cursor;
- cancellation is an explicit run request/action, not an implication of browser disconnect.

Runs are stored in `operations_agent_runs`; ordered events are stored in `operations_agent_events` with a unique `(run_id, sequence)` relation. The UI persists the selected run ID in browser session storage and reconnects/replays it, so a reload does not resubmit the same work. Run status includes starting, running, completed, cancelled, failed, step-limit or interrupted; it retains step count, maximum, cancellation state, final summary, error and timestamps.

The WebSocket fails closed unless an admin cookie is valid and the same-origin check passes. In production it is expected to be `wss` on the public host. Request IDs make retry/start idempotent. Only one active run is permitted per application process.

### 8.3 Autonomous execution boundaries

The model must emit one structured action at a time: short user-visible progress summary, an enum action and bounded JSON arguments. It is not asked for private chain-of-thought. Actions are virtual operations tools, not a shell exposed from the web process.

Important constraints implemented by the agent-console service include:

- at most 200 steps by default (configurable downward) and bounded total execution duration;
- 30-second timeout per allowlisted action and single-worker/bounded database/source-reader work;
- credential redaction, terminal-control-sequence removal, ASCII/control sanitisation and size caps before event/display storage;
- repository reads through a constrained GitHub reader rather than local shell access;
- virtual commands explicitly allow only reviewed read-only evidence and audited workflows;
- scratch writes are restricted to a run-specific workspace, relative safe paths, recognised non-binary extensions, 64 KiB per file, 256 KiB per workspace and a 16 MiB global cap; traversal, absolute paths, links, configuration, production database and `/app` writes are refused;
- coding-task submissions reject customer numbers, email, credentials/secret-shaped values and customer transcripts; a task must be anonymised engineering evidence;
- one active coding task globally, no duplicate submission by a single run;
- operational memory stores only non-secret reusable operating lessons, never raw customer transcripts or credentials;
- retention prunes completed history at 30 days/50 runs.

The console may queue a separate GitHub review-branch coding task where configured. It does not give the Fly web process direct source-write, deployment or production configuration access. Source/CI/deployment state must be audited independently. The configured repository and deployment settings should be treated as environment-specific capabilities, not as permission for a third party to deploy.

Some source paths include an owner approval workflow for a pending deployment action; integrations must preserve the final explicit authenticated owner action and must not promote code merely because an agent run completed or a branch was pushed.

## 9. HTTP and WebSocket contract catalogue

All JSON endpoints return standard FastAPI validation errors for malformed request payloads. API/booking/portal responses are sent with no-cache headers. Administrative endpoints are authenticated unless explicitly public.

### Authentication and public ingress

| Method/path | Contract |
|---|---|
| `GET /api/health` | Public readiness payload: `{status: "ok", service: "assistant-ui"}`. |
| `GET /api/auth/status` | Returns whether an admin session is authenticated. |
| `POST /api/auth/login` | `{username,password}`; validates admin credentials and sets signed HttpOnly session cookie. |
| `POST /api/auth/logout` | Deletes admin session cookie. |
| `POST /webhooks/sms` | Inbound provider webhook; validates destination/account, deduplicates and starts responder processing. |
| `POST /api/admin/sms-simulator` | Authenticated simulation of inbound SMS, returns normal pipeline result and `provider_sends: 0`. |

### Thread and message operations

| Method/path | Contract |
|---|---|
| `GET /api/threads` | Lists inbox threads; supports filters such as state/search/pagination/catch-up context. |
| `POST /api/threads/catch-up` | Finds missed/recent inbound work using configured lookback. |
| `GET /api/threads/{thread_id}` | Full thread: messages, notes, events, arrival/booking/review context. |
| `POST /api/threads/{thread_id}/autoresponder` | Set per-thread AI auto-reply enablement. |
| `POST /api/threads/{thread_id}/pin` | Pin/unpin. |
| `POST /api/threads/{thread_id}/block` | Block/unblock contact for the SMS account. |
| `POST /api/threads/{thread_id}/takeover` | Human takeover with agent ID. |
| `POST /api/threads/{thread_id}/reply` | Manual customer reply: agent ID, text, optional client request ID. |
| `POST /api/threads/{thread_id}/information-request/respond` | Respond to a curator/responder information request. |
| `DELETE /api/threads/{thread_id}/information-request/{request_event_id}` | Dismiss a request event. |
| `POST /api/threads/{thread_id}/notes` | Add internal note. |
| `POST /api/threads/{thread_id}/escalate` | Human escalation with reason. |
| `POST /api/threads/{thread_id}/resolve` | Resolution with audit note. |
| `DELETE /api/threads/{thread_id}/review-flags` | Clears review flags/status without deleting unsent drafts. |
| `POST /api/messages/{message_id}/approve` | Approves/sends a draft. |
| `PATCH /api/messages/{message_id}/draft` | Edits an unsent draft only. |
| `POST /api/messages/{message_id}/corrections` | Records response correction and queues review/learning work. |
| `POST /api/messages/{message_id}/discard` | Discards an unsent draft. |
| `DELETE /api/messages/drafts/pending` | Bulk deletes all drafts; auditable. |
| `DELETE /api/messages/review/pending` | Clears review-only thread markers; drafts survive. |

### Bookings, arrivals and notifications

| Method/path | Contract |
|---|---|
| `GET /api/calendar/bookings` | Lists bookings; optional past inclusion. |
| `POST /api/calendar/bookings` | Creates a staff/manual booking and confirmation path. |
| `PUT /api/calendar/bookings/{booking_id}` | Updates booking. |
| `DELETE /api/calendar/bookings/{booking_id}` | Cancels/deletes booking. |
| `GET /api/calendar/freebusy` | Dynamic free/busy slots; optional duration. |
| `GET/POST /api/settings/booking-reminder` | Booking reminder configuration. |
| `POST /api/arrival/admin/bookings/{booking_id}/invite` | Creates/reuses arrival invite. |
| `POST /api/arrival/activate` | Customer activates invite token and receives scoped client token/session. |
| `POST /api/arrival/status` | Customer check-in/status update. |
| `GET /api/arrival/client/{session_id}` | Scoped customer session read. |
| `POST /api/arrival/client/{session_id}/messages` | Customer arrival-chat message. |
| `GET /api/arrival/admin/sessions` | Admin arrival session list. |
| `GET /api/arrival/admin/sessions/{session_id}` | Admin detail. |
| `POST /api/arrival/admin/sessions/{session_id}/messages` | Admin arrival-chat message. |
| `POST /api/arrival/admin/sessions/{session_id}/close` | Close session. |
| `POST /api/threads/{thread_id}/arrivals/{session_id}/acknowledge` | Mark an arrival alert acknowledged against a thread. |
| `GET /api/push/config` | Push capability/public config. |
| `POST/DELETE /api/push/subscriptions` | Store/remove browser push subscription. |

### Knowledge, learning and business configuration

| Method/path | Contract |
|---|---|
| `GET/POST /api/settings` | Read/update system prompts, switches and UI settings; key is obfuscated on read. |
| `GET/POST /api/settings/business-variables` | Read/update validated template variables. |
| `GET/POST /api/settings/line-profiles` | SMS line profile configuration. |
| `POST /api/settings/line-profiles/{account_key}/auto-reply` | Per-line responder switch. |
| `GET/PUT /api/settings/quick-replies/{account_key}[/{slot_index}]` | Read/update quick replies. |
| `GET/POST /api/settings/first-contact-autoresponder` | First-contact responder controls. |
| `GET /api/settings/messages/export.csv` | Message CSV export. |
| `GET/POST /api/settings/working-hours` | Weekly hours. |
| `GET/POST /api/settings/mobilemessage` | SMS gateway config; password is not returned. |
| `GET/POST /api/qa-rules` | Deterministic Q&A rule list. |
| `GET/POST /api/services` | Shared catalogue; `active_only=true` filters unavailable services. |
| `GET/POST /api/service-addons` | Service add-on catalogue. |
| `GET/POST /api/settings/sms-confirmation` | SMS confirmation template. |
| `GET/POST /api/settings/learnings` and entry actions | Create/list/edit/delete/manual learning; approve, bulk approve, redraft, classify and move to review. |
| `POST /api/settings/learnings/sms-pair-preview` | Safe preview of candidate learning from SMS pairs. |
| `POST /api/settings/learnings/sms-pair-import` | Imports selected candidate pairs into review flow. |
| `GET /api/settings/knowledge-curator` | Curator run/proposal state. |
| `POST /api/settings/knowledge-curator/run` | Runs integrity/curation audit. |
| `POST /api/settings/knowledge-curator/proposals/{id}/accept` | Accept proposal. |
| `POST /api/settings/knowledge-curator/proposals/{id}/resolve` | Resolve with explicit action/record IDs. |
| `POST /api/settings/knowledge-curator/proposals/{id}/{transition}` | Reject/dismiss permitted proposal transitions. |
| `GET/POST /api/settings/upload-knowledge` | Lists/uploads knowledge documents. |
| `POST /api/settings/upload-credentials` | Protected credentials-file upload workflow. |
| `GET/POST/DELETE /api/settings/knowledge-files/{filename}` | Read/save/delete an individual knowledge file. |
| `POST /api/settings/knowledge-files/{filename}/search` | Search candidate entries. |
| `POST /api/settings/knowledge-files/{filename}/purge` | Purge by query or selected indexes. |
| `GET /api/rag/status` and `/api/admin/rag/status` | RAG status/diagnostic metadata. |
| `GET /api/settings/blocked-contacts`; `DELETE /api/settings/blocked-contacts` | Blocked-contact list and removal. |
| `POST /api/settings/bulk-sms` | Owner-confirmed bulk send. |

### Boot Camp and operations

| Method/path | Contract |
|---|---|
| `GET /api/bootcamp/personas` | Available simulated personas. |
| `GET /api/bootcamp/profile` | Current applied style profile. |
| `POST /api/bootcamp/profile/apply`, `/undo` | Apply/revert style profile. |
| `POST /api/bootcamp/runs` | Start simulated run. |
| `GET /api/bootcamp/runs/latest`, `/api/bootcamp/runs/{id}` | Run status/transcript. |
| `POST /api/bootcamp/runs/{id}/control` | Pause/resume/stop. |
| `DELETE /api/bootcamp/runs` | Reset runs. |
| `POST /api/bootcamp/conversations/{id}/information-request/respond` | Respond to simulation information need. |
| `GET /api/settings/operations-chat/messages` | Persisted Business Assistant history (bounded). |
| `POST /api/settings/operations-chat/messages` | Owner Business Assistant turn and constrained tool loop. |
| `POST /api/settings/operations-chat/realtime` | SDP realtime session initiation. |
| `POST /api/settings/operations-chat/realtime/turns` | Persist voice transcript turn. |
| `POST /api/settings/operations-chat/realtime/tool` | Execute approved realtime diagnostic tool. |
| `GET /api/settings/support-tickets` | Sanitised support-ticket status. |
| `POST /api/settings/support-tickets/{id}/deploy` | Final owner deployment approval for an eligible ticket. |
| `GET /api/settings/agent-console/runs` | Agent-console enablement and bounded run history. |
| `GET /api/settings/agent-console/runs/{id}/events?after=N` | Ordered redacted run-event replay. |
| `WS /ws/agent` | Authenticated start/attach/resume and live run event stream. |
| `POST /api/internal/operations/worker-claim` | GitHub-worker-only audited task claim; bearer identity required. |

There is also a legacy `POST /api/locanto/sync` adapter for external classified buyer messages. It records a thread, applies Q&A/AI response policy, and returns an outbound reply only where an automatic reply is safe and configured.

## 10. Core persisted entities

| Entity | Key fields and relationships | Why it matters to an integration |
|---|---|---|
| `Thread` | account+customer unique key; state, priority, assigned operator, SLA, unread count, responder controls, pinned, pending booking; messages/notes/events | Canonical SMS conversation aggregate. |
| `Message` | thread ID, role, text, provider message ID, timestamp | Preserves customer vs agent vs `draft` review distinction. |
| `InboundWebhookReceipt` | provider message ID claim, source/received timestamps | Idempotent inbound processing. |
| `Note` | thread, staff actor, text/time | Internal-only triage annotation. |
| `ThreadEvent` | thread, type, actor, metadata/time | Immutable-style operational audit stream; metadata should be treated as event-specific. |
| `CalendarEvent` | summary, customer/account/thread references, start/end, status, notes/amount | Local calendar fallback/mirror and appointment relation. |
| `ArrivalSession` / `ArrivalChatMessage` | booking/thread links, hashed tokens, state/expiry/alerts/acknowledgment, messages | Customer-arrival token boundary and audit state. |
| `BlockedContact` | account/customer unique key | Customer suppression control. |
| `PushSubscription` / `NotificationDelivery` | browser transport, delivery dedupe key | Operational alerts without storing message contents in delivery ledger. |
| `OperationsChatMessage`, `OperationsMemory`, `OperationsAction`, `SupportTicket` | owner conversation, non-secret memory, audited action proposal, customer-safe ticket status | Operations conversational/control plane. |
| `OperationsAgentRun` / `OperationsAgentEvent` | idempotent run ID/request ID, status/limits, ordered redacted event stream | Autonomous coding agent audit/replay model. |
| Knowledge records/evidence/aliases | canonical authority, applicability, source/evidence, aliases, supersession | Durable RAG governance model. |

## 11. Security, privacy and audit requirements for an integration

1. **Never expose secrets to the browser or model.** OpenAI, Google, SMS-provider, FastAPI Booking and GitHub credentials are server-side. Settings reads mask secret values.
2. **Treat customer transcript data as sensitive.** The operations coding-task bridge specifically rejects phones, emails, secret-shaped strings and customer transcript content. Preserve that filter at every new integration boundary.
3. **Preserve source accountability.** The original message remains in the customer conversation. Reusable learned knowledge keeps a safe template/source reference, not copied names, customer question or original response payload.
4. **Use idempotency.** Preserve inbound provider message IDs; send manual/bulk messages with request IDs; treat booking retries as a specific contract, not a generic retry.
5. **Fail closed for dynamic facts.** Do not transform a stored service description or training example into a claim about today’s availability. Query the live calendar/booking source immediately before such a claim and again before a booking write.
6. **Keep human-review state intact.** Drafts must never be reclassified as sent merely because another system synchronises messages. Takeover/escalation must continue to block autonomous replies.
7. **Audit mutations.** Integrations that approve, edit, discard, send, resolve, alter knowledge or release software should call the relevant action endpoint or produce an equivalent durable event with actor, timestamp, intent and result.
8. **Use the authenticated operations protocol.** Do not connect arbitrary browsers/services to `/ws/agent`; it requires signed session and strict same-origin validation. Do not turn virtual operations tools into shell access.
9. **Apply retention/minimisation.** Agent event history, scratch data and outcome signal buffers are deliberately bounded. New analytics should preserve redaction, retention and data-minimisation characteristics.
10. **Do not rely on implicit defaults.** Unknown inbound SMS destination, unclear knowledge scope, stale curator proposal, ambiguous booking confirmation and unsafe AI output all intentionally require a controlled failure/review path.

## 12. Integration recommendations and non-goals

### Recommended integration pattern

For a sibling booking, CRM, analytics or communications system, prefer a server-to-server adapter behind Assistant UI’s existing service boundary. Use stable identifiers (thread ID, message ID, calendar event ID, upstream booking ID and account key), signed/credentialed requests, and a versioned event mapping. External callbacks should be idempotent and should not bypass the UI’s thread state, draft review or booking confirmation workflow.

For a UI integration, consume the documented JSON shapes through a small typed client and preserve the four message roles and event timeline. A direct database integration is discouraged because it bypasses request validation, provider dispatch, locks, audit event creation, line scoping and additive migrations.

### Material boundaries / non-goals

- FastAPI Bookings **discovery** is integrated conditionally; its final write path is not yet complete.
- The application currently has a single documented SMS account key (`primary`) in many schema/UI controls, although data structures remain account-scoped. Do not assume multi-line support is fully productised.
- CORS is permissive but credentialed cross-origin admin UI use is not enabled.
- No external OpenAPI stability/version policy is declared. The endpoint inventory is a present implementation contract; a production integration should add versioning, auth scopes and contract tests before coupling tightly.
- The Operations Coding Agent is a constrained owner tool, not a general external workflow engine or autonomous production administrator.
- Public booking/arrival views have intentionally narrower access than the staff portal; they should not be used to expose administrative configuration or operational audit data.

### Verified FastAPI Bookings UI compatibility assessment

**Conclusion: Assistant UI is not a drop-in-compatible frontend for FastAPI Bookings, and the two applications should not be connected as co-equal runtime UIs.** This conclusion is based on the FastAPI Bookings workspace's own `AGENTS.md`, which identifies FastAPI Bookings as the active application and explicitly declares the old Assistant UI obsolete and prohibited as a runtime dependency.

There is conceptual and partial server-side discovery compatibility, but not UI-contract compatibility:

| Concern | Assistant UI | FastAPI Bookings | Compatibility result |
|---|---|---|---|
| Public booking UI | Its own standalone `/booking` page and native `booking-inline.js` embed, backed by Assistant UI service catalogue/free-busy endpoints | Tenant-configured `/book/:slug` booking form/widget with configurable visible modules, pre-selections and a public-token/form contract | Not interchangeable; FastAPI Bookings' form should remain the public booking UI. |
| Booking flow | Conversational two-step proposal then explicit confirmation; legacy calendar write | Form-specific resolve/availability/create flow; selected server-resolved slot; public booking becomes `pending` for provider/admin approval | Similar safety intent, different state machine and ownership. |
| Data model | One primary SMS-account model; service IDs are strings; no preserved FastAPI provider/location identity in staged legacy proposal | Tenant-scoped numeric entities, provider/location/category/resource/add-on relationships and configurable module order | Requires a mapping/adapter, not shared components. |
| API usage | Optional discovery adapter uses older public bootstrap/search endpoints | New widgets are directed to form-specific `/api/public/booking-forms/{slug}` endpoints; legacy endpoints are deprecated for new widgets | Assistant UI's public booking UI does not implement the current authoritative widget contract. |
| Admin UI | SMS triage, curator/audit, local calendar and operations controls | Tenant-scoped booking/admin workspace | Separate products; do not embed one admin SPA in the other. |

The practical target architecture is therefore **migration/replacement**, not UI federation: retain only independently approved functions that FastAPI Bookings does not already own, port them behind FastAPI Bookings' tenant/auth/data contracts, and retire Assistant UI as a runtime dependency. If the legacy SMS triage/audit workflow is needed, it should be reimplemented or deliberately migrated into FastAPI Bookings rather than embedded or linked as a second source of truth.

## 13. Verification assets

The repository includes a substantial test suite across backend and frontend. Test modules cover conversation safety, timestamps, inbound reliability/idempotency, manual reply idempotency, shared visibility, booking and timezone regressions, RAG retrieval/ambiguity/integrity, knowledge curation/autonomy, curator gaps, message CSV export/review navigation, arrival/booking alerts, push notifications, operations/agent console, realtime voice and the FastAPI Bookings discovery boundary.

Before an integration is accepted, validate at least these end-to-end scenarios:

1. Duplicate inbound webhook creates one customer message/reply sequence.
2. Draft edit → approval sends exactly once; discard never sends.
3. Takeover/escalation prevents an AI reply; resolution/audit event is visible.
4. Availability query uses current live source; a conflict between proposal and confirmation does not create a booking.
5. Dynamic personal/availability/price text from a correction cannot become retrievable knowledge.
6. Curator conflict/replacement remains a human proposal even at trusted-answer autonomy level.
7. Arrival invite cannot be accessed without its scoped token; staff acknowledgment/close is recorded.
8. Operations agent reconnect replay does not create a second run; agent event output is redacted.
9. Any external booking/CRM synchronisation is idempotent and does not bypass the review/booking lifecycle.

## 14. Primary implementation references

- Application composition/auth/lifecycle: `backend/main.py`, `backend/core/config.py`, `backend/core/database.py`.
- Domain persistence: `backend/models/domain.py`.
- Customer pipeline: `backend/routes/sms.py`, `backend/services/sms_service.py`, `backend/routes/phone.py`, `backend/routes/misc.py`.
- Booking boundary: `backend/services/booking_service.py`, `backend/booking_tools.py`, `backend/routes/booking.py`, `FASTAPI_BOOKINGS_HANDOFF.md`.
- Knowledge and curator: `backend/knowledge/`, `backend/curator/`, `backend/services/learning_service.py`, `backend/observability/`.
- Operations autonomy: `backend/routes/operations.py`, `backend/services/operations_service.py`, `backend/agent_console.py`.
- Front end: `frontend/src/App.tsx`, `SmsTriageDashboard.tsx`, `SettingsView.tsx`, `BookingsView.tsx`, `AgentConsole.tsx`, `OperationsAIChat.tsx`, `ArrivalProviderView.tsx` and `api.ts`.
