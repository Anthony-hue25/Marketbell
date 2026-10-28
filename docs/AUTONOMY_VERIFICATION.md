# Autonomy verification

Result: **PASS for the tested local background-worker paths**. This is not an uninterrupted 24/7 uptime or source-coverage claim.

The normal worker accepts authenticated controlled delivery through `/api/webhook`; detection, deduplication, triage, Research, blind Challenge, policy resolution and persistence remain in the application path. The controlled input is a preserved real issuer announcement labelled as controlled delivery, not naturally breaking news.

Verified process IDs before/after interruption: 1624 → 28180. The actual interpreter process identity and unique instance nonce were checked. Research was persisted before termination and its trace hash was unchanged on resume. The job required two attempts and produced one final outcome. Duplicate delivery reused the job identity. There were zero browser sessions during processing; no frontend was opened on the isolated test service. The normal state API displayed the persisted result afterward.

A separate, authentic PSX headline was fetched from the configured live public RSS source and fed unmodified through `scan_watch`; it completed real inference while the exchange was closed. Exact source/retrieval/publication evidence is in AUDIT_AUTONOMY_PROOF.json. Public RSS connectivity is not proof of commercial redistribution rights or exhaustive coverage.

Temporary network failures, successful empty polls, durable budget deferral, duplicate arrivals, partial-stage failures, immutable history, portfolio changes and restart recovery have automated regressions. Large bursts use mocked inference and are labelled as such. Personal review now follows durable opted-in portfolio records rather than browser-login state. A no-session regression verifies this distinction.

The scheduler maintains 7/30/90-calendar-day review jobs and uses the exchange calendar for appropriate completed-session price observations. It does not wait for market close before investigating news. Neither direct SEC polling nor intraday price polling exists. News price claims remain attributed assertions.

Operational requirements: keep the backend process/service running, persist SQLite and its volume, configure source access and dedicated deployment inference credentials, monitor the restricted diagnostics endpoint, and verify process supervision on the production host. Local laptop sleep or power loss prevents monitoring; restart backfill is bounded by source lookback and cannot guarantee every missed event.
