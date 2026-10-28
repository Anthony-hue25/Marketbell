# Performance benchmarks

Measured local observations, not production SLAs. Raw artifacts: AUDIT_RUNTIME_BENCHMARK.json and AUDIT_LOAD_RESULTS.json. The Windows launcher flaw invalidated earlier process/CPU measurements; the retained runtime artifact verifies exact child identity.

| HTTP path | Samples | p50 ms | p95 ms | Max ms |
|---|---:|---:|---:|---:|
| `/` | 30 | 13.92 | 32.64 | 35.78 |
| `/healthz` | 60 | 28.61 | 52.20 | 56.59 |
| `/api/state` | 40 | 154.17 | 607.73 | 2910.20 |
| `/api/impact?days=30&dataset=workspace` | 40 | 68.58 | 108.19 | 122.91 |
| `/api/universe` | 40 | 62.69 | 87.70 | 193.21 |

Cold process start: 6157 ms. The initial state maximum includes cold initialization; report that separately rather than hiding it. API timings exclude browser rendering and network distances beyond localhost.

100-event burst, with 100 duplicate arrivals: 100 unique jobs; {'completed': 100}; total 48.06 seconds. Mocked event resolution p50 404.93 / p95 650.01 ms. Contended enqueue p50 70.37 / p95 176.65 ms. Indexed Impact query p50 18.65 / p95 38.65 ms (40 queries). These exclude real inference latency. One- and ten-event samples are retained as observations; their tail estimates are not reliable capacity evidence.

Real-model pipeline observations (too few homogeneous samples for meaningful p95):
- Controlled event and mid-processing process restart: 16483 ms from event persistence to resolution; includes restart where specified. Research/Challenge: 4469 ms, 3094 ms.
- Authentic fetched RSS item through recurring detector: 10978 ms from event persistence to resolution; includes restart where specified. Research/Challenge: 5125 ms, 4375 ms.

Idle worker CPU: 2.90% of one core over one 15.1-second window. Process RSS/peak: 14.4/79.2 MiB. This short test is not a multi-day endurance or hosted-memory guarantee.

Optimizations: four bounded concurrent news fetches, persisted deferred notices, pending-job and tenant/audit indexes, indexed Impact projections, bounded raw histories, on-demand older evidence, stripped request transcripts, shared public inference with independent per-user authority, ten-second durable worker heartbeat instead of an idle write every second. Transactional authority checks were retained. No claimed baseline speedup is inferred from incomparable workloads.

Evidence-based local budgets for this release: Impact query p95 <25 ms at 100 cases; warmed Impact HTTP p95 <150 ms; warmed workspace state p95 <300 ms; mocked case resolution p95 <500 ms; idle CPU <5% of one core. Cold initial load and real model calls have separately visible pending states and no invented latency guarantee.

Browser rendering/navigation observations and viewport checks are retained in BROWSER_AUDIT.json. Desktop 1440px, tablet 768px and mobile 390px were inspected. Independent first-time-user comprehension and cross-browser compatibility are not certified.
