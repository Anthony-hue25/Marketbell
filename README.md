# MarketBell

**You don't have to babysit the market.**

MarketBell is an always-on AI investing research agent. It watches a portfolio and watchlist, investigates what
matters with **NVIDIA Nemotron on Nebius Token Factory**, and only asks when a decision genuinely needs you. When you
correct it, it proposes a narrow standing rule; once you approve that rule, it handles the next matching case by
itself, inside limits that code enforces every time.

> **AI understands the market. Rules control the money.**
> MarketBell never trades. It has no brokerage endpoint, no order route and no way to move money. The only action it
> can take is preparing a research consideration note (and organising a research watchlist within your mandate).

Built for the Nebius Global AI Hackathon · Track: Best Apps and Agents.

---

## The problem

Every day brings a stream of headlines, filings and price moves for the companies you own or follow. Most of it is
routine; a few items deserve a closer look; rarely does something actually need your judgement. Staying on top of it
today means watching all of it, and answering the same kind of question again and again.

## What MarketBell does

| | |
|---|---|
| **Watches** | News search, press-release wires, SEC filings and completed-session prices for your holdings and watchlist, on a schedule, with the browser closed. |
| **Notices** | Screens out routine, duplicate, wrong-company and low-credibility items, and keeps a log of what it set aside and why. |
| **Researches** | Nemotron interprets the evidence and must quote its sources word for word; quotes are checked against the evidence. |
| **Challenges** | A second, separate Nemotron call reviews the same evidence **blind** (it never sees the first verdict). Disagreement holds the case. |
| **Checks policy** | Deterministic code checks position size, sector exposure, cash, price freshness, evidence and your mandate. Policy always outranks the model. |
| **Handles or asks** | Routine reviews close themselves. A research note is prepared automatically only under a rule you approved, with fresh evidence and two clear reviews. Everything else comes to you. |
| **Learns, with approval** | "Correct & teach" turns your plain-language correction into a *proposed*, scoped, expiring rule. It grants nothing until you save it, and you can pause, edit or revoke it at any time. |
| **Explains and records** | Every case shows what was observed, what the AI interpreted, how fresh the evidence is, which rules applied, whether anything was actually done, and what to check at 7, 30 and 90 days. Every step is kept in a hash-chained journal. |

### What it handles for you

- Routine reviews where both AI reviews find nothing to act on.
- Research notes covered by a standing rule you approved: both reviews clear, every hard limit met, at least one
  substantive source (not just headlines or price data) and at least one source no more than 7 days old.
- Setting routine news aside, with a visible reason for each item.

### What it brings to you

- A material risk to a stock you own or watch.
- An opportunity that none of your rules covers yet.
- Evidence too thin or too old for automatic action.
- Rules that conflict.
- (Reviews that disagree are held and monitored; cases that would breach a limit are blocked and explained.)

Order of authority, highest first: **hard policy › your approved rules › your context › the model's recommendation.**

---

## How Nemotron and Nebius are used

| Call | Where | Purpose |
|---|---|---|
| Research | `backend/ai.py` → `AI.analyze('RESEARCH')` | Interpret the evidence package; strict JSON with exact-quote citations |
| Challenge | `AI.analyze('CHALLENGE')` | Independent, blind second review of the same evidence |
| Preference | `AI.preference` | Turn a correction into a proposed, bounded rule (validated by code) |
| Follow-up | `AI.followup` | Compare a sealed decision with later evidence at 7 / 30 / 90 days |
| Ask MarketBell | `backend/ask.py` | Answer questions about what it read, did and chose not to do, from records only |

- Provider: Nebius Token Factory (OpenAI-compatible endpoint), `backend/vendor/provider.py`.
- Default model: `nvidia/nemotron-3-super-120b-a12b` (any `nvidia/*nemotron*` model; others are refused).
- Model output is untrusted: schema validation, citation grounding, prompt-injection hardening, a daily call budget
  with a separate allowance for interactive use, and fail-closed handling (a failed call is shown as "Not verified",
  never as research).

---

## Run it

Requirements: Python 3.12+, optionally Node 20+ (for the launcher and JS test).

```powershell
# Windows
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.lock
.venv\Scripts\python -B -m backend.main          # or: node server.mjs
```

```bash
# macOS / Linux
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/python -B -m backend.main
```

Open <http://127.0.0.1:4318>.

**Recorded runs need no keys.** To use fresh inference, provide a Nebius key on the server side only:

```bash
export NEBIUS_API_KEY=...            # never commit it; see .env.example for all settings
```

Docker (single container, SQLite volume): create `.env.production` from `.env.production.example`, then
`docker compose up --build` and open <http://127.0.0.1:8080>.

### Three modes (Today → Demo controls)

| Mode | What it is |
|---|---|
| **Verified Run** | Authentic captured Nemotron runs over real evidence, replayed in your isolated workspace with the same authority code. Labelled as replay. Fastest walkthrough, no key needed. |
| **Live Nemotron** | Fresh Research and blind Challenge calls over the preserved evidence. Results and latency can differ from the recording; an honest "hold" is a valid outcome. |
| **Autonomous** | The background worker's real monitoring history: news it read, set aside and investigated. |

### Two-minute walkthrough

1. **Today** shows what is being watched, what was handled for you and what needs you.
2. Open the **PSX** case → **View investigation**: decision summary, Research vs blind Challenge, quoted evidence,
   policy checks.
3. **Correct & teach** → review the proposed Energy rule → **Save preference**. Separately **Approve** PSX.
4. **Run next similar event** → **VLO** is handled under your rule ("Completed and verified under your standing rule").
5. **Preferences** shows the rule, how often it was applied, and Pause / Edit / Revoke. **Activity** shows the journal.

---

## Architecture

```
sources ─► screen & triage ─► Research (Nemotron) ─► blind Challenge (Nemotron)
                                                          │
                                         deterministic policy & standing rules (code)
                                                          │
                       ┌──────────────────────────────────┼────────────────────────┐
                 handled within authority            needs you               blocked / monitoring
                 (note saved + read back)      (approve · reject · teach)
                                                          │
                                 hash-chained journal · sealed evidence · 7/30/90-day follow-ups
```

| Component | Module |
|---|---|
| Market data & sources | `backend/market.py`, `backend/sources.py`, `backend/vendor/alpaca_contract.py` (daily bars only) |
| Research / Challenge provider | `backend/ai.py`, `backend/vendor/provider.py` (replaceable: `AI(store, provider=…)`) |
| Decision policy | `backend/engine.py` `checks` (replaceable: `Engine(store, ai, policy=…)`) |
| Action handler | `Engine.prepare`: research note only, persisted and read back |
| Explanations | `backend/explain.py`: decision right, lifecycle stage, freshness, cautions, rules, what to check later |
| Outcome verification | `backend/impact.py`, follow-ups in `backend/worker.py` |
| Audit & learning | `backend/store.py`, `backend/db.py`: hash-chained journal, versioned rules, immutable snapshots |
| Web app | `backend/app.py` (WSGI API), `public/` (vanilla JS, mobile friendly) |
| Deployment | `Dockerfile`, `compose*.yaml`, `infra/azure/` (Azure Container Apps, Bicep) |

Storage is SQLite locally (WAL) and PostgreSQL in the cloud configuration.

---

## Tests

```bash
node --test test/domain.test.js
python -B -m unittest discover -s test -p "test_*.py"      # 222 tests, no network or model calls
node scripts/check.mjs                                     # syntax and static checks
```

`test/test_governance_principles.py` shows that research cannot bypass policy, stale or thin evidence never
carries automatic authority, a recommendation is never shown as an executed action, rules never widen implicitly,
corrections stay traceable, and the provider and policy modules can each be replaced.

---

## Honest limits

- MarketBell measures less monitoring and fewer repeat approvals, not stock-picking performance. A completed workflow
  is never presented as evidence that a recommendation would be profitable.
- Headlines are headline-only evidence; full articles are not retrieved. Free sources only; no real-time feed or uptime
  guarantee.
- Demo portfolios only. Private account import is disabled.
- No numeric confidence scores are produced; uncertainty is stated in words by the independent review.

## Further reading

- [`MARKETBELL_OPERATING_PRINCIPLES.md`](MARKETBELL_OPERATING_PRINCIPLES.md): how research, authority, action and learning are separated
- [`MARKETBELL_GOVERNANCE_AUDIT.md`](MARKETBELL_GOVERNANCE_AUDIT.md): latest governance review and test evidence
- [`ARCHITECTURE.md`](ARCHITECTURE.md), [`docs/CONTINUOUS_OPERATION.md`](docs/CONTINUOUS_OPERATION.md), [`docs/SECURITY_AUDIT.md`](docs/SECURITY_AUDIT.md)
- [`docs/AZURE_DEPLOYMENT_RUNBOOK.md`](docs/AZURE_DEPLOYMENT_RUNBOOK.md), [`docs/COST_MODEL.md`](docs/COST_MODEL.md)

## License

MIT. See [`LICENSE`](LICENSE).
