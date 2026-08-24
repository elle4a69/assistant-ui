# Autonomous Operations Console

The authenticated `/agent-console` page accepts one high-level objective and
streams a bounded Operations AI run over the same-origin `/ws/agent` WebSocket.
Runs and ordered events are persisted in the application's SQLite database, so
the browser can reconnect and replay an existing run without resubmitting it.

## Execution boundary

The Fly web process does not expose a shell. `run_terminal_command` is a
structured virtual command with an explicit, reviewed read-only allowlist.
Adding a tool to the wider Operations AI never grants it to this autonomous
loop. The virtual commands use local bounded operational evidence; source reads
use the separately bounded GitHub reader. One explicit queue command can start
an isolated GitHub coding task that writes and tests a review branch only. Web
research, runtime/deployment proposals, changing main, runtime settings, and
production deployment remain outside this loop in their existing audited,
owner-confirmed workflows.

Coding-task submissions reject customer phone numbers, email addresses,
credentials and secret-shaped values. Objectives must describe the defect with
anonymised engineering evidence; customer transcripts and identifying details
must never be copied into the external coding runner. Only one active coding
task may exist globally, regardless of its age, and one autonomous run cannot
submit the same work twice.

Local writes are limited to small, redacted scratch files below
`/data/agent-runs/<run-id>/workspace`. Absolute paths, traversal, symbolic
links, unsupported file types, oversized files and oversized workspaces are
rejected. Production SQLite, configuration, credentials and `/app` are never
writable through the console.

## Authentication and protocol

- The WebSocket fails closed unless `APP_PASSWORD` is configured.
- The signed HttpOnly admin-session cookie and same-origin `Origin` header are
  both required. Scheme, host, and port must match the configured public
  application origin. Secrets are never accepted in a URL.
- Production connects to `wss://<current-host>/ws/agent`; Fly's internal port
  `8080` is not exposed to the browser.
- Clients start with a UUID request ID or attach to a persisted run ID with an
  event-sequence cursor. Request IDs make reconnect/retry idempotent.
- The browser can explicitly cancel a run. Disconnecting the browser does not
  deploy code or promote a review branch.

## Limits and privacy

- One active run per application process.
- Maximum 15 model/action steps (configurable downward with
  `OPS_AGENT_MAX_STEPS`).
- 30 seconds per allowlisted action and a bounded total run duration. Actions
  use a dedicated single-worker executor, local database waits are capped at
  five seconds, and the source reader has its own shorter provider deadline, so
  a stalled read cannot consume the application’s general worker pool.
- Structured model output at every step; OpenAI storage is disabled.
- The legacy `thought` schema field is constrained to a short user-visible
  progress summary. Private chain-of-thought is not requested, stored or sent.
- Credentials and terminal control sequences are removed before output is
  stored or displayed.
- Completed run history is retained for at most 30 days/50 runs, and scratch
  storage has a global 16 MiB cap, so this feature cannot grow without bound on
  the production data volume.

Set `OPS_AGENT_AUTONOMOUS_ENABLED=false` to disable new runs immediately while
leaving audit history readable. Application rollback remains the normal GitHub
Actions/Fly rollback to the previous verified commit.
