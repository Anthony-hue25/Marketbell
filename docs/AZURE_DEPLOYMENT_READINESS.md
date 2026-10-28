# Azure deployment readiness

Legend: **Verified** = executed and passed here · **Local** = passed in local production-like containers only ·
**Prepared** = artifact exists and compiles/validates, not executed on Azure · **Open** = not done.

## Production build validation (section 20)

| Check | Status | Evidence |
|---|---|---|
| Image builds from `Dockerfile` with hash-pinned dependencies | Local | Built repeatedly; substitute Ubuntu 24.04/Python 3.12 base (Docker Hub unreachable here). CI builds on the digest-pinned `python:3.12-slim-bookworm`. |
| Startup commands (gunicorn API; `backend.worker_main`; `--once`; `scripts.audit_anchor`) | Local | T1–T14, smoke |
| Ports 8080 (API) / 8081 (worker probes) | Local | Probes and smoke |
| Environment handling (staging/production strictness; test seams refused) | Verified | `Configuration.*` tests; staging container |
| Database migrations (fresh DB, concurrent API + worker start, restart, restore) | Local | PG 16: fresh start 2.3 s; T13 restore |
| No Windows paths / developer files | Verified | `developer_path()`; container ran with no host mounts |
| No browser-dependent workers | Local | T1 with no client connected |
| Shutdown and restart | Local | T2 SIGKILL, T3 SIGTERM, full platform restart |
| No accidental demo/live activation | Verified | Image defaults `MARKETBELL_ALLOW_LIVE=0`, `MARKETBELL_WORKER=0`; Bicep sets live only on the worker |
| Health endpoints | Local | `/livez`, `/healthz`, worker `:8081` |
| Static assets, API connectivity, worker processing | Local | Smoke 13/13 incl. webhook → durable state |
| Read-only root FS, non-root, no capabilities | Local | All container runs |
| IaC static validation | Verified | Bicep CLI 0.48.1 `build` + `lint` + `build-params` (staging, production): 0 errors, 1 benign length warning; 25 resources |
| Container vulnerability + secret scan | Prepared | Trivy and gitleaks steps in the workflow; not run here |
| Dependency audit / SAST | Verified | pip-audit clean; bandit 0 medium/high |

## Networking and configuration (section 8)

| Item | Status | Notes |
|---|---|---|
| HTTPS/TLS | Prepared | Container Apps ingress terminates TLS (`allowInsecure:false`); app requires an `https://` origin in staging/production and sends HSTS. |
| Public vs internal ingress | Prepared | API external; worker and jobs have **no ingress**. |
| CORS | Verified | Not needed: UI and API are same-origin; foreign `Origin` is rejected for mutations. |
| Host / origin / proxy | Verified (local) | Host must equal the configured origin; one trusted `X-Forwarded-For` hop. |
| Private service access | Prepared | PostgreSQL private access in a delegated subnet + private DNS zone (`networkMode=vnet`). |
| DNS / custom domain | Prepared | Default Container Apps FQDN; `publicOrigin` parameter for a custom domain (managed certificate binding is a manual step). |
| Outbound to Nebius and news | Prepared | Platform egress; fixed hosts; timeouts 8–10 s connect, 15–90 s read; no redirects. **Not reachable from this test environment.** |
| Health checks / readiness | Local | Startup, liveness, readiness probes in Bicep for both apps. |
| Graceful shutdown | Local | `terminationGracePeriodSeconds: 60`, app grace 45 s. |
| Scaling | Prepared | api 1/1, worker 1/1, jobs cron. |
| Admin/monitoring endpoints not anonymous | Verified | Diagnostics 403 without token; worker port not exposed. |

## Secrets and identities (section 9)

| Item | Status |
|---|---|
| Key Vault + Container Apps Key Vault references; managed identity; least-privilege RBAC | Prepared (Bicep compiled) |
| Entra-only PostgreSQL authentication via managed identity token | Prepared; code path implemented (`db.managed_identity_token`), not exercised against Azure |
| No credentials in Git, images, browser bundle, logs | Verified (scan: 0 hits; image scan for secret files: none; log field filter test) |
| No personally owned permanent deployment credentials | Prepared (GitHub OIDC federation) |
| Rotation and recovery documented | Done (runbook) |

## Remaining cloud tests (must run on authorized Azure staging)

1. Phase-1 and phase-2 deployment of the Bicep template (what-if, then create).
2. Key Vault reference resolution and managed-identity token for PostgreSQL in the running apps.
3. Outbound reachability of Nebius Token Factory and Google News from Container Apps; one real Nemotron case.
4. Probe behaviour, revision rollover overlap (lease standby), worker restart, SIGTERM on revision change.
5. Log Analytics ingestion of `worker.heartbeat` and each alert rule firing once (stop the worker 20 minutes).
6. Point-in-time restore to a new server and the anchor job against Blob immutability.
7. Confirm no *Environment Management Hour* charge on the Consumption environment (cost model watch item).

## Required Azure access (not assumed)

Per resource group: Contributor + constrained Role Based Access Control Administrator (four roles only; no
subscription Owner). One-time by a subscription admin: resource group creation and provider registration. Ability to create
an Entra app registration with federated credentials; PostgreSQL Entra administrator (your account);
quota for 1 PostgreSQL Flexible B1ms and a Container Apps environment in the chosen region.

## Final verification additions (2026-10-20)

| Check | Status |
|---|---|
| Local repository identical to the assurance workspace (136 files, SHA-256) before this round; re-synced after | Verified |
| Adaptive Universe untouched (HEAD `ef76b6c`; its 72 pre-existing modifications all dated 2026-10-14, before this work) | Verified |
| `deploy.ps1` parses (PowerShell 7.6.7) and, with a recording `az` stub, issues the same calls as `deploy.sh`; tag-only images refused by both | Verified |
| `.github/workflows/release.yml` passes actionlint 1.7.12 | Verified |
| Production container: clean SIGTERM exit (code 0) for API and worker; smoke 13/13 on the rebuilt image | Local |
| CI jobs needing GitHub/Azure: `package-and-staging`, `production` (OIDC, ACR, what-if/deploy, smoke); `build` needs Docker Hub + Trivy DB; `security` gitleaks image pull | Require connected GitHub/Azure |
