# Release readiness

Local integrated build: implemented and tested. Public production release: **not yet verified or deployed**.

Implemented: immutable autonomy lifecycle; canonical headline screening; server-side period aggregates and bounded case pagination; Today workload metrics; Impact; prospective 7/30/90-calendar-day reviews; append-only outcome assessments; attributed missed-event/usefulness reports; Learning Journal; note and list-rule approval/edit/pause/revoke; manual/CSV holdings and watchlist onboarding; pins; explicit mandates; a three-tier universe; bounded rotating discovery from 32 curated companies; independent comparative investigations and non-trading portfolio proposals. No recommendation approval can change holdings. Manual holding edits require a separate reviewed entry confirmation and do not verify external transactions.

Real operation uses news and real Nemotron on Nebius. Verified Replay uses authenticated preserved model outputs. The isolated illustrative dataset contains 120 deterministic historical cases across 60 days, generated through Engine policy, rule, persistence and verification paths. Its simulated model outputs, approvals, chronology and retrospective outcomes are labelled. It is never included in genuine counters. No historical operating data or investment returns were invented as genuine evidence.

## Exact local startup

```powershell
cd C:\Users\antho\marketbell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
npm run dev
```

On this machine the isolated environment is already installed. Open `http://127.0.0.1:4318`. Python-only startup: `.\.venv\Scripts\python.exe -B -m backend.main`. Tests: `npm test`; syntax: `npm run lint`; frontend packaging: `npm run build`.

Use server-side environment or a secure existing key file for local Nebius access. No credentials are present in the release sources. Authenticated webhooks are optional and disabled without a separate secret. The production template disables live inference, watch and scheduler until explicitly configured. Existing secure local credentials must not be copied into public deployment.

## Public deployment procedure and remaining gates

1. Approve host/domain, recurring budget, dedicated deployment key, public source publication and replay/news/data redistribution. Original reused internal files have no detected public license grant; resolve ownership/license before selecting a public repository license.
2. Use a working approved Linux Docker engine. Run `docker build -t marketbell:release .`; record image digest. This host's Docker engine remains unavailable, so no container build/run is claimed.
3. Copy `.env.production.example` to the ignored `.env.production`; set exact HTTPS `MARKETBELL_PUBLIC_ORIGIN`; inject dedicated secrets through the host secret manager. Set the configured call and estimated-spend caps and provider billing limits.
4. Run `docker compose up -d --build`; retain the `/data` volume; use an approved TLS reverse proxy to loopback 8080. One Gunicorn process owns the worker and SQLite lock. Multiple replicas require distributed leasing and shared persistence; they are not supported.
5. Verify `/healthz`, restricted `/api/diagnostics` (dedicated bearer token), persistent state, actual worker heartbeat, source health, CSRF/host/cookie protections, the teach-once walkthrough, rollback and backup restore. Enable only approved source/inference channels. No direct SEC or intraday price polling is needed.
6. Publish the approved source repository/license, tested demo URL and ≤3-minute video; provide required Nebius/NVIDIA feedback. None was published by this work.

Rollback: stop the service, preserve the current volume and backup, then run the previously recorded image digest with a compatible database backup. Migrations are additive and backed up; do not overwrite the only current database. See DEPLOYMENT.md for details.

## Five principal remaining risks

1. Untested production container/TLS/process supervision and unapproved hosting costs.
2. Source/replay/internal-code redistribution rights and incomplete headline-source coverage.
3. No mature genuine retrospective effectiveness record or total-return benchmark.
4. Anonymous 30-day browser workspace credentials lack registered-account recovery; use demo records until that account lifecycle is implemented and reviewed for private portfolios.
5. Thirty-two curated companies, bounded backfill and single-worker personalization constrain scale; multi-day endurance and broad browser/user studies remain outstanding.

No brokerage execution, financial transfer, unauthorized ownership change or fabricated investment-return capability exists. Confirming recommendations and manually recording holdings are separate user actions.
