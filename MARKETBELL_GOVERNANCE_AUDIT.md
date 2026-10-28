# MarketBell governance audit: operating-principles update (2026-10-25)

**Scope.** This is a bounded governance, modularity and decision-quality update to `C:\Users\antho\marketbell`.

- `C:\Users\antho\trading-research-lab` was read only, for its governance principles (`GOVERNANCE.md`, correction notices). Nothing there was modified, copied or integrated.
- No commit was made. The worktree already held about 96 uncommitted paths from earlier work (base `fbda7ff`). This update sits on top of them, and only the files listed below were touched.

## 1. What already existed (read-only audit)

MarketBell already implemented most of the requested principles, verified by the existing tests and earlier assurance (`docs/MARKETBELL_DECISION_INTEGRITY_AUDIT.md`).

| Requirement | Classification | Existing implementation |
|---|---|---|
| Research separate from authority | REUSE + MINOR | Evidence → Research → blind Challenge → deterministic `checks` → owner → `prepare` (read back) → follow-ups. Missing: labels on each line saying what kind of statement it is (fact, interpretation, proposal, approval, verified action). |
| Recommendation never shown as executed | REUSE + MINOR | The model schema has no action field; only `prepare()` creates a note, and it reads it back. The UI showed "RECOMMENDATION" but had no per-case lifecycle stage. |
| Decision rights (auto / owner / blocked / monitor) | REUSE + MINOR | Statuses `handled` / `needs` / `blocked` / `held` already enforce them. Missing: an explicit decision-right label and the reason for it. |
| Standing rules: reviewable, editable, pausable, revocable | REUSE | Versions, `rule_revision`, expiry, conflicts ask, revocation applies mid-investigation. |
| Approval of one event is not approval of all | REUSE + **MISSING (defect)** | Rules were not retroactive to cases detected before approval. **But an explicit edit that widened a rule, or a resume after a pause, still applied to cases detected before the edit or resume** (on a retry). List-management rules had no timing check at all. |
| Fail safely | REUSE + **MISSING (defect)** | Fail-closed provider and schema handling, exact-quote citations, headline-only gate, deferred notices. **But stale evidence could carry standing-rule authority:** webhooks accept items up to 90 days old, and SEC indexes can lag. |
| Investment quality ≠ software correctness | REUSE + MINOR | Follow-ups say "positive returns alone do not establish correctness". No per-decision statement existed. |
| Modularity | REUSE + MINOR | Provider seam `AI(store, provider)`, `Engine(store, ai)`. The policy evaluator could not be replaced without editing the engine. |
| Decision explanations | MINOR | The investigation modal showed the trigger, both summaries, policy checks, and each evidence item with its retrieval time. Missing: freshness, sufficiency, downside, applicable rules, why owner review was or wasn't needed, and what to check later. |
| Trustworthy learning | REUSE | Inactive candidate, `grants_authority:false`, source event, version and correction text preserved, explicit save, pause / edit / revoke. Missing only: showing which rule affected a later decision, and its terms. |
| Operational visibility | REUSE + MINOR | Needs You, Handled For You, quiet log, deferred notices, journal. No single at-a-glance count of outcomes by decision right. |
| Research discipline | MISSING | Prompts did not require the price-move / returns / correlation / hypothetical / hindsight distinctions. There were no deterministic concentration, overlap, stale or thin-evidence cautions. |
| Security and action boundaries | REUSE (no change) | No order endpoint or code; Alpaca limited to daily bars; CSRF / Origin / Host checks; secrets never in UI. |

## 2. What changed

| Change | Files | Behaviour |
|---|---|---|
| **Rules never widen retroactively** (defect fix) | `backend/engine.py`, `backend/universe.py` | `effective_from` is set when a rule is approved, explicitly edited or resumed. `approved_before()` uses it, so a rule applies only to cases detected after its current terms took effect. List-management rules get the same timing check (labelled simulations are exempt). |
| **Stale evidence never carries automatic authority** (defect fix) | `backend/engine.py`, `backend/explain.py` | `current_enough()`: at least one cited non-price source must be ≤ 7 days old at detection. Otherwise the case goes to the owner with `rule_withheld` and a plain reason. Rule evaluations record `evidence_current`. Recorded replays and labelled simulations are judged at their own session and keep their pinned outcomes. |
| **Decision explanation contract** | `backend/explain.py` (new), `backend/app.py` | A pure function of stored records, attached to every case on read. It gives: decision right and reason; lifecycle stage (proposed, approved, completed and verified, and so on); what happened (observed); why it matters (AI interpretation); evidence with per-item freshness, how it is dated, price baseline and sufficiency; uncertainty; downside and limits; research cautions; rules applied, withheld or not matching; proposed action; what to check later; and the software-versus-investment note. Routine no-action cases get a brief version. There is no confidence score. |
| **Decision-time context** | `backend/engine.py` | At resolution, the event records whether the stock is held or watched, sector exposure and limit, the rules evaluated, and the applied rule's text (`decision_context`), plus `evidence_sufficiency`. This context is used for explanation only and never feeds authority. |
| **Replaceable policy module** | `backend/engine.py` | `Engine(store, ai, policy=fn)`. Both `resolve` and `decide` use the injected evaluator; the default is `checks`. |
| **Research discipline in prompts** | `backend/ai.py` | `DISCIPLINE` is appended to the Research and Challenge instructions only. The schema, validation, provider and model are unchanged. |
| **Operational counts** | `backend/explain.py`, `backend/app.py`, `public/app.js` | `/api/state.operations` holds counts straight from case records, shown as one line on Today. |
| **UI labels** | `public/app.js`, `public/style.css` | Each card shows its decision right and stage ("Owner review · Proposed only. Not executed…"). The investigation view opens with the decision summary. No new screens; existing design tokens are used. |

## 3. Deliberately not implemented

- Numerical confidence scores, risk metrics, a portfolio optimiser, backtester, ledger, stop-loss or profit-taking rules, or any strategy.
- A new status system: decision rights are derived from the existing statuses.
- New screens or dashboards: one count line and a summary block inside the existing investigation view.
- Database migrations: explanations are derived on read, and context is stored inside the existing event document.
- Any change to the Nemotron model, provider configuration or Nebius usage beyond the added prompt sentences.
- Anything from the Trading Research Lab's code, data, 30-stock universe or algorithms.
- Rewriting existing records: cases resolved before this update get explanations without `decision_context` (no concentration or rule lines).

## 4. Modules and interfaces affected

- **Modified:** `backend/engine.py`, `backend/universe.py`, `backend/app.py`, `backend/ai.py`, `public/app.js`, `public/style.css`.
- **New:** `backend/explain.py`, `test/test_governance_principles.py`, this file and `MARKETBELL_OPERATING_PRINCIPLES.md`.
- **API changes are additive:**
  - every event gains `explanation`;
  - `/api/state` gains `operations`;
  - events resolved from now on carry `decision_context` and `evidence_sufficiency`, and some carry `rule_withheld_reason`;
  - rules gain `effective_from`;
  - rule evaluations gain `evidence_current`.

## 5. Tests and results

The tests ran on an isolated copy of the worktree (Linux VM, Python 3.10.12; the project targets 3.12), with no network or model calls.

| Suite | Result |
|---|---|
| Baseline before changes | 201 Python tests, all pass |
| `test_backend` | 49 pass. A first run exposed a `KeyError` on rules without stored text; fixed. |
| `test_assurance` | 52 pass |
| `test_impact_universe` | 55 pass |
| `test_build_lessons` | 21 pass |
| `test_quiet_sources` | 24 pass |
| **`test_governance_principles` (new)** | **21 pass** |
| **Total** | **222 Python tests pass.** JS domain test passes; JS syntax and static checks pass. |

The new focused checks cover:

| Requirement | Test(s) |
|---|---|
| Research cannot bypass authority policy | `ResearchCannotBypassPolicy`: clear reviews plus a matching rule are still blocked by hard policy; a model answer with an `action` field is rejected |
| Stale or insufficient evidence labelled correctly | `EvidenceLabelling`: stale → rule withheld, labelled, `evidence_current:false`; fresh → rule used; headline-only → "headline or price data only"; failed investigation → "Not verified" |
| Recommendation cannot pass as an executed action | `RecommendationIsNotAction`: a model summary claiming "order executed" stays *Proposed* with no note; "completed" requires a verified readback; owner approval is labelled as the owner's |
| Standing rules gain no broader authority implicitly | `RulesNeverWidenImplicitly`: one-off approval creates no rule; a widening edit and a resume do not reach back; `effective_from` timing |
| Owner corrections remain traceable | `CorrectionsTraceable`: correction → candidate → approved rule (source, version, text) → journal order → later application explained with the rule's terms |
| Nemotron research and challenge flows still work | `ProviderAndPolicyReplaceable`: a full Research + blind Challenge run through the real `AI` class with a replacement provider. Discipline is present in both prompts; no Research verdict is in the Challenge request. Existing verified-replay tests still pass with their pinned outcomes. |
| Provider or policy replaceable independently | the same class: a policy swap blocks without changing research; the default engine is unaffected |
| Product functions and UI intact | All previous suites pass. `ExplanationAndVisibility`: brief routine explanations, full fields, no confidence, concentration and overlap cautions, counts from records, `/api/state` exposes them. Rendered UI checked at 1400 px and 390 px: no page errors, no horizontal scroll (screenshots in `work/governance_ui/`, git-ignored). |

**Negative control.** With the two defect fixes reverted on a scratch copy, 4 of the new tests fail (the stale-evidence test and three rule-timing tests). So the tests detect the defects, not just the new code.

## 6. Closeout review

| Area | Finding |
|---|---|
| Data integrity | No change to evidence, snapshot, journal or evaluation tables. Explanations are derived; decision context is new data, written in the same resolution transaction. |
| Security | No new endpoints, dependencies, credentials or integrations. User-visible strings are escaped (`esc`) in the UI. |
| Action permissions | Only made stricter: two new conditions can withhold automatic authority; nothing grants new authority. `executionEnabled:false` is unchanged. |
| Failure handling | Explanations tolerate missing fields and older records. A failed case is labelled "Not verified" and never "researched". |
| Audit correctness | Rule changes still create revisions and journal entries; `effective_from` changes are part of each rule version. |
| Mobile usability | Checked at 390 px: the summary block wraps, the count line wraps, no overflow. |
| Deployment readiness | Unchanged apart from the prompt text: the Research and Challenge system prompts gain about 110 tokens per call. |

## 7. Material limitations

- **No live Nemotron call was made** in this update (cost, and no key in the test environment). The prompt addition is verified through the real `AI` request builder with a replacement provider. Its effect on Nemotron's wording is not yet observed: run one live Research/Challenge (Live Nemotron mode) before the demo.
- **PostgreSQL not exercised.** The new count query is plain SQL, but the PostgreSQL test path was not run (`psycopg` is absent in the test environment).
- **Tests ran on Linux / Python 3.10.** Run `npm test` on Windows (`.venv`, Python 3.12) before committing.
- **Undated evidence is aged from its retrieval time** and labelled `dated_by: retrieved`.
- **Freshness thresholds are fixed constants** (fresh ≤ 3 days, stale > 7 days), matching the existing 7-day news lookback.
- **Cases resolved before this update** lack `decision_context`, so their explanations omit concentration and rule lines.
- **Cautions are deterministic heuristics** over recorded portfolio fields (holdings, watchlist, sector exposure). They are prompts to think, not measured risk.

## 8. Remaining P0/P1 blockers

None introduced or found in this scope. The two P1 defects found (retroactive widening via edit or resume, and stale evidence carrying rule authority) are fixed and covered by tests. Committing remains the owner's decision.
