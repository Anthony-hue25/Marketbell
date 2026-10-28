# MarketBell integrated architecture

The warm service-oriented frontend remains plain HTML/CSS/JavaScript. The Node launcher starts the isolated Python backend; no original project code is imported at runtime. Vendored modules and recorded evidence are local to MarketBell.

## Modules

- `backend/store.py`: SQLite migrations v1–v2, WAL, transactions, tenant documents, sessions, persistent jobs, daily call accounting, hash-linked audit, update/delete guards, model/note tamper verification and online backup.
- `backend/market.py`: read-only Alpaca adapter, existing authorized cache import, Massive fallback, bounded headline RSS and fixed primary issuer evidence. Source evidence includes retrieval and publication limitations.
- `backend/ai.py`: provider-neutral validated decisions, exact quoted evidence, short citation aliases, separate Research and blind Challenge, bounded retry, correction-to-candidate interpretation. Hidden chain-of-thought is never requested for display or persisted.
- `backend/engine.py`: hard-policy precedence, conservative disagreement resolution, note preparation/readback, explicit decisions, candidate confirmation, rule scope/expiry/conflict/revision/revocation.
- `backend/recordings.py`: genuine captured-run manifest validation, source provenance and trace replay.
- `backend/worker.py`: OS singleton lock, durable queued/running/completed/failed states, bounded restart recovery, per-case isolation, an independent continuous detector thread, source cursors, news polling, authenticated webhooks, priority queueing, exact-job fast replay independent of slow inference.
- `backend/app.py`: same-origin WSGI API, opaque HttpOnly sessions, hashed tokens, CSRF, body/rate limits, tenant scoping, private admin boundary. Only isolated demonstration portfolios are accepted; private account import is disabled.
- `public/app.js`: Today, Portfolio, Activity, Preferences, mode labels, correction confirmation, action status, evidence inspection. The browser cannot grant authority by changing localStorage.

## One workflow, three modes

All paths use Engine ingest/investigate/resolve and the same deterministic note authority. Verified Run injects authenticated captured public model outputs after manifest/schema/grounding checks and applies the policy in a historical isolated demo. Live Nemotron makes fresh Research and Challenge calls over preserved evidence, applying the current completed-session freshness requirement. Autonomous imports current authorized market data, prioritizes cases and makes fresh calls before persisting outcomes. Slow live calls never block instant replay jobs.

## Persistence and trust

The document table models portfolio, events, evidence, preferences, candidate/approved standing rules, note actions and failed evidence revisions. An evidence refresh after technical failure creates an auditable prior revision before resetting model stages. Explicit authority checks and note readback occur inside immediate transactions, preventing duplicate or stale decisions. Sessions/jobs/cost accounting have dedicated tables. A future migration should split document schemas if query volume warrants it, rather than building a large trading engine now.

The journal and note/model commitments detect accidental or unauthorized content changes within the retained history. They do not defend against a database owner replacing all state or truncating a history suffix without an external checkpoint. Keep off-host backups and audit heads. One process owns the worker; multiple replicas require distributed leasing and a shared production database.

## Deployment boundary

Local development uses a threaded WSGI server on loopback 4318. The prepared production image uses Gunicorn behind an approved TLS reverse proxy, non-root/read-only filesystem and persistent `/data`. Public fresh inference and scheduling default off until dedicated deployment credentials and cost controls are approved. Docker runtime verification and actual deployment remain external release gates. No live trading code, broker endpoints, private account imports or production credential publication exists.


## Event-driven operating model (supersedes session-centric descriptions)

Continuous source detection is the primary entry point. The monitor thread runs independently of the model worker. Lightweight news checks feed durable prioritized notices; approved webhooks use the same pipeline. Completed sessions maintain price and broader-screen context. A new qualitative event can be investigated without waiting for a fresh close; missing current price context blocks preparation rather than preventing qualitative analysis.

User changes and explicit questions create investigation jobs. Shared public-source analyses can be reused for identical evidence, with separate current portfolio policy, standing authority and audit records per subscribed demo tenant. No authority is inherited across users. Source rates, health and bounded missed-window recovery are visible. See docs/CONTINUOUS_OPERATION.md for verified and unavailable channels.


## Autonomy, impact and dynamic universe

`backend/impact.py` owns immutable lifecycle, original decision snapshots, append-only horizon reviews, canonical source identity, attributed feedback, indexed projections and bounded history queries. SQLite migrations now span v1–v7. Existing raw records and journal history are preserved; migration backfills are labelled. Outcome reviews never rewrite the original thesis or grant authority.

`backend/universe.py` owns reviewed manual/CSV records, the 32-company exchange-verified catalog, pins, mandates, non-trading list rules and portfolio proposals. News-led discovery rotates through the wider curated catalog. User-entered holdings remain distinct from watchlist and pool candidates. List organization adds no exposure; note preparation retains its quantitative governor. Brokerage execution and agent-created ownership are prohibited. Automatic list rules and edits require explicit confirmation; conflicts ask again.

`model_calls` tracks safe attempt metadata and conservative spend reservations. One inference worker and four bounded news-fetch threads remain. Durable worker heartbeat is every ten seconds while idle; source monitoring has its own thread. `/api/diagnostics` requires a private bearer token. Frontend responses omit private requests/transcripts and bound event/history payloads. Personal review follows opted-in durable portfolio records, independently of browser sessions.


## Azure-ready process model (supersedes the single-process statements above)

- `backend/db.py`: dialect boundary. SQLite locally; PostgreSQL in Azure (SQLite-compatible helper functions,
  one global advisory write lock preserving `BEGIN IMMEDIATE` semantics for the hash chain, small connection
  pool, Entra ID token auth via the Container Apps managed identity).
- `backend/worker.py`: database **leases** replace the OS file lock across containers — singleton `worker` and
  `monitor` roles with standby replicas, atomic fenced job claims, per-job leases renewed while running,
  recovery of expired leases only, graceful shutdown that returns work without consuming a retry, parallel
  investigation slots, scheduled `run_once` mode.
- `backend/worker_main.py`: the worker process with internal health probes. `backend/production.py`: the API.
- `backend/telemetry.py`: structured JSON events on stdout for Log Analytics alerts; sensitive fields dropped.
- New migration v9: `leases`, `call_budget`, append-only `evidence_versions`, job lease columns.
- `scripts/audit_anchor.py`: daily chain verification + write-once external head anchor (detects truncation).
