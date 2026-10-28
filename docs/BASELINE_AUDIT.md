# Baseline audit — 2026-10-14

Commit: fbda7ffa3a21f18a631ce1b5f080dedefe948861

Pre-existing uncommitted changes are preserved in work/audit/pre-audit.patch; database backup is work/audit/pre-audit.sqlite3. Original repositories remain read-only.

Real: news RSS, Nebius/Nemotron, durable SQLite queue and stage persistence, server-side note authority. Replay: authentic recorded inference. Controlled: demo portfolios and injected acceptance sources. Missing: registered private accounts, production hosting/container verification, licensed comprehensive news coverage.

Independent baseline test output: work/audit/baseline-tests.txt. Baseline HTTP observations: work/audit/baseline.json.

Initial findings: deferred feed notices are not durable; database-error handling can terminate worker threads; successful empty RSS and provider failure are conflated; daily call limit lacks dollar ceiling and failed-call telemetry; diagnostics lack liveness and backlog measurements. These require fixes and regression tests.

Working tree before audit:
```
 M .env.example
 M .env.production.example
 M ARCHITECTURE.md
 M README.md
 M backend/app.py
 D backend/detect.py
 M backend/market.py
 M backend/store.py
 M backend/worker.py
 M docs/CONTINUOUS_OPERATION.md
 M docs/COSTS.md
 M docs/DEPLOYMENT.md
 M docs/RELEASE_AUDIT.md
 M public/app.js
 M scripts/continuous_acceptance.py
 M test/test_backend.py
```
