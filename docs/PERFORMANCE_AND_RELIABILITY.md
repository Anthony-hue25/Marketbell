# Performance and reliability evidence

**Test level: local, production-like containers — not Azure.** The production image was built and run as two
containers (API in `staging` mode behind a simulated ingress with `X-Forwarded-For`; worker in `test` mode so it
could use loopback fault-injection endpoints) against PostgreSQL 16 on the same host, with mocked Nemotron and
a mocked Google-News-shaped RSS feed (`infra/local/`). Containers ran read-only, `cap_drop ALL`,
`no-new-privileges`, uid 10001. Raw results: `docs/evidence/LOCAL_CONTAINER_FAULT_TESTS.json`,
`LOCAL_CONTAINER_BENCHMARKS.json`, `LOCAL_CONTAINER_SMOKE.json`. Real Nebius and Google News were **unreachable**
from this environment (egress policy), so live integration is a separate, bounded check
(`scripts/live_bounded_check.py`) for the owner's machine.

## Reliability tests (all passed)

| ID | Test | Result |
|---|---|---|
| T1 | Browser closed: a new headline appears in the feed; no client connected | Notice → triage → Research → Challenge → policy → `needs` escalation persisted **55 s** after publication (dominated by the 60 s test poll interval); visible to a returning visitor; worker healthy flag true. |
| T2 | Worker **SIGKILL** mid-Research (25 s model latency) | Restarted worker took the role after lease expiry, requeued the job, completed it: 1 event, `RESEARCH:reserved` (interrupted) + 1 successful Research + 1 Challenge; recovery 96 s including 50 s of model time. Stale reservation now reconciled as `abandoned`. |
| T3 | Graceful **SIGTERM** mid-investigation | Stopped in 5.3 s; job returned to `queued` with **attempts 0** (no retry consumed); roles released; next worker completed it in 53 s. |
| T4 | Nebius **HTTP 503** (both attempts) | Event `failed/NEBIUS_HTTP_503`, journaled; automatically retried and completed after recovery (31.8 s with a 20 s test retry delay). |
| T5 | News source **outage** (HTTP 500, 75 s) | Monitor `active:false`, *Sources unavailable; retrying*, last success preserved, outage report recorded; item published during the outage investigated 6 s after recovery; personal copies created for subscribed workspaces. |
| T6 | **Database outage** (45 s) | API `/healthz`, `/api/state`, webhook → 503 `DATABASE_UNAVAILABLE`; worker stayed up (health 503 `database:false`); both recovered with no restart; next webhook completed. |
| T7 | Duplicate / syndicated articles; duplicate and revised webhook | Same headline across scans and two publishers → 1 investigation; duplicate webhook → `duplicate_ignored`, same job; revised content → separate job (`revision_of`), original unchanged. |
| T8 | **Two workers**, then kill the active one | Second worker standby; 8/8 cases completed after failover; **0 duplicate successful model stages**; 17 calls for 8 cases (16 + 1 interrupted). |
| T9 | **Backlog after downtime** (20 webhooks + 2 news items during 150 s downtime) | All 20 drained in 45.9 s after start; both news items investigated; all jobs `completed`. |
| T10 | Delayed and stale articles | 3-day-old newly indexed item investigated (evidence keeps provider `published_at` and our `retrieved_at`); 8-day-old item never screened. |
| T13 | Logical backup and restore | `pg_dump`/`pg_restore` to a new database (0.7 s); `Store.verify()` on the restore passed (16,053 records, identical head hash). |
| T14 | Scheduled-job worker mode | One `--once` pass processed 3 queued cases in 17.5 s and released its roles; a concurrent run reports `standby`. |
| — | Full platform restart (host processes killed: Docker, PostgreSQL) | All state intact (16,053 audit rows, 381 jobs); smoke 13/13 passed afterwards. |
| — | Concurrent cold start of API and worker on an empty database | Found a real defect (DDL serialization failure crashed the API); fixed (READ COMMITTED + advisory lock; API start-up DB retry). Both now ready in 2.3 s. |

Every accepted event reached a durable final state (`completed`, `failed` with a code, or a waiting state such as
`needs`/`deferred`) in every test. Requests refused during a database outage were refused explicitly (503),
never accepted and lost.

## Benchmarks (measured, mocked inference)

| Workload | Model latency/stage | Slots | Total | Throughput | Queue delay p50 / max | End-to-end max | Worker CPU max | Worker RSS |
|---|---|---|---|---|---|---|---|---|
| 10 events | 0 s | 1 | 5.2 s | 116 / min | 1.1 / 1.5 s | 1.6 s | 16 % | 69 MiB |
| 10 events | 2 s | 1 | 42.5 s | 14 / min | 19 / 38 s | 42 s | 6 % | 69 MiB |
| 100 events | 0 s | 1 | 14.0 s | 429 / min | 5 / 10 s | 10 s | 54 % | 71 MiB |
| 100 events | 2 s | 1 | 413 s | 14.5 / min | 204 / 407 s | 411 s | 12 % | 73 MiB |
| 100 events | 2 s | **4** | **112 s** | **54 / min** | 53 / 104 s | 108 s | 68 % | 87 MiB |

Per-case processing overhead without model time is ~0.1 s. Throughput is bound by model latency × 2 stages;
parallel slots (added in this assurance) scale it nearly linearly (3.7× with 4 slots) with no duplicates.
Recorded real Nemotron stages took 2.0–3.3 s on small synthetic inputs (12 calls, `docs/AUDIT_AI_EVALUATION.json`)
and a real end-to-end webhook case 28.7 s (`docs/CONTINUOUS_ACCEPTANCE_PROOF.json`).

### API latency (gunicorn, 1 process × 8 threads, PostgreSQL; ~4.8k documents, ~10k audit rows)

| Endpoint | Concurrent users (continuous polling) | Requests/s | p50 | p95 | p99 | Errors |
|---|---|---|---|---|---|---|
| `/api/state` | 1 | 53 | 18 ms | 24 ms | 31 ms | 0 |
| `/api/state` | 10 | 74 | 132 ms | 200 ms | 226 ms | 0 |
| `/api/state` | 25 | 72 | 347 ms | 463 ms | 515 ms | 0 |
| `/api/state` during 12 long investigations | 10 / 25 | 77 / 77 | 125 / 326 ms | 199 / 420 ms | 233 / 512 ms | 0 |
| `/api/impact` (30 days) | 10 | 116 | 85 ms | 127 ms | 153 ms | 0 |
| `/` (static) | 10 | 778 | 12 ms | 26 ms | 35 ms | 0 |

The API saturates at ~75 state requests/s per process (CPU-bound JSON assembly, ~13 ms each). The UI polls every
30 s when idle, so one replica supports several hundred concurrently open browsers. Long investigations do not
affect API latency (they run in the worker).

### Projected cloud performance (not measured)

Container Apps 0.25 vCPU is roughly a quarter of the measured core, so expect ~15–20 state requests/s per API
replica at 0.25 vCPU (raise to 0.5 vCPU for the moderate scenario). Database round-trips will add ~1–3 ms within a
region. These are projections, not measurements.

## Bottlenecks fixed

| Bottleneck | Fix |
|---|---|
| Serial investigations (1 case at a time) | `MARKETBELL_WORKER_CONCURRENCY` parallel slots |
| One new news investigation per scan | `MARKETBELL_SCAN_MAX_NEW` (default 1 for cost safety) |
| Webhook limit fixed at 20/min | `MARKETBELL_WEBHOOK_PER_MINUTE` |

## Reliability design (implemented)

- Liveness `/livez`, readiness `/healthz` (DB + schema) for the API; `/livez` + `/healthz` on the worker's
  internal port 8081; startup probes for both.
- Durable queue in PostgreSQL; atomic fenced claims; per-job leases renewed every 15 s; singleton role leases
  (worker, monitor) with standby replicas; recovery of expired leases every 30 s; retry with delay for transient
  provider codes; `RETRY_LIMIT` after 3 attempts (explicit failure, never silent).
- Duplicate suppression: deterministic job ids, story identity, notice cursors, revision ids.
- Heartbeats every 10 s in the database and a structured `worker.heartbeat` event every 60 s (queue depth,
  oldest pending, news freshness, model failures, spend, deferred holdings, awaiting user).
- Rollback: immutable image digests; previous digest recorded before each production release.
