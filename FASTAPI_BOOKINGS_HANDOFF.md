# Assistant UI and FastAPI Bookings Integration Handoff

Status date: 12 August 2026 (Australia/Sydney)

This is a factual engineering handoff. It is not an AI prompt and does not authorize deployment or other production changes.

## 1. Objective

The customer-facing conversational assistant must be able to handle the complete booking journey inside the SMS/chat conversation. The customer should not be sent to a booking webpage and should not need to complete the multi-step web form themselves.

The assistant should be able to:

- Report the current local business date and time.
- List active bookable services, descriptions, prices, durations, and stable service IDs.
- Find available times today.
- Find available times tomorrow.
- Find the next available time, optionally after a requested date/time.
- Present several live available options when useful.
- Collect the customer's name, service, exact time, and optional notes conversationally.
- Present the complete proposed booking back to the customer.
- Require a later, explicit customer confirmation before making the booking.
- Recheck availability immediately before the write.
- Create the booking through the booking system API, without Playwright or browser automation.
- Return an accurate confirmation or a useful conflict/failure response.

The long-term booking system is the sibling `FastAPI_bookings` application. Assistant-facing tool names and payloads should remain stable even if the booking implementation changes.

## 2. Repositories and workspaces

### Assistant UI

- Primary repository: <https://github.com/elle4a69/assistant-ui>
- Secondary repository/copy: <https://github.com/Ciccio666/assistant-ui>
- Local workspace: `F:\Projects\assistant-ui`
- Working branch: `main`
- Primary remote name: `upstream`
- Secondary remote name: `origin`
- Current checked-out commit before local changes: `050912a` (`Reduce booking outlines to one pixel`)
- Pushes to `upstream/main` trigger the `Fly Deploy` GitHub Actions workflow.

### FastAPI Bookings

- Local workspace: `F:\Projects\fastapi_bookings`
- Its checked-in OpenAPI description is `F:\Projects\fastapi_bookings\openapi.json`.
- Its relevant server implementation is under `F:\Projects\fastapi_bookings\app`.
- No remote repository or production deployment details for this application were established during this work.

## 3. Assistant UI production infrastructure

### Fly.io

- Application: `assistant-ui-hub`
- Public hostname: <https://assistant-ui-hub.fly.dev>
- Primary region: `syd`
- Current machine ID: `683d510b606048`
- Allocation: one shared CPU and 1 GB RAM
- Internal application port: `8080`
- Persistent volume: `assistant_data_volume`
- Volume mount: `/data`
- Health check: `GET /api/health`
- Health-check interval: 15 seconds
- Health-check timeout: 3 seconds
- Health-check grace period: 20 seconds
- HTTPS is forced.
- Automatic machine stop is disabled.
- Automatic machine start is enabled.
- Minimum running machines: one.

### Runtime and build

- Root `Dockerfile` uses `python:3.11-slim`.
- Working directory in the image is `/app`, then `/app/backend` at runtime.
- Python packages are installed from `backend/requirements.txt`.
- Production command:

  ```text
  python -m uvicorn main:app --host 0.0.0.0 --port 8080
  ```

- The GitHub Actions workflow runs on pushes to `main`:

  1. Check out the repository.
  2. Install Node.js 20.
  3. Run `npm ci` in `frontend`.
  4. Run `npm run build` in `frontend`.
  5. Install `flyctl`.
  6. Run `flyctl deploy --remote-only` using the GitHub `FLY_API_TOKEN` secret.

- The backend serves `frontend/dist` as the production SPA when that directory exists.

### Application stack

- Backend: FastAPI, Python 3.11, SQLAlchemy, Pydantic, Uvicorn.
- Frontend: React 18, TypeScript, Vite 5, Tailwind CSS 4, `@assistant-ui/react`.
- Frontend local development port: `5190`.
- Vite's current local `/api` proxy target is `http://127.0.0.1:8025`.
- Database: SQLite by default, configurable through `DATABASE_URL`.

### Persistence

- On Fly, `PERSIST_DIR` resolves to `/data`.
- Default production database: `/data/assistant.db`.
- `DATABASE_URL` overrides that default when set.
- Other persistent paths include:

  - `/data/data` for settings JSON such as services and working hours.
  - `/data/knowledge` for knowledge files.
  - `/data/prompts` for prompt templates.
  - `/data/bootcamp.db` for bootcamp/style data.

- On first use of a mounted `/data` volume, bundled database, prompt, and data defaults are copied only when an appropriate persistent destination does not already exist.

## 4. Protected configuration

Never print, commit, copy into a handoff, or expose to the language model any credential value.

- `APP_USERNAME` and `APP_PASSWORD` protect administrative routes.
- `FLY_API_TOKEN` is stored as a GitHub Actions secret.
- OpenAI credentials are stored in environment variables or secrets.
- SMS provider credentials are stored in environment variables, Fly secrets, or protected application settings.
- Google Calendar credentials are stored outside source control or in protected environment configuration.
- FastAPI Bookings credentials must be stored as runtime/Fly secrets.

The booking adapter recognizes these non-secret configuration names:

```env
BOOKING_BACKEND=legacy
BOOKING_TIMEZONE=Australia/Hobart
```

For FastAPI discovery:

```env
BOOKING_BACKEND=fastapi
FASTAPI_BOOKINGS_URL=<booking-service-base-url>
FASTAPI_BOOKINGS_TENANT=<tenant-slug>
FASTAPI_BOOKINGS_TOKEN=<runtime-secret>
BOOKING_TIMEZONE=Australia/Hobart
```

No real values are included here.

## 5. Existing access behavior

- Administrative routes are protected with application basic authentication when `APP_PASSWORD` is configured.
- Explicitly public paths include the health endpoint, SMS webhook, public website, static assets, and the legacy customer booking widget paths.
- CORS currently allows all origins and methods, with credentials disabled.
- API and live booking responses receive no-cache headers.

The FastAPI Bookings integration is server-to-server. Its token and tenant configuration must never be placed in frontend JavaScript or returned in LLM tool output.

## 6. Work currently implemented locally

The following files are modified or untracked in `F:\Projects\assistant-ui` and are not committed, pushed, or deployed:

- Modified: `README.md`
- Modified: `backend/main.py`
- New: `backend/booking_tools.py`
- New: `backend/test_booking_tools.py`
- New: `backend/test_conversational_booking.py`

### Conversational booking authorization flow

The local implementation adds a two-stage booking flow:

1. The assistant gathers an exact service, exact offered time, customer name, and optional notes.
2. `propose_booking` validates the details and availability but does not create anything.
3. The assistant presents the complete proposal and asks whether it is correct.
4. The proposal is retained on the conversation thread only after the proposal-summary response is successfully dispatched.
5. On a later customer message, `confirm_booking` is allowed only for a short, explicit confirmation.
6. Availability is checked again.
7. The booking is created only after that check succeeds.
8. The pending proposal and pending time choices are cleared after successful creation.

Safety behavior includes:

- Ambiguous replies do not authorize booking.
- A reply that changes details, such as “yes, but make it 4 pm,” does not authorize the old proposal.
- Explicit rejection clears the pending authorization.
- Proposals expire after two hours.
- Duplicate customer bookings at the same time are treated as already confirmed.
- A time becoming unavailable between proposal and confirmation prevents creation.
- Replies `1`, `2`, or `3` select a previously offered time only. They do not directly create a booking.
- The assistant is instructed never to send the customer to a form or webpage.
- The assistant must not claim success unless the confirmation tool reports `confirmed` or `already_confirmed`.

The thread schema now includes nullable `pending_booking` JSON text, with a SQLite startup migration for existing databases.

### Assistant-facing discovery tools

`backend/booking_tools.py` defines a provider-neutral discovery suite with these model tools:

| Tool | Purpose | Write operation |
|---|---|---:|
| `get_current_time` | Current local business date, time, weekday, and timezone | No |
| `list_booking_services` | Active services with stable IDs, duration, price, and description | No |
| `get_times_today` | Remaining available slots today for one service | No |
| `get_times_tomorrow` | Available slots tomorrow for one service | No |
| `get_next_available` | First available slot after now or a supplied timestamp | No |
| `propose_booking` | Validate and stage a complete proposal | No external write |
| `confirm_booking` | Finalize the staged proposal after explicit confirmation | Yes |

Discovery has two adapters:

- `LegacyCalendarDiscoveryProvider` reads current `services.json`, working hours, and Google Calendar/SQLite busy slots.
- `FastAPIBookingsDiscoveryProvider` calls the FastAPI Bookings server over HTTP.

The model sees normalized service and slot data, not transport details or credentials.

## 7. FastAPI Bookings API contract discovered locally

The following endpoints already exist in the sibling application.

### Bootstrap and services

`GET /api/public/bootstrap`

Returns tenant/company information, active services, providers, locations, booking rules, and timezone. The discovery adapter uses this endpoint for service data.

Relevant service fields include:

- `id`
- `name`
- `description`
- `duration` in minutes
- `price`
- `active`
- `is_visible`
- provider relationships
- booking buffers, group limits, fixed start times, and advance-booking limits where configured

### Availability search

`POST /api/public/search-availability`

Accepted search fields include:

- `service_id`
- `provider_id`
- `location_id`
- `category_id`
- `desired_time`
- `date_from`
- `date_to`

Returned availability items include:

- `start_time`
- `end_time`
- provider ID and name
- service ID and name
- required resources where applicable

This is preferable to the narrower `GET /api/public/availability`, because that endpoint requires the caller to choose a provider before searching.

### Client identification

`POST /api/public/clients/identify`

- Accepts phone and/or email as query parameters.
- Finds an existing tenant-scoped client or creates a minimal client.
- Returns the client record and a `created` flag.

The SMS assistant should send the canonical customer phone number and retain the returned client ID only in server-side booking state.

### Booking creation

`POST /api/public/bookings`

The current `BookingCreate` request requires:

- `client_id`
- `provider_id`
- `service_id`
- optional `location_id`
- `start_time`
- `end_time`
- optional `notes`

Public bookings are created with `pending` status and may require later staff/provider approval. The route checks tenant ownership, client restrictions, and slot conflicts. A provider conflict returns HTTP 409.

### Tenant resolution and headers

- Tenant resolution supports an `X-Tenant` header, a `tenant` query parameter, or an appropriate host subdomain.
- Public endpoints optionally accept `X-Token`.
- The assistant adapter sends `X-Tenant` and `X-Token` only from protected server-side configuration when configured.

## 8. Current migration boundary

Read-only discovery can switch to FastAPI Bookings now by configuration. Final booking creation has not yet migrated: `confirm_booking` still rechecks and writes through the current Google Calendar/SQLite calendar service.

This split must remain explicit. Do not enable `BOOKING_BACKEND=fastapi` and describe the whole write path as migrated.

The full write migration still needs:

1. A server-side FastAPI Bookings command adapter.
2. Client identification by canonical SMS phone number.
3. Provider selection from the exact availability result selected by the customer.
4. Location selection when the service or slot requires it.
5. Storage of provider ID, location ID, exact start/end, and backend identity in the staged proposal.
6. A final availability recheck against FastAPI Bookings after explicit confirmation.
7. Idempotent booking creation.
8. Correct handling of HTTP 409 and other booking-domain failures.
9. An authoritative returned booking ID and status.
10. Tests proving retries cannot create duplicates.

## 9. Recommended final write design

### Stable internal command

Keep the model-facing `confirm_booking` tool free of customer-supplied write parameters. It should operate only on the server-side staged proposal.

The staged proposal should contain at least:

```json
{
  "backend": "fastapi",
  "tenant": "server-resolved",
  "service_id": "7",
  "service_name": "Consult",
  "provider_id": 3,
  "provider_name": "Provider name",
  "location_id": 2,
  "start_time": "2026-08-14T09:00:00+10:00",
  "end_time": "2026-08-14T09:30:00+10:00",
  "duration": 30,
  "customer_name": "Customer name",
  "customer_phone": "+61412345678",
  "notes": "",
  "created_at": "2026-08-12T00:00:00Z",
  "proposal_id": "opaque-unique-id"
}
```

The actual tenant value and any credentials remain server-side. They must not be copied into the proposal returned to the model.

### Confirmation transaction sequence

1. Verify the latest customer message is an explicit confirmation.
2. Load and validate the staged proposal.
3. Reject expired or altered proposals.
4. Resolve or create the FastAPI Bookings client by canonical phone.
5. Re-query the exact service/provider/time window.
6. Ensure the selected provider and exact start/end are still present.
7. Submit the booking using the resolved client ID and staged slot identifiers.
8. Treat a duplicate idempotency result as success with the original booking ID.
9. Treat HTTP 409 as “that time has just been taken,” clear the stale proposal, and offer fresh times.
10. Persist the returned booking ID and status before sending a success message.
11. Clear pending booking state only after a durable success or definitive stale-slot failure.

### Idempotency requirement

The existing FastAPI Bookings public create endpoint does not expose an idempotency contract in the inspected schema. Add one before production cutover. Recommended options:

- Preferred: accept an `Idempotency-Key` header, enforce a unique tenant-scoped key, and return the original result for retries.
- Alternative: add a unique `external_reference`/`channel_booking_id` to booking creation and enforce uniqueness per tenant.

Use the stable assistant proposal ID as the idempotency value. Network timeouts, worker retries, duplicate webhook delivery, and repeated model tool calls must not create a second booking.

### Customer names

The existing identify endpoint may create a minimal phone-only client. If a newly created or existing client has no usable name, update it from the explicitly confirmed booking name through a purpose-built server-side endpoint or expanded identify contract. Do not silently overwrite a meaningful existing customer name.

### Booking status language

FastAPI Bookings currently creates public bookings with `pending` status. Customer-facing wording must reflect the returned status:

- If the business intends immediate final confirmation, add a trusted agent-specific booking endpoint with the intended confirmed state and appropriate authorization.
- If public bookings must remain pending approval, say “booking request received” rather than “confirmed.”

This product decision must be settled before cutover.

## 10. Error and safety contract

Expected adapter results should be structured and safe for the model:

- `ok`: operation completed.
- `awaiting_confirmation`: proposal validated but not booked.
- `confirmed`: booking durably created and confirmed.
- `pending`: booking request created but awaiting business approval.
- `already_confirmed`: idempotent retry returned the existing booking.
- `unavailable`: dependency temporarily unavailable.
- `rejected`: invalid service, invalid proposal, expired confirmation, customer restriction, or unsupported request.
- `conflict`: selected slot is no longer available.

Never return raw exception traces, credentials, access tokens, internal authorization headers, or unrestricted upstream response bodies to the language model or customer.

Network calls should have bounded timeouts. Only safe idempotent requests may be retried automatically. A booking POST may be retried only when protected by an idempotency key.

## 11. Testing state

Latest local verification:

- `python -m pytest backend -q`
- Result: `171 passed`
- `python -m py_compile backend\booking_tools.py backend\main.py`
- Result: passed
- `git diff --check`
- Result: no whitespace errors; Git reported only expected LF-to-CRLF working-copy warnings.

Two existing warnings remain:

- `pytest-asyncio` default loop-scope deprecation warning.
- Pydantic v2 class-based configuration deprecation warning in `WebhookSMSInput`.

Tests specifically cover:

- Explicit confirmation and rejection phrases.
- Ambiguous or detail-changing replies.
- Proposal without immediate booking.
- Later confirmation creating exactly one booking.
- Availability becoming occupied before confirmation.
- Complete two-turn assistant flow.
- Execution of a discovery tool through the OpenAI tool-call loop.
- Current time, services, today, tomorrow, and next-available helpers.
- Working hours, service duration, and busy-slot filtering.
- Absence of the old booking-form-link tool and direct numeric booking shortcut.

## 12. Deployment and operational checklist

Before any deployment:

1. Review the uncommitted diff and confirm it contains no unrelated user work.
2. Confirm no credentials or local database files are staged.
3. Run the full backend test suite.
4. Build the frontend with `npm run build` from `frontend`.
5. Confirm the production booking backend URL, tenant, timezone, and token are present as Fly secrets/environment values.
6. Confirm FastAPI Bookings is reachable from Fly.
7. Confirm the desired public booking status semantics (`pending` versus `confirmed`).
8. Confirm idempotency exists before switching final creation.
9. Test a real service lookup and availability query without logging protected headers.
10. Test one booking in a controlled tenant, then verify booking ID, client, provider, service, timezone, and status in FastAPI Bookings.
11. Test a duplicate webhook/tool retry and verify only one booking exists.
12. Test a race in which another booking takes the slot after proposal but before confirmation.
13. Push to `upstream/main` only when production deployment is intended, because that push automatically triggers Fly deployment.
14. Verify GitHub Actions `Fly Deploy`, Fly health checks, application logs, and the public hostname after deployment.

## 13. Out of scope/not yet implemented

- No settings-page full developer LLM UI was implemented.
- No onboarding voice agent or realtime audio flow was implemented.
- No automated website generation/onboarding implementation was added.
- No FastAPI Bookings write adapter was completed.
- No FastAPI Bookings idempotency endpoint or database migration was added.
- No credentials were created, moved, printed, or committed.
- No commit, push, GitHub Actions run, or Fly deployment was performed.

## 14. Immediate next milestone

Complete the FastAPI Bookings write path as a separately tested phase:

- Add idempotency support to FastAPI Bookings.
- Add a trusted, tenant-scoped agent booking contract if bookings must be immediately confirmed.
- Implement `FastAPIBookingsCommandProvider` in Assistant UI.
- Extend staged proposals with provider/location/backend identity and an opaque proposal ID.
- Switch availability validation, duplicate detection, and creation together; do not split reads and writes across different booking backends after cutover.
- Retain the existing explicit two-turn authorization behavior.
- Run contract, integration, retry, race, and end-to-end SMS tests before enabling production configuration.
