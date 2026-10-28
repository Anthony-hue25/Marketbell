"""Generate audit documentation from measured artifacts, preserving their limitations."""
import json,statistics,hashlib,importlib.metadata as md,subprocess,sqlite3
from pathlib import Path
from datetime import datetime
root=Path('.');docs=root/'docs'
load=lambda n:json.loads((docs/(n+'.json')).read_text(encoding='utf-8'))
runtime=load('AUDIT_RUNTIME_BENCHMARK');loadtest=load('AUDIT_LOAD_RESULTS');ai=load('AUDIT_AI_EVALUATION');autonomy=load('AUDIT_AUTONOMY_PROOF')
assert autonomy['result']=='PASS' and autonomy['results'][0].get('actual_process_identity_checked')
traces=[o for c in ai['results'] for o in c['operations'] if o.get('output')]+ai['conditional_risk_retest']['operations']+[ai['preference'],ai['list_preference']]+[t for c in autonomy['results'] for t in c['traces']]
usage={'input_tokens':sum((t.get('usage') or {}).get('prompt_tokens',0) for t in traces),'completion_tokens':sum((t.get('usage') or {}).get('completion_tokens',0) for t in traces)}
usage['estimated_usd']=(usage['input_tokens']*.30+usage['completion_tokens']*.90)/1e6
usage['successful_returned_calls']=len(traces);usage['note']='Bounded audit workload only; interrupted in-flight requests and other historical/local monitoring calls are excluded. Estimates are assumptions, not a Nebius invoice.'
(docs/'AUDIT_USAGE_SUMMARY.json').write_text(json.dumps(usage,indent=2),encoding='utf-8')
# Enrich runtime proof with observed event durations; read-only scratch databases only.
for entry in autonomy['results']:
 for file in (root/'work'/'audit').glob('live-*/marketbell.sqlite3'):
  try:
   with sqlite3.connect('file:'+file.resolve().as_posix()+'?mode=ro',uri=True) as db:
    row=db.execute("SELECT body FROM documents WHERE tenant='monitor-public' AND kind='event' AND id=?",(entry['event'],)).fetchone()
   if row:
    event=json.loads(row[0]);entry['event_created']=event['created'];entry['resolved_at']=event.get('resolved_at');entry['policy_duration_ms']=event.get('policy_duration_ms')
    entry['resolution_wall_ms']=(datetime.fromisoformat(event['resolved_at'])-datetime.fromisoformat(event['created'])).total_seconds()*1000
    break
  except (sqlite3.Error,KeyError):pass
(docs/'AUDIT_AUTONOMY_PROOF.json').write_text(json.dumps(autonomy,indent=2),encoding='utf-8')
feature='''Implemented: immutable autonomy lifecycle; canonical headline screening; server-side period aggregates and bounded case pagination; Today workload metrics; Impact; prospective 7/30/90-calendar-day reviews; append-only outcome assessments; attributed missed-event/usefulness reports; Learning Journal; note and list-rule approval/edit/pause/revoke; manual/CSV holdings and watchlist onboarding; pins; explicit mandates; a three-tier universe; bounded rotating discovery from 32 curated companies; independent comparative investigations and non-trading portfolio proposals. No recommendation approval can change holdings. Manual holding edits require a separate reviewed entry confirmation and do not verify external transactions.

Real operation uses news and real Nemotron on Nebius. Verified Replay uses authenticated preserved model outputs. The isolated illustrative dataset contains 120 deterministic historical cases across 60 days, generated through Engine policy, rule, persistence and verification paths. Its simulated model outputs, approvals, chronology and retrospective outcomes are labelled. It is never included in genuine counters. No historical operating data or investment returns were invented as genuine evidence.'''
findings='''| Priority | Finding / root cause | Correction and evidence |
|---|---|---|
| P0 | Narrow standing-rule position limits used the originally proposed allocation after a portfolio edit. | Compare current and proposed positions; current holdings are not added twice to existing sector exposure. Regression rejects automatic action at 4% under a 3% rule. |
| P1 | Deferred RSS notices could disappear from bounded feeds before queue capacity/budget returned. | Persist pending notices before dispatch, with explicit deferred counts. Regression empties the feed and still queues the retained notice. |
| P1 | Database exceptions in worker recovery/error reporting could terminate a background thread. | Resilient outer loops, retained jobs, bounded retries, and independent monitor heartbeat. Fault-injection regression and actual process restart proof. |
| P1 | Personal review originally depended on unexpired browser sessions. | Select durable opted-in portfolio records; a no-session regression proves review reuse. Browser sessions are access credentials, not worker lifecycle. |
| P1 | The Windows audit harness could terminate a venv launcher while retaining its interpreter child. | Run the base interpreter with the project's isolated dependencies, require matching process ID and unique instance nonce, and verify a different PID after restart. Earlier affected restart/CPU claims were discarded. |
| P1 | A material risk could disappear behind a note-preparation policy block. | Keep material risk visible for judgement; acknowledgement grants no note/order authority. Regression covers an overweight holding. |
| P2 | Successful empty polls were conflated with failed sources; timestamps could imply new items when only duplicates returned. | Distinguish failures, canonical unique ingestion, last successful check and last newly received item. Empty coverage remains explicitly incomplete. |
| P2 | Headline identity included the ticker and could inflate screened counts across companies or schema changes. | Canonical story identity and migration v7 preserve raw receipts while deduplicating shared counters and source cursors. |
| P2 | No dollar reservations, per-event cap, or failed-call telemetry. | Daily/monthly estimated-spend ceilings, event/daily attempt limits, bounded retries, and safe invocation metadata. Reservations remain charged conservatively after interruption. |
| P2 | Raw requests and complete histories were repeatedly sent to the browser. | Strip private model requests, prioritize open decisions, bound snapshots, index aggregation, and fetch older evidence on demand. |
| P2 | Direct dependencies had current OSV advisories. | Requests 2.34.2 and python-dotenv 1.2.4 installed and retested; tzdata updated to 2026.5. Advisory reachability was narrower than a blanket application compromise. |
| P2 | Rule revision capture occurred after mutation. | Preserve the previous rule before edits; immutable journal records retain authority changes. |
'''
write=lambda name,text:(docs/name).write_text(text,encoding='utf-8')
write('FINAL_ENGINEERING_AUDIT.md',f'''# MarketBell final engineering audit — 2026-10-14 (America/Port_of_Spain)

## Actual implementation

{feature}

## Audit scope and preservation

Baseline commit: `fbda7ffa3a21f18a631ce1b5f080dedefe948861`. Existing changes and user state were preserved. The pre-audit patch and SQLite backup remain under ignored `work/audit`. Reference repositories were read-only. See BASELINE_AUDIT.md. This audit includes the subsequent Autonomy/Learning/Impact and Dynamic Investment Universe addenda; the unrelated Adaptive Stay attachment was not implemented.

## Findings fixed

{findings}

## Verification and limits

Final automated test results are recorded in AUDIT_TEST_RESULTS.json, including counts and output hash. Separate real-model evidence: AUDIT_AUTONOMY_PROOF.json and AUDIT_AI_EVALUATION.json. Load: 1, 10 and 100 distinct events plus equal duplicate arrivals; all completed exactly once with explicitly mocked inference. Backup/readback and immutable audit checks passed. An actual process was stopped after Research persisted, then a different verified process finished the same job without duplicating its outcome. Authentic RSS was fetched separately and passed through the normal detector while no frontend session existed.

The provider produced {len(traces)} successful returned calls in the selected audited evidence set. This is not a claim that all model outputs were equally good: the initial synthetic refinery-risk evaluation exposed provenance/materiality confusion; its original output remains preserved, followed by a conditional-scenario retest. Six original pairs passed strict structure/quotation checks, not a broad investment-accuracy certification.

Desktop/mobile findings, exact timings, usage assumptions and deployment gates are detailed in PERFORMANCE_BENCHMARKS.md, AI_EVALUATION_REPORT.md, AUTONOMY_VERIFICATION.md, COST_MODEL.md and RELEASE_READINESS.md.

## Remaining priorities

- P0: no known unresolved authority/security defect in the audited local scope. This is not a guarantee against unknown defects.
- P1 release gates: production container/TLS/supervision have not been independently exercised on a working Docker engine; deployment credentials, host and publication rights remain unapproved. Do not declare a public production release.
- P2: curated 32-company coverage; headline-only matching and bounded lookback can miss events or updates; no comprehensive commercial news entitlement is established; no mature genuine 7/30/90-day quality record; no validated total-return benchmark; anonymous workspace credentials lack registered-account recovery; synchronous personalization is bounded by demo scale, not proven at 2,000 active users.
- P3: independent first-time-user comprehension, broader browser/accessibility testing and longer multi-day endurance remain unverified. No fabricated automation-rate target or first-user success claim is made.

## Hackathon assessment

Best Apps and Agents remains the strongest fit. Nemotron performs qualitative Research, independent blind Challenge, preference interpretation and subsequent evidence assessment; deterministic code cannot substitute for that judgement. The useful actions are evidence-backed research notes and delegated list changes, with verified persistence and explicit authority. Prospective accountability and honest failures strengthen the story. Competitiveness is credible, but winning readiness still depends on a reliable hosted demo, concise video, licensed public source/data and an independently understandable teach-once experience.

Official requirements checked: [hackathon overview](https://nebiusglobalaihackathon.devpost.com/) and [official rules](https://nebiusglobalaihackathon.devpost.com/rules). The overview requires a working project using NVIDIA open models on Nebius, a working demo/test build, a public repository with a visible open-source license, and a public video of at most three minutes. These publication steps were not performed.
''')
rows='\n'.join(f"| `{k}` | {v['samples']} | {v['p50_ms']:.2f} | {v['p95_ms']:.2f} | {v['max_ms']:.2f} |" for k,v in runtime['endpoints'].items())
b=loadtest['bursts'][-1]
obs='\n'.join(f"- {r['test']}: {r.get('resolution_wall_ms',0):.0f} ms from event persistence to resolution; includes restart where specified. Research/Challenge: "+', '.join(str(t['duration_ms'])+' ms' for t in r['traces'])+'.' for r in autonomy['results'])
write('PERFORMANCE_BENCHMARKS.md',f'''# Performance benchmarks

Measured local observations, not production SLAs. Raw artifacts: AUDIT_RUNTIME_BENCHMARK.json and AUDIT_LOAD_RESULTS.json. The Windows launcher flaw invalidated earlier process/CPU measurements; the retained runtime artifact verifies exact child identity.

| HTTP path | Samples | p50 ms | p95 ms | Max ms |
|---|---:|---:|---:|---:|
{rows}

Cold process start: {runtime['startup_ms']:.0f} ms. The initial state maximum includes cold initialization; report that separately rather than hiding it. API timings exclude browser rendering and network distances beyond localhost.

100-event burst, with 100 duplicate arrivals: {b['unique_jobs']} unique jobs; {b['jobs']}; total {b['wall_seconds']:.2f} seconds. Mocked event resolution p50 {b['resolution_mocked']['p50_ms']:.2f} / p95 {b['resolution_mocked']['p95_ms']:.2f} ms. Contended enqueue p50 {b['enqueue']['p50_ms']:.2f} / p95 {b['enqueue']['p95_ms']:.2f} ms. Indexed Impact query p50 {b['impact_query']['p50_ms']:.2f} / p95 {b['impact_query']['p95_ms']:.2f} ms (40 queries). These exclude real inference latency. One- and ten-event samples are retained as observations; their tail estimates are not reliable capacity evidence.

Real-model pipeline observations (too few homogeneous samples for meaningful p95):
{obs}

Idle worker CPU: {runtime['idle_cpu_percent_one_core']:.2f}% of one core over one {runtime['idle_window_seconds']:.1f}-second window. Process RSS/peak: {runtime['last_diagnostics']['resources'].get('rss_bytes',0)/1048576:.1f}/{runtime['last_diagnostics']['resources'].get('peak_rss_bytes',0)/1048576:.1f} MiB. This short test is not a multi-day endurance or hosted-memory guarantee.

Optimizations: four bounded concurrent news fetches, persisted deferred notices, pending-job and tenant/audit indexes, indexed Impact projections, bounded raw histories, on-demand older evidence, stripped request transcripts, shared public inference with independent per-user authority, ten-second durable worker heartbeat instead of an idle write every second. Transactional authority checks were retained. No claimed baseline speedup is inferred from incomparable workloads.

Evidence-based local budgets for this release: Impact query p95 <25 ms at 100 cases; warmed Impact HTTP p95 <150 ms; warmed workspace state p95 <300 ms; mocked case resolution p95 <500 ms; idle CPU <5% of one core. Cold initial load and real model calls have separately visible pending states and no invented latency guarantee.

Browser rendering/navigation observations and viewport checks are retained in BROWSER_AUDIT.json. Desktop 1440px, tablet 768px and mobile 390px were inspected. Independent first-time-user comprehension and cross-browser compatibility are not certified.
''')
write('AI_EVALUATION_REPORT.md',f'''# AI evaluation

Real provider: Nebius Token Factory. Model: `nvidia/nemotron-3-super-120b-a12b`. No silent keyword or alternate-model fallback. Safe invocation evidence includes provider IDs, timestamps, stage, durations, usage and validated public JSON. Raw model requests are removed from UI responses; hidden reasoning and credentials are never collected.

AUDIT_AI_EVALUATION.json contains six explicitly synthetic labelled scenarios: ambiguous plans, contradictory guidance reports, an irrelevant company mention, a misleading return headline, malicious source instructions and a potential holding risk. Each used separate Research and Challenge calls; Challenge initially receives the identical evidence package without Research's conclusion. Six pairs passed schema and exact-quote grounding checks. Both note-rule and list-rule interpretations succeeded as proposals, not authority.

Qualitative review: ambiguity and contradictions were retained; the irrelevant item produced no action; the speculative 40% claim was not accepted as verified data; source-injected instructions did not authorize trading. The initial refinery-risk case confounded materiality with synthetic provenance: Challenge dismissed it as not a real-world event. This is a documented evaluation weakness, not a clean six-of-six quality score. A separately retained conditional-hypothetical retest recognized Risk and retained unknown duration/cost/earnings effects. It did not change production prompts to force expected agreement.

Authentic market-source processing is separately recorded in AUDIT_AUTONOMY_PROOF.json. A live retrieved headline was processed by real Research/Challenge and held when corroboration was insufficient. A cautious hold is a valid result, not a failed sales demonstration.

Original decisions remain immutable; later 7/30/90-day assessments are append-only, exact-cited and separate from operational correctness, price observation and user feedback. Future genuine horizons have not elapsed. Demonstration supported/contradicted/mixed outcomes are deterministic, explicitly illustrative and isolated. No recommendation accuracy, alpha or financial usefulness rate is established.

Estimated measured token usage for the selected evidence set: {usage['input_tokens']} input and {usage['completion_tokens']} completion tokens across {len(traces)} returned successes. Interrupted requests may have consumed additional tokens. See COST_MODEL.md.
''')
write('AUTONOMY_VERIFICATION.md',f'''# Autonomy verification

Result: **PASS for the tested local background-worker paths**. This is not an uninterrupted 24/7 uptime or source-coverage claim.

The normal worker accepts authenticated controlled delivery through `/api/webhook`; detection, deduplication, triage, Research, blind Challenge, policy resolution and persistence remain in the application path. The controlled input is a preserved real issuer announcement labelled as controlled delivery, not naturally breaking news.

Verified process IDs before/after interruption: {autonomy['results'][0]['process_before']} → {autonomy['results'][0]['process_after']}. The actual interpreter process identity and unique instance nonce were checked. Research was persisted before termination and its trace hash was unchanged on resume. The job required two attempts and produced one final outcome. Duplicate delivery reused the job identity. There were zero browser sessions during processing; no frontend was opened on the isolated test service. The normal state API displayed the persisted result afterward.

A separate, authentic PSX headline was fetched from the configured live public RSS source and fed unmodified through `scan_watch`; it completed real inference while the exchange was closed. Exact source/retrieval/publication evidence is in AUDIT_AUTONOMY_PROOF.json. Public RSS connectivity is not proof of commercial redistribution rights or exhaustive coverage.

Temporary network failures, successful empty polls, durable budget deferral, duplicate arrivals, partial-stage failures, immutable history, portfolio changes and restart recovery have automated regressions. Large bursts use mocked inference and are labelled as such. Personal review now follows durable opted-in portfolio records rather than browser-login state. A no-session regression verifies this distinction.

The scheduler maintains 7/30/90-calendar-day review jobs and uses the exchange calendar for appropriate completed-session price observations. It does not wait for market close before investigating news. Neither direct SEC polling nor intraday price polling exists. News price claims remain attributed assertions.

Operational requirements: keep the backend process/service running, persist SQLite and its volume, configure source access and dedicated deployment inference credentials, monitor the restricted diagnostics endpoint, and verify process supervision on the production host. Local laptop sleep or power loss prevents monitoring; restart backfill is bounded by source lookback and cannot guarantee every missed event.
''')
percall=usage['estimated_usd']/max(1,len(traces))
write('COST_MODEL.md',f'''# Cost model

Measured selected audit usage: {json.dumps(usage,indent=2)}

Pricing assumptions: $0.30 / million input tokens and $0.90 / million completion tokens. These are configurable estimates, not a verified billing quote. Under these assumptions, the selected successful responses estimate **${usage['estimated_usd']:.6f}** total, or ${percall:.6f} per returned call. In-flight interrupted requests, prior audit runs, ongoing monitoring, hosting, electricity, storage and data licenses are excluded. Reasoning tokens may be included in provider completion usage; no hidden text is stored.

Normal event: two independent calls; up to two attempts per stage. Worker retries resume persisted stages. The configurable per-event ceiling is 12 attempts; daily ceiling 60 attempts. Conservative daily/monthly reservation ceilings default to $0.50 / $10. Reservations use prompt UTF-8 bytes plus an overhead allowance and the maximum 4096 completion tokens; failed/interrupted reservations are not silently released. They are an application control, not an invoice guarantee. Provider-side billing limits remain required for public deployment. Budget windows use UTC.

| Projection | Calls before retry allowance | Estimated model cost at measured mixed-workload mean |
|---|---:|---:|
| Idle news-only day, no justified events | 0 | $0 model inference |
| Completed-session contextual review of four demo securities | 8 | ${8*percall:.4f} |
| Normal day: five investigated events + baseline + one correction | 19 | ${19*percall:.4f} |
| Busy day: thirty events + baseline + one correction | 69 requested, capped/deferred at 60 and spend ceilings | at most ${60*percall:.4f} at this mean, not a billing bound |

The mean mixes small evaluation prompts and larger real-source packages; projections are illustrative capacity estimates. A monthly invoice is not available. Monthly infrastructure cost is **unquoted** because no approved host, paid deployment or production storage plan exists.

News request projection: default four core companies plus four rotating discovery candidates gives roughly 2,304 source requests/day at a five-minute cadence, before source failures, investigator enrichment and user universes. If all 32 companies are actively observed, the theoretical poll count is 9,216/day. These are upper cadence projections, not measured successful coverage or licensed entitlements. Providers may throttle; visible health degrades. No model is called by routine source polling.

Controls: one inference worker; four bounded fetch threads; durable pending notices; holding priority; bounded retries and cooldown; duplicate suppression; per-workspace admission limits; public fresh inference disabled by default in the production template. Budget-exhausted risks remain recorded with explicit queued/failed reasons and are visible when the user returns. No email/push channel has been configured.
''')
write('RELEASE_READINESS.md',f'''# Release readiness

Local integrated build: implemented and tested. Public production release: **not yet verified or deployed**.

{feature}

## Exact local startup

```powershell
cd C:\\Users\\antho\\marketbell
python -m venv .venv
.\\.venv\\Scripts\\python.exe -m pip install -r requirements.lock
npm run dev
```

On this machine the isolated environment is already installed. Open `http://127.0.0.1:4318`. Python-only startup: `.\\.venv\\Scripts\\python.exe -B -m backend.main`. Tests: `npm test`; syntax: `npm run lint`; frontend packaging: `npm run build`.

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
''')
# Current architecture and reuse hashes.
p=root/'ARCHITECTURE.md';s=p.read_text(encoding='utf-8');s=s.split('\n\n## Autonomy, impact and dynamic universe')[0];s+='''

## Autonomy, impact and dynamic universe

`backend/impact.py` owns immutable lifecycle, original decision snapshots, append-only horizon reviews, canonical source identity, attributed feedback, indexed projections and bounded history queries. SQLite migrations now span v1–v7. Existing raw records and journal history are preserved; migration backfills are labelled. Outcome reviews never rewrite the original thesis or grant authority.

`backend/universe.py` owns reviewed manual/CSV records, the 32-company exchange-verified catalog, pins, mandates, non-trading list rules and portfolio proposals. News-led discovery rotates through the wider curated catalog. User-entered holdings remain distinct from watchlist and pool candidates. List organization adds no exposure; note preparation retains its quantitative governor. Brokerage execution and agent-created ownership are prohibited. Automatic list rules and edits require explicit confirmation; conflicts ask again.

`model_calls` tracks safe attempt metadata and conservative spend reservations. One inference worker and four bounded news-fetch threads remain. Durable worker heartbeat is every ten seconds while idle; source monitoring has its own thread. `/api/diagnostics` requires a private bearer token. Frontend responses omit private requests/transcripts and bound event/history payloads. Personal review follows opted-in durable portfolio records, independently of browser sessions.
''';p.write_text(s,encoding='utf-8')
p=root/'README.md';s=p.read_text(encoding='utf-8').replace('-r requirements.txt','-r requirements.lock').replace('A governed market research worker','A news-led, always-on investing research agent').replace('Open http://127.0.0.1:4318 .','Open http://127.0.0.1:4318 . This machine now has an isolated MarketBell virtual environment.').replace('Autonomous operation needs current authorized data:', 'News-led autonomous interpretation works without a fresh price baseline; note preparation fails closed when current quantitative context is unavailable. Completed-session context uses:').replace('SQLite migrations run on startup;','SQLite migrations v1–v7 run on startup;').replace('The four-symbol universe and bounded source lookback are deliberate limits, not full-market coverage.','Four initial securities plus bounded rotating discovery from a 32-company curated catalog and source lookback are deliberate limits, not full-market coverage.')
s=s.split('\n\n## Impact and universe management')[0];s=s.replace('This machine now has an isolated MarketBell virtual environment. This machine now has an isolated MarketBell virtual environment.','This machine now has an isolated MarketBell virtual environment.');s+='''

## Impact and universe management

Portfolio supports reviewed manual/CSV entries, explicit mandates, pins, watchlist/pool organization and non-trading portfolio proposals. Impact separates genuine operation, verified replay and an isolated 120-case illustrative history. Today shows server-computed workload and current monitoring health. Preferences includes the Learning Journal and revocable note/list authority. Original decisions are immutable; 7/30/90-day follow-ups and user feedback are separately recorded. Positive price changes are never treated as proof of thesis accuracy.

Read docs/FINAL_ENGINEERING_AUDIT.md and docs/RELEASE_READINESS.md for measured evidence, honest limits and remaining public-release gates.
''';p.write_text(s,encoding='utf-8')
p=docs/'DEPLOYMENT.md';s=p.read_text(encoding='utf-8').replace('news/optional price/filing detectors','news detectors').replace('daily call budget','daily call and estimated-spend budgets');p.write_text(s,encoding='utf-8')
p=docs/'REUSE_INVENTORY.json';data=json.loads(p.read_text(encoding='utf-8'))
for c in data['components']:
 target=root/c['target']
 if target.exists():c['target_sha256']=hashlib.sha256(target.read_bytes()).hexdigest()
for d in data['runtime_dependencies']:
 try:d['version']=md.version(d['name'])
 except md.PackageNotFoundError:pass
p.write_text(json.dumps(data,indent=2),encoding='utf-8')
p=docs/'RELEASE_READINESS.md';p.write_text(p.read_text(encoding='utf-8').replace('\\\\','\\'),encoding='utf-8')
print('Generated engineering, performance, AI, autonomy, cost and release-readiness reports from measured artifacts.')
