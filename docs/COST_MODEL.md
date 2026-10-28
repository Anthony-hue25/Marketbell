# Cost model

Measured selected audit usage: {
  "input_tokens": 40169,
  "completion_tokens": 10847,
  "estimated_usd": 0.021813,
  "successful_returned_calls": 20,
  "note": "Bounded audit workload only; interrupted in-flight requests and other historical/local monitoring calls are excluded. Estimates are assumptions, not a Nebius invoice."
}

Pricing assumptions: $0.30 / million input tokens and $0.90 / million completion tokens. These are configurable estimates, not a verified billing quote. Under these assumptions, the selected successful responses estimate **$0.021813** total, or $0.001091 per returned call. In-flight interrupted requests, prior audit runs, ongoing monitoring, hosting, electricity, storage and data licenses are excluded. Reasoning tokens may be included in provider completion usage; no hidden text is stored.

Normal event: two independent calls; up to two attempts per stage. Worker retries resume persisted stages. The configurable per-event ceiling is 12 attempts; daily ceiling 60 attempts. Conservative daily/monthly reservation ceilings default to $0.50 / $10. Reservations use prompt UTF-8 bytes plus an overhead allowance and the maximum 4096 completion tokens; failed/interrupted reservations are not silently released. They are an application control, not an invoice guarantee. Provider-side billing limits remain required for public deployment. Budget windows use UTC.

| Projection | Calls before retry allowance | Estimated model cost at measured mixed-workload mean |
|---|---:|---:|
| Idle news-only day, no justified events | 0 | $0 model inference |
| Completed-session contextual review of four demo securities | 8 | $0.0087 |
| Normal day: five investigated events + baseline + one correction | 19 | $0.0207 |
| Busy day: thirty events + baseline + one correction | 69 requested, capped/deferred at 60 and spend ceilings | at most $0.0654 at this mean, not a billing bound |

The mean mixes small evaluation prompts and larger real-source packages; projections are illustrative capacity estimates. A monthly invoice is not available. Monthly infrastructure cost is **unquoted** because no approved host, paid deployment or production storage plan exists.

News request projection: default four core companies plus four rotating discovery candidates gives roughly 2,304 source requests/day at a five-minute cadence, before source failures, investigator enrichment and user universes. If all 32 companies are actively observed, the theoretical poll count is 9,216/day. These are upper cadence projections, not measured successful coverage or licensed entitlements. Providers may throttle; visible health degrades. No model is called by routine source polling.

Controls: one inference worker; four bounded fetch threads; durable pending notices; holding priority; bounded retries and cooldown; duplicate suppression; per-workspace admission limits; public fresh inference disabled by default in the production template. Budget-exhausted risks remain recorded with explicit queued/failed reasons and are visible when the user returns. No email/push channel has been configured.
