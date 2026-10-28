# Operating cost assumptions

Replay: zero model calls. Browser replay actions still use the app server/database.

Fresh case: one Research and one blind Challenge invocation. Each stage permits at most two attempts and 4,096 output tokens. Corrections use one invocation with at most one retry. The app-wide production database caps attempted model calls at 60 per UTC day. The four-security daily monitor normally needs eight calls, with a worst-case stage-retry allowance of sixteen. Independent local acceptance/evaluation databases have their own test budgets, not a shared billing ceiling.

Illustrative budget assumption, not a billing quote: at $0.30 per million input tokens and $0.90 per million output tokens, a 5,000-input / 1,000-output call is $0.0024; a normal two-call case is $0.0048 and a correction about $0.0024. A 60-call day at 5,000 input and the full 4,096 output allowance would be about $0.3112/day. Reasoning tokens, retry patterns, larger evidence packages and price changes affect actual billing. Fresh traces now retain provider-reported usage when supplied; earlier captures predate that addition.

Confirm current rates in the official Token Factory pricing console before enabling a paid public runtime: https://tokenfactory.nebius.com/ . Hosting, backups, network, storage, domain and licensed market-data costs are excluded; no hosting price is assumed or purchased.

The call cap is an inference-call control, not a hard dollar cap. Public live inference is off by default. For a small judge demo, keep replay as the default and enable fresh inference only with a dedicated approved key and provider-side billing limits. No third-party analytics, advertising or session-replay collection is installed.


Continuous polling itself invokes no model. At default cadences, a four-symbol news scan can make up to 1,152 public RSS requests/day; providers may throttle or reject them, in which case health degrades honestly. Direct SEC and intraday price polling are disabled by design. Entitlement or licensing costs are additional. Notice triage permits at most one new investigation per source scan and a bounded pending queue, under the same daily model budget. Repeated source events do not trigger repeat inference. Public-source analysis reuse across matching demo workspaces adds deterministic per-portfolio checks, not redundant model calls.
