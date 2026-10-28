# MarketBell final engineering audit — 2026-10-14 (America/Port_of_Spain)

## Actual implementation

Implemented: immutable autonomy lifecycle; canonical headline screening; server-side period aggregates and bounded case pagination; Today workload metrics; Impact; prospective 7/30/90-calendar-day reviews; append-only outcome assessments; attributed missed-event/usefulness reports; Learning Journal; note and list-rule approval/edit/pause/revoke; manual/CSV holdings and watchlist onboarding; pins; explicit mandates; a three-tier universe; bounded rotating discovery from 32 curated companies; independent comparative investigations and non-trading portfolio proposals. No recommendation approval can change holdings. Manual holding edits require a separate reviewed entry confirmation and do not verify external transactions.

Real operation uses news and real Nemotron on Nebius. Verified Replay uses authenticated preserved model outputs. The isolated illustrative dataset contains 120 deterministic historical cases across 60 days, generated through Engine policy, rule, persistence and verification paths. Its simulated model outputs, approvals, chronology and retrospective outcomes are labelled. It is never included in genuine counters. No historical operating data or investment returns were invented as genuine evidence.

## Audit scope and preservation

Baseline commit: `fbda7ffa3a21f18a631ce1b5f080dedefe948861`. Existing changes and user state were preserved. The pre-audit patch and SQLite backup remain under ignored `work/audit`. Reference repositories were read-only. See BASELINE_AUDIT.md. This audit includes the subsequent Autonomy/Learning/Impact and Dynamic Investment Universe addenda; the unrelated Adaptive Stay attachment was not implemented.

## Findings fixed

| Priority | Finding / root cause | Correction and evidence |
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


## Verification and limits

Final automated test results are recorded in AUDIT_TEST_RESULTS.json, including counts and output hash. Separate real-model evidence: AUDIT_AUTONOMY_PROOF.json and AUDIT_AI_EVALUATION.json. Load: 1, 10 and 100 distinct events plus equal duplicate arrivals; all completed exactly once with explicitly mocked inference. Backup/readback and immutable audit checks passed. An actual process was stopped after Research persisted, then a different verified process finished the same job without duplicating its outcome. Authentic RSS was fetched separately and passed through the normal detector while no frontend session existed.

The provider produced 20 successful returned calls in the selected audited evidence set. This is not a claim that all model outputs were equally good: the initial synthetic refinery-risk evaluation exposed provenance/materiality confusion; its original output remains preserved, followed by a conditional-scenario retest. Six original pairs passed strict structure/quotation checks, not a broad investment-accuracy certification.

Desktop/mobile findings, exact timings, usage assumptions and deployment gates are detailed in PERFORMANCE_BENCHMARKS.md, AI_EVALUATION_REPORT.md, AUTONOMY_VERIFICATION.md, COST_MODEL.md and RELEASE_READINESS.md.

## Remaining priorities

- P0: no known unresolved authority/security defect in the audited local scope. This is not a guarantee against unknown defects.
- P1 release gates: production container/TLS/supervision have not been independently exercised on a working Docker engine; deployment credentials, host and publication rights remain unapproved. Do not declare a public production release.
- P2: curated 32-company coverage; headline-only matching and bounded lookback can miss events or updates; no comprehensive commercial news entitlement is established; no mature genuine 7/30/90-day quality record; no validated total-return benchmark; anonymous workspace credentials lack registered-account recovery; synchronous personalization is bounded by demo scale, not proven at 2,000 active users.
- P3: independent first-time-user comprehension, broader browser/accessibility testing and longer multi-day endurance remain unverified. No fabricated automation-rate target or first-user success claim is made.

## Hackathon assessment

Best Apps and Agents remains the strongest fit. Nemotron performs qualitative Research, independent blind Challenge, preference interpretation and subsequent evidence assessment; deterministic code cannot substitute for that judgement. The useful actions are evidence-backed research notes and delegated list changes, with verified persistence and explicit authority. Prospective accountability and honest failures strengthen the story. Competitiveness is credible, but winning readiness still depends on a reliable hosted demo, concise video, licensed public source/data and an independently understandable teach-once experience.

Official requirements checked: [hackathon overview](https://nebiusglobalaihackathon.devpost.com/) and [official rules](https://nebiusglobalaihackathon.devpost.com/rules). The overview requires a working project using NVIDIA open models on Nebius, a working demo/test build, a public repository with a visible open-source license, and a public video of at most three minutes. These publication steps were not performed.
