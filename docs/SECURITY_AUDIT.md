# Security audit (full stack, 2026-10-19)

Scope: `backend/`, `public/`, container image, CI/CD, IaC, local secrets. Method: code review, attack tests
(`test/test_assurance.py`, existing suites), container tests, static analysis, dependency audit, secret scan.

## Identity model

This release has **no named user accounts**. Every visitor receives an opaque, server-side session
(`secrets.token_hex(32)`, stored hashed, HttpOnly + SameSite=Strict + Secure in production, 30 days) bound to an
isolated demo workspace. Browser storage grants nothing. The "users" in tenant isolation are these workspaces.
Real multi-user accounts (sign-in, recovery, account deletion) are **out of scope and a stated release limit**
for any deployment that stores real portfolios.

## Findings

| ID | Sev | Finding | Status |
|---|---|---|---|
| S1 | **High** (availability) | Behind Container Apps ingress `REMOTE_ADDR` is the proxy, so the 20-sessions-per-hour limit was shared by **all** visitors: the 21st judge in an hour was locked out (reproduced: 5 of 25 distinct visitors got 429). | Fixed: `MARKETBELL_TRUSTED_PROXY_HOPS=1` takes exactly one trusted `X-Forwarded-For` hop (right-most), validates it as an IP, ignores spoofed left entries. Tests + harness. |
| S2 | High (cost/availability) | Anonymous visitors' live inference shared the daily model budget with monitoring. | Fixed: separate interactive cap; live web inference off by default in Azure (`allowInteractiveLive=false`). |
| S3 | Medium | `/healthz` returned 409 on database failure, defeating readiness probes. | Fixed: 503 + `/livez` liveness split. |
| S4 | Medium | Production container could read developer-machine credential/data paths (`C:/Users/antho/...` defaults for the Nebius key file, Massive `.env`, Adaptive Universe data). | Fixed: developer defaults apply only outside production/staging (`config.developer_path`). |
| S5 | Medium | Model endpoint not overridable, but no test seam existed; a naive override would allow redirecting inference. | Added loopback-only test override, refused in production/staging (test). |
| S6 | Medium | Untrusted RSS XML parsed by ElementTree (bandit B314). | DTD/ENTITY content rejected before parsing; 2 MB cap retained. |
| S7 | Medium | Public demo could retain personal holding details (share counts, cost basis) from anonymous visitors. | `MARKETBELL_PUBLIC_DEMO=1` discards them before persistence (test). |
| S8 | Low | Missing HSTS, COOP, Permissions-Policy, `form-action`. | Added (HSTS in production/staging). |
| S9 | Low | `monitor` action accepted any non-empty admin token length. | Requires ≥ 24 characters like diagnostics. |
| S10 | Info | Plaintext Nebius key file inside the *Adaptive Universe* folder (`NEBIUS API  KEY.txt`, git-ignored there). Not copied, not changed (read-only project). | Recommend moving to a secret store and rotating before any production use. |

## Verified controls (attack tests)

| Threat | Result |
|---|---|
| Cross-tenant IDOR (approve/reject/correct/revoke/edit/retry/feedback/proposal/universe decision/impact detail) | All rejected with tenant-scoped lookups; victim state unchanged (`TenantIsolation`). |
| CSRF / Origin / Host | Token bound to session; foreign Origin and Host rejected (existing tests + smoke). |
| XSS | Every server string rendered through `esc()`; non-escaped interpolations are server numbers/ids or constants; CSP `script-src 'self'` with no inline script. |
| Admin/monitoring endpoints | `/api/diagnostics` and `monitor` require bearer token (disabled when unset); worker health port has no ingress. Smoke: 403. |
| Webhook | Bearer token ≥ 24 chars, JSON only, ≤ 6 kB, symbol allow-list, timestamp window (future > 5 min, older than 90 days, naive → rejected), source string validated, text sanitized; rate limit configurable. |
| Prompt injection via news | Evidence and quoted trigger text declared untrusted; outputs strict JSON schema with exact-substring citations; deterministic policy owns authority; no tools, URLs or code execution offered to the model. |
| SSRF | No user-supplied URL is ever fetched; outbound hosts fixed in code; redirects disabled. |
| Malicious upload | CSV ≤ 20 kB parsed with catalog/exchange validation; body ≤ 30 kB. |
| Sensitive errors | Only `UPPER_SNAKE` codes leave the server; provider errors redacted and not persisted. |
| Logs | `telemetry.emit` drops fields named like secrets, tokens, text, bodies, holdings (test); no request bodies or news text logged. |
| Brokerage | No order capability anywhere; market data adapter restricted to daily bars. |

## Secrets and identities

- Secret scans, by coverage (values never printed):

  | Scan | Covers | Result |
  |---|---|---|
  | Known-value scan (2026-10-19): the actual Nebius key value compared byte-for-byte | Every file in the working tree except `.venv` and `.git` (tracked **and** untracked, including uncommitted work, `work/` databases and docs) + `git log -p --all` | 0 hits |
  | Pattern scan (same run): key/secret assignment patterns | Same as above | 0 hits |
  | **gitleaks 8.30.1 `git`** (2026-10-20) | Full Git history: all 3 commits | **No leaks** |
  | **gitleaks 8.30.1 `dir`** (2026-10-20) | Entire folder including untracked files (92 MB) | 6 findings, all `generic-api-key` false positives in `.venv/…/numpy/random/tests` (third-party test vectors, not deployed) |
  | `scripts/security_check.py` (pre-existing) | Tracked files only | — (not relied on) |
  | Image scan | `/app`, `/opt/venv` in the built image | No `.env`, key, `.sqlite3` files |

  Uncommitted work is **not** in Git history, so only the `dir`/known-value scans cover it; re-run
  `gitleaks git` after you commit.
- Azure: Key Vault (RBAC, purge protection) + Container Apps Key Vault references; user-assigned managed
  identity with least-privilege role assignments; PostgreSQL Entra-only authentication (password auth disabled);
  GitHub OIDC for CI/CD. No secrets in images, Bicep, parameters, browser bundle or logs.
- Rotation: rotate in Key Vault → `az containerapp revision restart` (or new revision) picks up the new value;
  webhook/admin tokens are independent; Nebius key rotation does not touch the database. See runbook.
- Separation of configuration: `development`/`test` (local, test seams allowed), `staging` and `production`
  (HTTPS origin mandatory, secure cookies, live web inference off by default, developer paths disabled).

## Supply chain

| Control | Status |
|---|---|
| Dependency vulnerabilities | `pip-audit -r requirements.lock`: **no known vulnerabilities** (2026-10-19). No npm dependencies. |
| Static analysis | `bandit -ll`: 0 medium/high after fixes (3 reviewed with justification: fixed-table SQL, probe bind, guarded XML). |
| Reproducible install | `requirements.container.lock` with every PyPI file hash; image installs with `--require-hashes --no-deps --only-binary`. |
| Base image | Pinned by digest in CI (resolved and recorded per build). Locally validated on a substitute Ubuntu 24.04 + Python 3.12 base because Docker Hub was not reachable from this environment — **the `python:3.12-slim` base itself was not tested here.** |
| Runtime | Non-root uid 10001, read-only application files, read-only root FS and `cap_drop: ALL` in compose and tests. |
| Image scanning / secret scanning | Trivy (CRITICAL/HIGH, fixed) and gitleaks (full history) in the pipeline; **not executed here** (no registry access). |
| Upgrades | Pins unchanged except the new `psycopg[binary]==3.2.13` (+ `typing_extensions` for 3.12); full regression run on SQLite and PostgreSQL. |

## Addendum 2026-10-21 (sources, quiet log, Ask)

| Area | Control |
|---|---|
| New outbound hosts | `www.sec.gov`, `data.sec.gov` (fixed URLs built from a numeric CIK; no redirects; 15 s timeout; 8 MB cap; ≤ 6 requests/s). Wire search reuses the Google News host. |
| SEC identity | The filer record's `tickers` must contain the symbol, or the channel fails closed (`SEC_IDENTITY_MISMATCH`). |
| `SEC_USER_AGENT` | A contact string, not a credential; must look like "name email". No SEC login exists or is used. |
| Publisher tier spoofing | Tier from the feed's publisher domain with exact or subdomain match; display names are ignored. |
| Quiet-log overturn | Tenant-scoped record; rate-limited (20/h). In the public demo it never changes shared monitoring; only a personal deployment trusts the publisher for future triage. Live investigation of an overturned item needs live inference and uses the visitor's interactive budget. |
| Ask MarketBell | Records-only answers need no model. Model answers: strict schema, every claim an exact quote from a supplied record, buy/sell recommendation phrasing refused (`ASK_ADVICE_REFUSED`, falls back to the records answer), 20 questions/h per workspace, interactive budget. |
