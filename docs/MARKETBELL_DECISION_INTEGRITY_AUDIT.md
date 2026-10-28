# MarketBell decision-integrity audit (independent assurance, 2026-10-19)

## Baseline (verified independently, earlier reports not trusted)

| Item | Finding |
|---|---|
| Git | HEAD `fbda7ff` (*Make MarketBell continuously event-driven…*). The worktree carried 63 changed/added paths (~5.7k insertions) **not committed**. Preserved untouched: `work/assurance/pre-assurance-worktree.patch`, `pre-assurance-status.txt`, `pre-assurance-HEAD.txt`. No commit, branch or history change was made by this assurance. |
| Stack | Python 3.12 WSGI app (`backend/`), vanilla JS UI (`public/`), SQLite (WAL) persistence, in-process worker threads, gunicorn container, Node launcher for local dev. |
| Tests at baseline | 103 Python tests: 102 pass, **1 fail** (time-bomb: hard-coded `2026-10-23` treated as "after the horizon"; wrong from 2026-10-19). JS domain test passes. |
| Tests now | **149** Python tests: all pass on SQLite; on PostgreSQL 16, 146 pass, 3 skipped (SQLite-only tamper simulations / file backup). JS domain test passes. |

### Maturity classification (before this assurance)

- **Implemented and verified:** governed engine (deterministic policy precedence, conservative Research/Challenge
  resolution, schema + exact-quote citation validation), explicit rule approval/pause/revoke/edit with versions,
  hash-linked audit with UPDATE/DELETE triggers, immutable decision snapshots and follow-up evaluations,
  CSRF/Origin/Host checks, HttpOnly SameSite=Strict sessions, tenant scoping, verified replay of authentic
  Nemotron recordings, news polling with cursors and durable notices.
- **Implemented but insufficiently tested:** restart recovery (only in-process), multi-process safety (file lock
  only), budget exhaustion behaviour, evidence revisions, outcome comparisons across provider bases.
- **Simulated / demo-only:** illustrative 120-case history (`illustrative-shared-v3`, clearly labelled), issuer
  pages hard-coded for PSX/VLO Q3 2026, demo portfolios only (no private accounts).
- **Incomplete / missing for Azure:** production database, process separation, cross-process coordination,
  health/readiness semantics, telemetry, IaC, CI/CD, external audit anchoring.

## Three histories

| History | Where | Guarantee now |
|---|---|---|
| **Evidence** | `evidence_versions` (append-only, content-addressed), `screened_news`, `news_identity`, event `evidence[]` | First-seen version is never overwritten; every later version retained with first-seen time; snapshot seals SHA-256 of each item and the package; publisher/discovery provenance kept separately; `verify()` re-hashes all versions. |
| **Decision** | `decision_snapshots` (immutable), `lifecycle` (immutable), `rule_evaluation` (immutable docs), `audit` | Research/Challenge outputs, deterministic policy result, rule evaluation incl. *approved_before_detection* and *evidence_substantive*, disposition and rationale frozen at resolution and committed in the hash chain. |
| **Outcome** | `evaluations` (immutable, append-only per horizon), `impact_reports` | Later evidence must postdate the decision and fall within the horizon; price comparisons only on one adjustment basis; observations before the decision rejected; provider revisions recorded beside, never over, the original. Positive returns never establish thesis accuracy (unchanged). |

## Operating chain audit: NEWS → TRIAGE → RESEARCH → CHALLENGE → POLICY → HANDLE/ESCALATE → LEARN → AUDIT → FOLLOW-UP

| Requirement | Verdict | Evidence |
|---|---|---|
| Unsupported recommendation never treated as fact | Pass | Exact-quote citation validation; headline-only limitation text; prompts mark evidence **and trigger-quoted source text** as untrusted (prompt hardened). |
| Nemotron cannot bypass deterministic policy | Pass | `resolve()` computes policy in code inside the write transaction; model output only feeds validated fields. |
| Challenge disagreement handled safely | Pass | Any hold/disagreement → `held`; existing tests + `test_disagreement_held`. |
| User corrections cannot silently grant authority | Pass | Candidate inactive; `grants_authority:false`; `Authority.test_correction_creates_no_authority_until_explicit_approval`. |
| Standing rules require explicit approval | Pass, strengthened | Rules without `approved_at` never match; rules never authorize cases detected before approval (**fixed P1**). |
| Revoked rules stop applying | Pass | Including revocation **during** an investigation (`test_rule_revoked_during_investigation_stops_applying`). |
| Portfolio ownership never changed without verified input / confirmation | Pass | Only `portfolio-update` (versioned) and `universe-commit` (`confirmed:true` + version) change holdings; `consider()` never mutates holdings; public demo discards share counts and cost basis. |
| Automatic watchlist changes within the mandate | Pass | Exclusions, caps, pins, holdings protection enforced in `consider()`; mandate re-commit pauses inconsistent list rules. |
| Actions reproducible and attributable | Pass | Hash-chained journal, trace hashes, request hashes, rule versions, lease owners on jobs, structured events. |
| No brokerage execution capability | Pass | No order/trading endpoint or code; Alpaca adapter restricted to `/v2/stocks/bars` with `1Day` and allow-listed symbols; `/api/orders` 404 test. |
| Budget exhaustion never silently drops a material holding risk | **Fixed (P1)** | Deferred holding notices are preserved, journaled, shown to holders (`deferredNotices`), alerted (`budget.deferred`), retried, and marked investigated afterwards. |
| Interactive use cannot starve monitoring | **Fixed (P1)** | Separate interactive daily cap (`MARKETBELL_INTERACTIVE_DAILY_CALLS`, default ⅓ of the daily cap) attributed per workspace. |

## Defects found and fixed

| ID | Pri | Defect | Fix | Test |
|---|---|---|---|---|
| D1 | P1 | Rule approved after detection authorized the earlier case on retry | `approved_before()` | `Authority.test_rule_approved_after_detection_*` |
| D2 | P1 | Headline/price-only support could produce an automatic note under a rule | `substantive()` gate, `rule_withheld` | `Authority.test_headline_only_*` |
| D3 | P1 | Evidence history last-write-wins | `evidence_versions`, sealed snapshot hashes | `EvidenceIntegrity.*` |
| D4 | P1 | Webhook redelivery with changed content was silently ignored (revision lost) | Revision gets its own notice id (`revision_of`), original untouched, journaled | `Webhook.*`, container T7 |
| D5 | P1 | Budget exhaustion left holding news only as a count | Explicit deferred state + alert | `Budgets.test_budget_exhaustion_*` |
| D6 | P1 | Visitors could exhaust the shared model budget used by monitoring | Scoped budgets | `Budgets.test_interactive_*` |
| D7 | P2 | Daily-data timestamp fixed at 20:00 UTC | Calendar close | `TemporalCorrectness.*` |
| D8 | P2 | Outcome % change across split/basis changes; pre-decision observations accepted | Same-retrieval basis, basis check, decided< observation | `Outcomes.*` |
| D9 | P2 | Entity aliases from generic first words | Curated strong/weak aliases | `Provenance.*` |
| D10 | P2 | Aggregator recorded as source, no publisher | Publisher/discovery split | `Provenance.*` |
| D11 | P3 | Time-bomb test | Calendar-relative dates | `test_impact_universe` |

Cross-process and deployment defects (leases, recovery, health, proxy IP) are recorded in
`docs/PERFORMANCE_AND_RELIABILITY.md` and `docs/SECURITY_AUDIT.md`.

## Adversarial review (post-fix)

`test/test_assurance.py::Adversarial` and the container fault suite attacked the fixes with: future, naive and
90-day-old webhook timestamps (rejected, nothing queued); expired rules; rule candidates approved after the
source decision changed (rejected); late user approval after a new completed session (fail closed);
challenge failure followed by restart (Research not repeated, no extra model call); duplicate delivery after
completion (no second note); duplicate/syndicated articles (one investigation); revised webhook content
(separate investigation, original preserved); SIGKILL and SIGTERM mid-investigation; two workers; database
outage. All passed. Passing unit tests are **not** the basis of the verdict; see `docs/PRODUCTION_GO_NO_GO.md`.

## Residual decision-integrity limits (not defects)

- Headlines are headline-only evidence; full articles are not retrieved.
- RSS `pubDate` is provider-asserted; MarketBell records `retrieved_at` as the decision-time knowledge boundary.
- Database owner can still drop triggers; the external anchor detects truncation/rewrite at the next daily run
  (detection, not prevention). Locking the Blob immutability policy is an explicit owner decision.

## Addendum 2026-10-21: what counts as substantive evidence

`engine.substantive()` now distinguishes headlines by publisher tier. A headline counts towards automatic
(standing-rule) authority only when its publisher is tier 1 (issuer wire or regulator) or tier 2 (established
newsroom); commentary and unknown publishers (tier 3) never do, and price data never does. The tier comes from
the publisher's site domain supplied by the feed, never from its display name, so a feed item labelled
"Reuters" from another domain stays tier 3 (`test_quiet_sources.Tiers`). SEC filings (`regulatory_filing`) count;
their text is index metadata (form, items), and the evidence carries that limitation. Policy checks, both
independent reviews and approval timing still apply unchanged.

The second review is skipped only when the first returns `no_action` / `Routine` / not material for a stock the
investor neither owns nor watches. The skipped review is recorded (`skipped: true`, committed to the journal) and
can only produce a "nothing to do" outcome; it never reaches preparation or a standing rule. Turn it off with
`MARKETBELL_SKIP_ROUTINE_CHALLENGE=0`.
