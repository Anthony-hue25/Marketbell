# MarketBell watches continuously; it thinks selectively

The primary trigger is a meaningful development, not the market close. Completed-session screening is one source of scheduled intelligence. News/event noticing and model processing use independent threads under a singleton process lock. Source monitoring continues while an inference call is slow, and no browser must remain open.

## Four operating channels

| Channel | Detection and cadence | Actual access state in this build |
|---|---|---|
| Market hours | Public news monitoring detects material company developments and reported price moves; default 300 seconds | News statements are attributed to their sources; no direct intraday quote or volume feed. |
| Outside market hours / all day | Public news RSS every 300 seconds, issuer evidence enrichment on investigation; authenticated event webhooks | News monitoring verified. Webhook flow verified with controlled delivery. Direct SEC and intraday price polling are outside the product scope. |
| Scheduled intelligence | Hourly deterministic portfolio health/exposure review; completed-session four-security opportunity review, checking exchange calendar every 30–60 seconds | Implemented and run. Completed sessions maintain context; they do not gate event investigations. |
| User-triggered | Explicit research request, demo holding changes, corrections and standing-rule management | Implemented behind session/CSRF checks. User changes trigger bounded review jobs and fresh policy checks. Private account import remains disabled. |

Lightweight detectors do not call Nemotron. They persist cursors, suppress repeated source IDs/normalized headlines, prioritize holding risks, and queue a bounded investigation only when source information changes. Research and blind Challenge then interpret materiality; they may conclude that a noticed event is routine or too uncertain. A queued notice is never labelled a completed action.

## Persistence, recovery and cost

SQLite migration v2 adds queue priorities. Source cursor updates and notice queue insertion are atomic. Failed provider/schema/citation jobs have bounded delayed retries; budget-exhausted jobs can resume after a new UTC budget day. Interrupted jobs preserve completed Research stages. Source lookback can recover unseen information after downtime, within the source's actual returned window. The RSS connector currently returns at most five headlines/security within seven days; it cannot guarantee complete recovery of every missed report.

Public-source investigations can be reused for identical evidence across subscribed demo workspaces. Each workspace separately re-evaluates current holdings, cash/exposure policy, standing rules, expiry and revocation, then persists its own outcome. Personal monitoring can be paused. No personal authority is inherited from the shared demonstration portfolio.

No continuously running inference loop exists. Default source cadences are configurable; the global production database has a 60-attempt/day model limit, at most two attempts per model stage, and pending-work limits. Routine source polling consumes no model quota. When source permissions or quota are unavailable, monitoring status reports the degraded channel rather than inventing coverage. A qualitative event can still be investigated without a current price refresh, but missing price context blocks preparation through the same governor.

## Browser-closed acceptance evidence

`docs/CONTINUOUS_ACCEPTANCE_PROOF.json` records an actual off-hours run with the app tab closed. An authenticated source emitter delivered a previously unseen event to the running service. It used a **preserved real issuer announcement in a controlled delivery**, not a claim of naturally breaking news. The backend queued the event, ran fresh Nemotron Research and blind Challenge, applied policy, persisted a HOLD because the independent reviews disagreed, and suppressed a duplicate delivery without additional model calls. The latest completed session was unchanged throughout. The record survived database reopen and was visible on return.

This demonstrates event-triggered operation independently of browser activity or a market close. It does not demonstrate guaranteed uptime, full-market coverage or a newly published real-world press release. The actual local service operates while its process and machine remain running. The prepared production container supports a continuously running service, but public infrastructure deployment is not yet approved or verified.

## Webhook contract

Configure a dedicated `MARKETBELL_WEBHOOK_TOKEN` (at least 24 random characters), or a private mounted `MARKETBELL_WEBHOOK_KEY_FILE`. Do not put the token in a URL, frontend, image or log. The temporary local test token was revoked after acceptance.

`POST /api/webhook`, `Authorization: Bearer <dedicated token>`, JSON:

```json
{
  "symbol": "PSX",
  "event_id": "source-stable-event-id",
  "published_at": "2026-10-14T21:30:00+00:00",
  "source": "Authenticated integration name or public source URL without credentials",
  "text": "The source's bounded event text. Source content is always untrusted data."
}
```

The response is an accepted job ID, not an action-completed claim. Repeating the same source/event ID returns the same job. The supported universe is currently PSX, VLO, NVDA and JPM. Source entitlements, issuer identifiers, and completeness must be extended and verified before claiming broader production coverage.
