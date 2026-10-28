# Staging release checklist (one controlled Azure staging deployment)

Tick in order. Commands: PowerShell on Windows (`infra/azure/deploy.ps1`) or bash (`deploy.sh`). Windows script
execution is disabled on this machine, so start each PowerShell session with
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` (this window only), and use `npm.cmd` instead of `npm`. Details in
`docs/AZURE_DEPLOYMENT_RUNBOOK.md`. Nothing here has been run on Azure yet.

## A. Before Azure (you, ~30 min)

- [ ] **Rotate the Nebius key** (old one is plaintext in `adaptive-universe-lab\NEBIUS API  KEY.txt`). Keep the new key
      outside both repositories (password manager / a file outside the repos referenced by `NEBIUS_KEY_FILE`).
- [ ] **Live check** (≤ 4 model calls, reservations hard-capped at $0.05):
      `.venv\Scripts\python -m pip install -r requirements.lock` then
      `.venv\Scripts\python -B -m scripts.live_bounded_check` → must print `"passed": true`; file
      `docs\evidence\LIVE_BOUNDED_CHECK.json`. Then view the case: the command in its `view_in_browser` field.
- [ ] **Re-run the live check once after the 2026-10-21 update** (multi-source news). Optional: add
      `SEC_USER_AGENT=MarketBell research <your email>` to `.env` first so the SEC filings channel is exercised
      (free; no SEC login). The report shows `by_tier`, `by_channel` and `sec_edgar_enabled`.
- [ ] In `infra/azure/staging.bicepparam` set `secUserAgent` the same way (not a secret).
- [ ] **Commit** the assurance work on a branch, push to GitHub, let `.github/workflows/release.yml` run
      `test`, `security` (pip-audit, bandit, **gitleaks history**, Bicep) and `build` (**official
      `python:3.12-slim-bookworm` by digest + Trivy**). All green.

## B. Azure access (subscription admin, once)

- [ ] Region `eastus2`; resource group `rg-marketbell-staging` created.
- [ ] Providers registered: `Microsoft.App, DBforPostgreSQL, OperationalInsights, ContainerRegistry, KeyVault, Insights, Network, Storage, Consumption`.
- [ ] You get, **on that resource group only**: *Contributor* + *Role Based Access Control Administrator*
      constrained to AcrPull / Key Vault Secrets User / Storage Blob Data Contributor / Key Vault Secrets Officer
      (exact command in runbook §0). No subscription Owner.
- [ ] Your Entra object ID and UPN, and an alert e-mail, entered in `infra/azure/staging.bicepparam`.

## C. Deploy (you, ~45 min)

- [ ] `deploy.ps1 -Action preview -Environment staging -ResourceGroup rg-marketbell-staging` (what-if only).
- [ ] `deploy.ps1 -Action platform …` — **charges start** (≈ $1/day: PostgreSQL, ACR, alerts).
- [ ] Key Vault secrets `nebius-api-key`, `webhook-token`, `admin-token` (runbook §2; generated tokens, never typed into history).
- [ ] Database init (runbook §2 SQL): `pgaadauth_create_principal('mktbell-staging-app-id', …)`, schema `marketbell` owned by it.
- [ ] Image: CI push (or `az acr build`) → note `…/marketbell@sha256:…`.
- [ ] `deploy.ps1 -Action apps -Image <digest>` → type `staging` to confirm. Migrations run at start.

## D. Verify (you + this checklist, ~1 h)

- [ ] `python -m scripts.smoke https://<api fqdn> --wait-worker 300` with `MARKETBELL_SMOKE_WEBHOOK_TOKEN` set → 13/13.
- [ ] One real case: `model.call` events with `status:success` in Log Analytics; case visible under Today › Autonomous.
- [ ] Persistence: restart the API revision; the case is still there.
- [ ] Worker restart: restart the worker revision; new `worker.started`, heartbeat fresh within 2 min, no duplicate case.
- [ ] Alert test: deactivate the worker revision ~20 min → *monitoring stopped* e-mail → reactivate.
- [ ] Day 1: Cost Management shows no *Environment Management Hour* meter; spend in line with ≈ $1.2/day.

## E. Spend limits actually in force (staging parameters)

| Limit | Value | Enforced by |
|---|---|---|
| Model spend (reservations) | $0.25/day, $5/month | Application, before each call |
| Model calls | 60/day total, 20/day interactive, 12/event | Application |
| Interactive (visitor) live inference | Off | `allowInteractiveLive=false` |
| Azure budget | $25/month, e-mail at 50 % / 80 % / forecast 100 % | Azure (notification only — does not stop spend) |
| Logs | 0.25 GB/day cap | Log Analytics |

## F. Rollback

- [ ] Before each apps release, record the running image: `az containerapp show -g <rg> -n mktbell-staging-api --query "properties.template.containers[0].image" -o tsv`.
- [ ] Roll back: `deploy.ps1 -Action rollback -Environment staging -ResourceGroup <rg> -Image <that digest>`.
- [ ] Full stop (to stop charges): `az group delete -n rg-marketbell-staging` (Key Vault stays soft-deleted 30 days).
