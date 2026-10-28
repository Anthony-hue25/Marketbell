# MarketBell operating principles

MarketBell watches a portfolio and watchlist, investigates meaningful developments with NVIDIA Nemotron, and asks
the owner only when a decision needs them. These principles govern how it researches, decides, acts, learns and
explains. They come from the investment-research governance used in the Trading Research Lab ("a technical PASS
is not a financial PASS"), applied as research and decision discipline. No strategies, backtests, portfolio
engines or trading capability were imported.

The order of authority never changes: **hard policy > approved standing rules > soft context > model recommendation.**

## 1. Research is separate from authority

Every case moves through seven separate records. Each later step can only use earlier ones, never replace them.

| Step | What it is | Where it lives |
|---|---|---|
| 1. Evidence collected | Source text, publisher, publication and retrieval times; first version never overwritten | `evidence_versions`, event `evidence[]` |
| 2. AI interpretation | Nemotron Research summary with exact-quote citations | event `research` + trace hash |
| 3. AI recommendation | Research and a blind Challenge decision (`clear` / `hold` / `no_action`) | event `research`, `challenge` |
| 4. Deterministic policy | Position, sector, cash, freshness, evidence, mandate checks in code | `engine.checks` → event `policy` |
| 5. Owner approval (where needed) | Approve, reject, acknowledge, or correct and teach | `Engine.decide`, `Engine.propose` |
| 6. Authorised action | A research consideration note, persisted and read back. Never an order | `Engine.prepare` → `note` |
| 7. Verified outcome | 7 / 30 / 90-day follow-ups comparing later evidence with the sealed decision | `decision_snapshots`, `evaluations` |

An AI recommendation is never shown as an action. Each case carries a derived explanation
(`backend/explain.py`) that labels each line as one of:

- **Observed:** the trigger and evidence.
- **AI interpretation:** the Research and Challenge summaries.
- **Proposed:** "Proposed only. Not executed; waiting for your decision."
- **Approved:** "Approved by you, then completed and verified."
- **Completed and verified:** shown only when a note was saved **and read back**. A "handled" case with no verified readback is never labelled completed.

## 2. Decision rights are explicit

MarketBell keeps its existing case statuses. The decision right is derived from them, so there is no second status
system:

| Decision right | Existing status | Meaning |
|---|---|---|
| AUTO-HANDLE ("Handled within authority") | `handled`, no owner involvement | A routine review with nothing to do, or a note prepared under an approved standing rule after fresh policy checks and two clear reviews |
| OWNER REVIEW | `needs` (and `handled`/`rejected` once the owner decides) | Approve, reject or correct and teach |
| BLOCKED | `blocked` | A hard policy failed or evidence was missing; nothing prepared |
| MONITOR ("Monitoring") | `held` | The reviews were not both clear, or the risk concerns an unowned stock; nothing proposed |
| In progress / Not verified | `queued`, `investigating` / `failed` | Waiting for evidence or investigation / failed closed with no verdict |

Standing rules:

- **Created only by explicit approval.** A correction creates an inactive candidate with `grants_authority: false`. The owner must save it.
- **Reviewable, editable, pausable, revocable and expiring.** Each change creates a new version and keeps the previous one (`rule_revision`).
- **Never retroactive.** A rule applies only to cases detected after its current terms took effect: approval, or the latest explicit edit or resume (`effective_from`). Widening a rule therefore never reaches back to earlier cases. The same timing applies to list-management rules.
- **Scoped.** Approving one case creates no rule. A narrow correction is never turned into broad permission, as the PREFERENCE prompt and `validate_rule` limits require.

## 3. Failing safely

- A provider or schema failure ends as `failed` ("Not verified. No action taken"). No synthetic verdict is substituted.
- Citations must be exact substrings of the supplied evidence, or the answer is rejected.
- Headline-only or price-only support never carries automatic authority (`substantive()`).
- **Stale evidence never carries automatic authority.** Cited sources must include at least one item no more than 7 days old at detection (`current_enough()`). Older evidence still reaches the owner, labelled.
- Budget exhaustion keeps held-stock notices as explicit deferred items. They are never dropped silently.
- Disagreement between the two reviews holds the case.

## 4. Investment quality is not software correctness

These are reported as separate things, and none implies another:

- application correctness (tests);
- data freshness and integrity (sealed evidence, freshness labels);
- evidence sufficiency (substantive, current);
- policy compliance (deterministic checks);
- recommendation quality (follow-up reviews);
- outcome usefulness (owner feedback and impact).

Every full explanation ends with: *"This record shows the workflow ran and your policy was applied. It is not evidence
that the research is right or that acting on it would be profitable."* A price rise alone never counts as a correct
recommendation.

## 5. Explanations

For meaningful cases the explanation gives:

- what happened;
- why it matters (labelled AI interpretation);
- the evidence, with the freshness of each item, the price baseline and sufficiency;
- uncertainty (the Challenge view);
- downside and limits (failed policy checks, Challenge concerns, research cautions);
- which standing rules applied, were withheld or did not match;
- the proposed action;
- the decision right and why it applies;
- what to check later.

Routine no-action cases get a two-line brief. No numerical confidence score is produced.

## 6. Research discipline

Research and Challenge prompts require these distinctions:

- a price move is not profitability;
- historical returns are not expected returns;
- correlation is not diversification;
- hypothetical outcomes are not executed trades;
- a recommendation is not a decision;
- lower risk is not higher return;
- a current opportunity is not a hindsight-selected winner.

Deterministic cautions are drawn from existing records only. They flag:

- stale cited evidence;
- support that is headlines or price data only;
- conclusions resting on a single source;
- disagreement between the two reviews;
- adding to a stock already held;
- the same stock both held and watched;
- a sector already at half its limit or more;
- replay or simulation context.

No risk metrics are calculated.

## 7. Visibility

The Today page shows the existing Needs You and Handled For You lists. It also shows one line of counts taken
straight from case records:

- handled without you;
- handled under your rules;
- needs you;
- decided by you;
- blocked;
- monitoring;
- awaiting evidence (including deferred notices);
- not verified;
- outcomes reviewed.

Nothing is estimated or inflated.

## 8. Boundaries that do not change

There is no:

- live trading;
- broker order endpoint (`/api/orders` returns 404);
- rebalancing;
- money movement;
- new account permission or integration.

The Alpaca adapter is limited to daily bars for allow-listed symbols. The only action MarketBell can take is a
non-trading research note, or list organisation within the mandate.

## Replaceable modules

| Component | Module | Replace by |
|---|---|---|
| Market data | `backend/market.py`, `backend/vendor/alpaca_contract.py`, `backend/sources.py` | Source adapters (unchanged) |
| Research provider | `backend/ai.py` `AI(store, provider=…)` → `NemotronProvider` | Passing another provider with `complete(messages, tools)` |
| Challenge / review | `AI.analyze('CHALLENGE', …)`: blind, separate call | Same provider seam |
| Decision policy | `engine.checks` | `Engine(store, ai, policy=fn)` |
| Action handler | `Engine.prepare` (note only, read back) | Not intended to grow |
| Outcome verification | `backend/impact.py`, follow-ups in `backend/worker.py` | Unchanged |
| Audit and learning | `backend/store.py` hash-chained journal, rules, revisions, rule evaluations | Unchanged |
| Presentation | `public/app.js`, `backend/explain.py` (derived explanation) | Explanation is a pure function of records |

`test/test_governance_principles.py` shows a provider and a policy each being replaced without changing the other.
