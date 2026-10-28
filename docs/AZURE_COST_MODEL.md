# Azure + external API monthly cost model

**Status: estimated, not measured on Azure.** Nothing was deployed and no Azure invoice exists. Region
**East US 2**, currency **USD**, list (pay-as-you-go) prices from the **Azure Retail Prices API**
(`prices.azure.com`, retrieved 2026-10-19). Items marked **(A)** are assumptions or list prices that could not
be retrieved from the API in this session. The model is code: `python -m scripts.cost_model`
(output: `docs/evidence/COST_MODEL_OUTPUT.json`). Month = 730 h.

## Rates used

| Meter | Rate | Source |
|---|---|---|
| Container Apps vCPU active / idle | $0.000024 / $0.000003 per vCPU-s | Retail API |
| Container Apps memory active / idle | $0.000003 / $0.000003 per GiB-s | Retail API |
| Container Apps requests | $0.40 per million | Retail API |
| Container Apps free grant (per subscription, monthly) | 180,000 vCPU-s, 360,000 GiB-s, 2 M requests | [Billing doc](https://learn.microsoft.com/en-us/azure/container-apps/billing) |
| PostgreSQL Flexible B1ms / B2s | $0.017 / $0.068 per hour | Retail API |
| PostgreSQL storage | $0.115 per GB-month **(A)** | PostgreSQL storage row not returned by the API query; the same API returns $0.115/GB-month for the equivalent MySQL Flexible storage meter in East US 2 |
| PostgreSQL backup storage beyond the free allowance | $0.095 per GB-month | Retail API (allowance equal to provisioned storage **(A)**) |
| Container Registry Standard | $0.6666 per day **(A)** | List price; the Bicep uses Standard because it is the tier in the Azure free account (Basic $0.1666/day is not) |
| Key Vault operations | $0.03 per 10,000 | Retail API |
| Log Analytics (Analytics logs) | first 5 GB free, then $2.76/GB; retention to 31 days included **(A)** | Retail API |
| Log search alerts, 15-minute frequency | $0.50 per rule-month (8 rules) | Retail API |
| Private DNS zone | $0.50 per zone-month **(A)** | List price |
| Nemotron 3 Super on Nebius | $0.30 / $0.90 per M input / output tokens **(A)** | Third-party listing; verify on Nebius billing |
| Google News RSS, issuer pages | $0 (unofficial public feed; may rate-limit or change) | — |

**Environment management fee — resolved from Microsoft documentation, to be confirmed on the first bill.** The
Retail API lists *Environment Management Hour*, *Planned Maintenance Hour* and *Private Endpoint* meters at
$0.10/h (from 2026-09-01). Microsoft's documentation ties the management meter to Dedicated workload profiles
and premium features: *"You aren't billed any plan management charges unless you use a Dedicated workload
profile"* ([billing](https://learn.microsoft.com/en-us/azure/container-apps/billing)); *"Planned Maintenance is a
paid feature … billed through the Dedicated plan's Management (hour) meter … This meter also applies to other
premium features like private endpoint"* ([planned maintenance](https://learn.microsoft.com/en-us/azure/container-apps/planned-maintenance)).
The Bicep (verified in `main.bicep`) declares only the `Consumption` workload profile, no Dedicated profile, no
private endpoint on the environment and no maintenance configuration, so **no management fee is expected**.
Residual risk: the 2026-09 meter is not described on those pages. Day 1 of staging, check Cost Management for
this meter (one day would show ≈ $2.40). Fallback: a *Consumption-only (legacy)* environment, documented as
*"no cost associated with the Container Apps environment"* ([environment](https://learn.microsoft.com/en-us/azure/container-apps/environment)).

## Inference assumptions

2.5 calls per investigation (Research + Challenge + retries + 7/30/90-day follow-ups), 1,500 input and 600
output tokens per call → ≈ $0.001 per call. A recorded bounded run measured 20 calls for $0.0218
(`docs/AUDIT_USAGE_SUMMARY.json`), which agrees. App limits cap spend regardless: defaults $0.50/day,
$10/month, 60 calls/day, 12 calls per event, interactive share 20 calls/day.

## Monthly estimates

| Scenario | Sizing | Worker mode | Container Apps | PostgreSQL (compute + storage) | Fixed (ACR, alerts, KV, DNS, Blob) | Logs | **Azure** | Nebius | **Total** |
|---|---|---|---|---|---|---|---|---|---|
| 1. Hackathon demo (20 investigations/day, 60k requests) | api 0.25/0.5, worker 0.25/0.5, B1ms 32 GB | continuous | $10.76 (≤ $20.65 if the worker is never billed idle) | $16.09 | $9.58 | $0 | **$36.43** (≤ $46.32) | $1.50 | **$37.93** (≤ $47.82) |
| | | scheduled | $5.74 | $16.09 | $9.58 | $0 | **$31.41** | $1.50 | **$32.91** |
| 2. Personal use (30/day, 30k requests) | same | continuous | $10.53 (≤ $20.51) | $16.09 | $9.58 | $0 | **$36.20** (≤ $46.18) | $2.26 | **$38.46** (≤ $48.44) |
| | | scheduled | $5.79 | $16.09 | $9.58 | $0 | **$31.46** | $2.26 | **$33.72** |
| 3. Moderate, ~50 active users (150/day, 1.5 M requests) | api 0.5/1, worker 0.5/1 | continuous | $38.96 (≤ $52.76) | $16.09 | $9.58 | $0 | **$64.63** (≤ $78.43) | $11.29 | **$75.92** (≤ $89.72) |
| | | scheduled | $29.10 | $16.09 | $9.58 | $0 | **$54.77** | $11.29 | **$66.06** |
| 4. High news volume stress (1,000/day, 3 M requests) | api 0.5/1, worker 0.5/1 always active, **B2s** 64 GB | continuous | $57.30 | $57.00 | $9.58 | $8.28 | **$132.16** | $75.24 | **$207.40** |
| | | scheduled | $54.65 | $57.00 | $9.58 | $8.28 | **$129.51** | $75.24 | **$204.75** |

Scenario 4 needs the application limits raised (≈ $2.50/day of inference) and 4+ worker slots; at the default
limits MarketBell defers rather than spends.

## Architectures compared

| Architecture | Demo/personal monthly | Reliability | Verdict |
|---|---|---|---|
| A. Container Apps api + always-on worker + PostgreSQL B1ms | ≈ $36–48 | Seconds-level webhook response, 5-min news cadence, fast takeover | **Recommended** |
| B. Container Apps api + 5-minute scheduled worker job + PostgreSQL B1ms | ≈ $31–33 | ≤ 5-min latency for all work; long investigations span runs | Cheapest that still meets the requirement |
| C. Single container + SQLite on Azure Files | ≈ $20 | SQLite WAL/locking unsupported on SMB; unsafe during revision overlap | Rejected |
| D. App Service B1 (web) + WebJob + PostgreSQL | ≈ $13 (B1, (A)) + $16 DB + fixed ≈ $35+ | Always-on, but no per-second idle billing, weaker job model | Not better than A |

**Recommendation:** A for the public demo and production. Switch to B (`workerMode=scheduled`) if ≈ $5/month
matters more than minute-level latency. A new Azure account's PostgreSQL free offer (B1ms hours and 32 GB for
12 months, if eligible **(A)**) would remove ≈ $16/month.

## Spend controls

- **Application (enforced):** `MARKETBELL_DAILY_USD`, `MARKETBELL_MONTHLY_USD`, `MARKETBELL_DAILY_CALLS`,
  `MARKETBELL_EVENT_CALLS`, `MARKETBELL_INTERACTIVE_DAILY_CALLS`; reservations made before each call.
  Exhaustion defers work and shows deferred holding notices; it never silently drops them.
- **Azure (notification only):** resource-group budget (50 % / 80 % actual, 100 % forecast) and an alert at 80 %
  of the daily model limit. **Azure budgets do not stop spending.** The Log Analytics daily cap (0.25 GB) bounds
  telemetry cost; reaching it triggers the monitoring-stopped alert, by design.


## Configuration facts verified in `infra/azure/main.bicep` (2026-10-20)

| Question | Answer from the template |
|---|---|
| Workload profiles | One profile, `Consumption`; no Dedicated profile → no plan-management charge per Microsoft docs |
| Minimum replicas / idle | api 1/1, worker 1/1 (continuous mode). Both qualify for the idle rate only while < 0.01 vCPU and < 1 kB/s; the cost table shows both bounds |
| Continuous worker availability | Exactly one replica always running; leases make revision overlap safe |
| PostgreSQL | `Standard_B1ms` Burstable, PostgreSQL 16, 32 GB auto-grow, 7-day (staging) / 14-day (production) backups, no HA, no geo-backup; private access in a delegated subnet; Entra-only auth |
| Managed identity tokens | Fetched from the Container Apps identity endpoint per new database connection; cached until 5 minutes before expiry; existing connections are unaffected by token expiry (PostgreSQL authenticates at connect) |
| Key Vault | RBAC mode; the app identity has only *Key Vault Secrets User* on the vault; the operator needs *Key Vault Secrets Officer* to set secrets |
| Monitoring cost | 8 log-search alerts at 15-minute frequency = $4.00/month; Log Analytics capped at 0.25 GB/day (≈ 7.6 GB/month worst case; first 5 GB free) |
| Backups | Automated backups up to the provisioned size are included **(A)**; beyond that $0.095/GB-month |

## Provider operating limits

| Provider | Limit in MarketBell | Provider-side |
|---|---|---|
| Nebius Token Factory (Nemotron 3 Super) | Two calls per case (+1 retry each), ≤ 12 calls per event, 60/day, $0.50/day, $10/month; 4,096 max output tokens; 90 s read timeout; worker concurrency 2 | Account rate limits not published to us; HTTP 429 is retried after the retry delay |
| Google News RSS | One request per symbol per scan (default every 300 s, 4–12 symbols), 5 items each, 15 s timeout | Unofficial, unauthenticated feed: no SLA, may throttle or change format; failures are shown as source outages, never as "no news" |
| Issuer pages | PSX/VLO only, fetched with market context | Public websites |


## Update 2026-10-21: free account, hourly worker, Standard registry

`python -m scripts.cost_model` now also reports a `hourly` worker mode and an
`azure_total_free_account_first_12_months` figure (PostgreSQL B1ms + 32 GB and one Standard registry removed,
as included in a new Azure free account's 12-month services **(A)**: confirm eligibility on the first bill).

| Scenario | Worker | Azure, pay-as-you-go | Azure, free account (first 12 months) | Nebius |
|---|---|---|---|---|
| 1. Hackathon demo | continuous | $51.63 | **$15.28** | $1.50 |
| | every 5 min | $46.61 | **$10.26** | $1.50 |
| | hourly | $46.61 | **$10.26** | $1.50 |
| 2. Personal use | continuous | $51.40 | **$15.05** | $2.26 |
| | hourly | $46.66 | **$10.31** | $2.26 |

What remains on a free account is the always-on API replica (~$5.74; the free Container Apps grant covers the
worker), 8 alert rules ($4.00) and small fixed items. Hourly and 5-minute schedules cost the same at demo
scale because worker compute sits inside the free grant; hourly only helps at higher volume (scenario 3: $17.28
vs $29.10 compute). Choose it with `workerMode='scheduled'`, `workerScheduleMinutes=60`; heartbeat age, news
cadence and the stopped/stale/backlog alert windows follow automatically.

Nebius token savings now in code (not yet reflected in `CALLS_PER_INVESTIGATION`, so the Nebius column is
conservative): commentary and unknown publishers are logged, not investigated; routine filings (Form 4 and
similar) are logged; the same story from several publishers is read once; the second review is skipped when
the first finds routine news on a stock the investor neither owns nor watches. Daily call and dollar caps are
unchanged.
