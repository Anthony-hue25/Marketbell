# Infrastructure reuse ledger

The original projects were inspected read-only. No original repository or sealed evidence was modified. `docs/REUSE_INVENTORY.json` contains exact source modules, source hashes, destination hashes, adaptation descriptions and installed dependency/license metadata.

| Component | Reuse | Dependencies carried |
|---|---|---|
| Adaptive Universe `lab/provider.py` | Copied/adapted into server-only vendor provider: working Nebius transport, secure local access, explicit errors, no mock fallback. Added JSON-only responses, bounded output, usage metadata. | requests, python-dotenv |
| `lab/calendar.py` | Copied unchanged: real XNYS sessions and completed-session delay. | pandas, exchange_calendars, numpy, tzdata |
| `lab/alpaca_contract.py` | Copied with relative calendar import: OHLCV validation, pagination, duplicates, session bounds. | calendar dependencies |
| `lab/acquisition.py` | Adapted per-call symbol scoping and explicit process-environment Alpaca credential contract. | requests, parser |
| Trading Agent `src/market_download.py` | Adapted Massive transport to bounded daily bars; reuse existing secure access, no brokerage calls. Existing key returned 401 in verification. | requests, python-dotenv |
| `lab/research_web.py` | Adapted bounded RSS and untrusted-source sanitization; primary issuer enrichment stays on fixed hosts. | requests; standard-library XML |
| `lab/safety.py` | Hash-chain/parent commitment principles adapted into SQLite journal, transaction and readback checks. | Python standard library |
| `lab/forward/governor_v2.py` | Execution-disabled authority and hard-policy precedence adapted into note-only governor. | Python standard library |
| `tests/test_integrity_replay.py` | Offline regression, technical-failure separation and duplicate/recovery concepts adapted. | unittest / Node built-in test |

Data reuse: selected PSX/VLO/NVDA/JPM bars are revalidated from an existing authorized `ALPACA_IEX` download. Original retrieval time, import time, source manifest hash and source CSV hash are retained. Original caches remain read-only; no credentials or private portfolio data are copied. This is labelled preserved data ingestion, never a fresh Alpaca API request.

Licensing: no license file was detected in either original local repository. Treat copied code as internal user-provided source until ownership and public redistribution rights are confirmed. Dependency packages retain their own installed license metadata. Confirm market-data redistribution rights before making recorded bars publicly accessible. No source or data has been published. A public repository license must be selected after those rights are confirmed; MarketBell does not invent an upstream license.
