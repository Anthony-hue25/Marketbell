# Azure architecture decision

**Decision:** Azure Container Apps (Consumption profile) running **one image as two independent workloads**:
an `api` container app (website + JSON API, external HTTPS ingress) and a `worker` container app (no ingress,
exactly one always-on replica), backed by **Azure Database for PostgreSQL Flexible Server (Burstable B1ms)**,
with Key Vault, ACR Basic, Log Analytics, log-search alerts, a daily `audit-anchor` Container Apps Job and
write-once Blob anchors. A scheduled-job worker (`workerMode=scheduled`) is the lower-cost alternative.

The starting hypothesis (Container Apps, separate frontend/API and background workloads) was **validated
with one change**: the frontend is not split into Static Web Apps, because the UI is three static files served
same-origin by the API with a strict CSP; a second origin would add CORS, cookie and deployment surface for
no measurable benefit (static files: p50 11.8 ms, 778 req/s measured locally).

## What the code required (assessed before choosing)

| Fact | Consequence |
|---|---|
| Plain Python WSGI app + threads; long Nemotron calls (2–30 s each, two per case) | Must never run in request threads → separate worker workload; API stays responsive (measured: p95 unchanged during 12 long investigations). |
| All state in a relational store with JSON documents, triggers and transactions | Needs a real database with transactions and triggers. PostgreSQL maps directly (dialect layer in `backend/db.py`; full suite passes on PG 16). |
| SQLite WAL needs local shared memory | SQLite on Azure Files (SMB) is unsafe for WAL and multi-writer → rejected for production. |
| In-memory rate limiter in the API | API pinned to one replica (sufficient: ~75 state req/s per replica; UI polls every 30 s). Spend limits are database-enforced, so they hold across replicas. |
| Background monitoring must survive with no browser | Worker is a separate app with min=max=1 replica; heartbeats, leases and durable queue in the DB. |

## Background processing options evaluated

| | A. Continuous worker app (chosen) | B. Scheduled job, every 5 min (`workerMode=scheduled`) | C. Single container (API + worker) on SQLite + Azure Files |
|---|---|---|---|
| Always-on monitoring | Yes, 5-min news cadence, webhooks start within seconds | Yes, ≤5-min latency for webhooks and news | Yes |
| Long investigations | Natural; leases renewed while running | Bounded to 240 s per run; unfinished work continues next run | Natural |
| Restart / takeover | Lease expiry ≤ 60 s + job lease 90 s (measured recovery 96 s after SIGKILL incl. 50 s of model time) | Next run | Single point; SQLite locking risk on SMB |
| Revision rollover overlap | Safe: newcomer is standby until the lease frees | Safe: overlapping runs → `standby` | Unsafe (two writers on one SMB file) |
| Monthly Azure cost (demo) | ≈ $36 (up to ≈ $46 if never billed idle) | ≈ $31 | ≈ $20 but no durable/safe persistence |
| Verdict | **Recommended** | Valid cost-saving option | Rejected for production |

Kubernetes (AKS), VMs, Functions and microservices were not adopted: Container Apps already gives
revisions, probes, managed identity, Key Vault references and jobs; Functions' consumption timeouts and
Python cold starts do not fit long Nemotron investigations, and the worker needs no event-trigger fan-out.

## How always-on monitoring is satisfied while minimizing compute

- The worker replica is the only always-on compute besides one API replica; both are 0.25 vCPU / 0.5 GiB
  (worker measured 64–90 MiB RSS, CPU mostly waiting on the provider). Container Apps bills a replica at the
  *idle* rate when it is at its minimum count and uses < 0.01 vCPU with < 1 kB/s network, which the sleeping
  worker meets between scans. The cost model shows both the idle-eligible estimate and the never-idle bound.
- News is polled every 300 s (configurable 60–3600). Investigations run only for new, entity-matched,
  budget-permitted notices; holdings first. `MARKETBELL_WORKER_CONCURRENCY` (default 2) runs parallel
  investigation slots inside the single active worker (measured 3.7× throughput with 4 slots).
- A responding website is not treated as proof of monitoring: `/api/state` reports the worker heartbeat and
  marks the monitor stale after 2.5 × the poll interval; the *monitoring stopped* alert fires when no
  `worker.heartbeat` event arrives for 15 minutes.

## Topology, dependencies and failure boundaries

```
Internet ──HTTPS──> [ACA ingress / Envoy, TLS] ──> api (1 replica) ──┐
                                                                     ├──> PostgreSQL Flexible (private VNet, Entra auth)
                      worker (1 replica, no ingress) ────────────────┤
                         ├──HTTPS──> Nebius Token Factory (Nemotron)  │
                         └──HTTPS──> Google News RSS / issuer pages    │
                      audit-anchor job (daily) ───────────────────────┴──> Blob (write-once anchors)
All apps ──managed identity──> Key Vault (secrets), ACR (pull), PostgreSQL (token), Blob (anchors)
stdout JSON events ──> Log Analytics ──> 8 log-search alerts ──> email action group;  RG budget ──> email
```

| Failure | Effect | Recovery (tested locally) |
|---|---|---|
| API replica down | Website unavailable; monitoring unaffected | Platform restart; stateless |
| Worker killed | Monitoring pauses | Restart; expired job lease requeued (T2: completed, 1 case, no duplicate decision) |
| Worker SIGTERM (rollover) | Current job returned to queue without consuming a retry | New replica takes over immediately (T3) |
| Two workers (overlap) | Second stays standby | Takeover after lease expiry (T8: 8/8 completed, 0 duplicate model stages) |
| Database outage | API `/healthz` 503, webhooks 503 (sender retries), worker keeps running | Automatic reconnect (T6: recovered without restarts) |
| Nebius 5xx / timeout | Case `failed` with code, journaled | Retried after `MARKETBELL_RETRY_DELAY_SECONDS` (T4) |
| News outage | Monitor `active:false`, *Sources unavailable* | Items published during the outage investigated after recovery (T5) |
| Budget exhausted | No new investigations; holding notices deferred visibly | Next day / budget raise |

## Scaling rules

| Workload | Rule | Why |
|---|---|---|
| api | min 1, max 1 | Coherent in-memory rate limits; capacity ≫ demo load. To scale out, move rate limits to the database first. |
| worker | min 1, max 1; scale *up* via `MARKETBELL_WORKER_CONCURRENCY` and CPU size | Singleton monitoring role by design; extra replicas would only be standby. |
| anchor job | daily cron | Cheap integrity proof. |
| PostgreSQL | B1ms → B2s for the stress scenario | Single-writer advisory lock keeps writes serialized; measured write volume is low. |

## Networking

`networkMode=vnet` (default): Container Apps environment in a delegated /23 subnet, PostgreSQL with private
access in a delegated /28 subnet and private DNS zone; no public database endpoint. Outbound to Nebius and news
uses platform egress (no NAT gateway cost). `networkMode=public` exists for constrained subscriptions; it
exposes the database endpoint to Azure services (TLS + Entra-only auth still required) and is weaker.
Monitoring/admin endpoints: `/api/diagnostics` requires a bearer admin token (disabled when unset); the worker
health port has no ingress; there is no other administrative surface.

## Identity and secrets

One user-assigned managed identity: AcrPull (registry), Key Vault Secrets User (vault), Storage Blob Data
Contributor (anchor container only), PostgreSQL Entra role (created once by the database admin). PostgreSQL
password authentication is **disabled**; the app obtains Entra tokens from the Container Apps identity
endpoint (`MARKETBELL_DATABASE_AUTH=managed-identity`, no SDK dependency). Nebius key only in the worker; webhook
and admin tokens only in the API. CI/CD uses GitHub OIDC federation (no stored Azure secret).
