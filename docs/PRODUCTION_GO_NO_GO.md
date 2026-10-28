# Production go / no-go (final verification 2026-10-20; product update 2026-10-21)

## Verdict

**Not yet GREEN FOR STAGING — one item left.** The authentic real-provider check has now **passed** (2026-10-20).
Remaining: the container checks on the official base image (build on `python:3.12-slim` + Trivy), which run on
the first GitHub CI build. No P0/P1 defect is open.
**Not GREEN FOR PRODUCTION** — that requires a passed Azure staging deployment, which has not happened.

**2026-10-21 product update** (new default portfolio, multi-source news with publisher tiers, quiet log,
plain-language blocks, token savings, Ask MarketBell): 201 tests pass on SQLite and PostgreSQL 16 (after the 2026-10-21 build-lessons audit, `docs/ADAPTIVE_UNIVERSE_LESSONS.md`); container
smoke 13/13 and an end-to-end triage run pass locally. Because news retrieval changed, **re-run the live check
once** (same command) before staging; the 2026-10-20 runs below exercised the previous single-channel retrieval.

## Scorecard

| Category | Item | Status |
|---|---|---|
| **Verified locally** | 201 tests (2026-10-21): pass on SQLite and on your own folder (Python 3.10, after sync); 198 pass + 3 SQLite-only skips on PostgreSQL 16 | ✅ |
| | 10 reproduced defects fixed, identical results on SQLite and PostgreSQL (`ASSURANCE_REPRODUCTION.json`) | ✅ |
| | Fixes re-tested through production code paths: follow-up job with a split, RSS parser + scan for wrong company, watch-event timestamps, two started workers (`RealPaths`) | ✅ |
| | Production image (substitute Ubuntu 24.04 / Python 3.12 base): start, health, smoke 13/13, SIGTERM exit 0 for API and worker, fault suite T1–T14 (2026-10-20); rebuilt 2026-10-21: smoke 13/13, triage end to end (Reuters item investigated, commentary set aside), SIGTERM exit 0 | ✅ |
| | Live-check ceiling enforced: limits forced over shell/.env, refused before the provider is called (tests) | ✅ |
| | Dependency audit (pip-audit) clean; bandit 0 medium/high | ✅ |
| | gitleaks: Git history clean (3 commits); working tree only third-party false positives in `.venv` | ✅ |
| **Verified with authentic external services** | `live_bounded_check` run twice on your machine, 2026-10-20 00:33 UTC: **both passed** (evidence file holds the second run). Google News reachable for all 4 symbols (20 items, 19 identity-matched; publishers incl. Reuters, CNBC, Business Wire). NVDA headline (24/7 Wall St., published 2026-10-19 16:00 UTC) → real `nvidia/nemotron-3-super-120b-a12b` Research and blind Challenge, 2 calls per run, 0 retries, 2.6–4.1 s and 3.6–4.4 s; ~1,000 input and 355–757 output tokens per call; schema + citations valid; both `hold`; deterministic policy `blocked` (no verified price baseline; NVDA 7 % vs 5 % limit) — identical outcome both runs; persisted, journal verified after reopen; listed by `/api/state` without transcripts. Cost $0.0014–$0.0019 per run at list rates (reserved $0.0104; ceiling $0.05). Evidence: `docs/evidence/LIVE_BOUNDED_CHECK.json` | ✅ |
| | Earlier sessions' authentic evidence (`CONTINUOUS_ACCEPTANCE_PROOF.json`, recordings) | Historical only; not re-verified |
| **Validated as infrastructure code** | Bicep 0.48.1 build + lint + both parameter files (0 errors) | ✅ |
| | GitHub workflow (actionlint 1.7.12: clean) | ✅ |
| | `deploy.ps1` (PowerShell 7.6.7 parse + dry run) and `deploy.sh` produce identical `az` calls; non-digest images refused | ✅ |
| | `az deployment what-if` | ⛔ Needs an Azure login and resource group |
| **Not yet verified on Azure** | Key Vault references, managed-identity database login, outbound egress, probes, revision overlap, alerts, PITR restore, billing meters | ⏳ Staging |
| **Blocked by permissions / authorization** | Official `python:3.12-slim` build + Trivy (Docker Hub and vulnerability DBs blocked here; runs in CI) | ⛔ First CI run |
| | Nebius key rotation | ⛔ You |
| | Azure subscription access (RG Contributor + constrained RBAC Administrator) | ⛔ Subscription admin |

## Gate status

| Gate | Status |
|---|---|
| GREEN FOR STAGING: local checks | ✅ |
| real-provider checks | ✅ passed 2026-10-20 |
| security and container checks | ⚠️ local done; official base + Trivy pending CI |
| infrastructure validated | ✅ (what-if pending Azure access) |
| no known P0/P1 | ✅ |
| GREEN FOR PRODUCTION | ⏳ after staging passes cloud auth, persistence, autonomous worker, recovery, security, performance and rollback tests |

## What turns this green for staging

Live check ✅ done. Remaining: confirm the Nebius key was rotated (A1) and one green CI run (A3) of
`docs/STAGING_RELEASE_CHECKLIST.md`. Then the staging deployment itself (sections B–F).
