# November 20, 2026 — Server-side Strategy B collector deployment

The authorized `tradebot` Ubuntu host now runs `server_collector.py` through the enabled **user-level systemd** `options-sandbox-collector.timer` (not a ChatGPT automation). The collector has the existing protected `/etc/trade-option-bot.env` credential file as `EnvironmentFile` and uses fixed-host, GET-only requests. No credentials are printed, copied into the project, or backed up to public GitHub.

## Verified real provider behavior

- **Success:** Alpaca IEX SPY five-minute research bars (`data.alpaca.markets/v2/stocks/SPY/bars`), and verified contract lookup for same-day **SPXW** and **XSP** PM-settled roots (`paper-api.alpaca.markets/v2/options/contracts`). Their successful API responses prove connectivity, but IEX volume is single-exchange and **not equivalent to a SIP/consolidated volume regime**.
- **Block:** Alpaca read-only `/v1beta1/options/snapshots?feed=opra` responds `403 OPRA agreement is not signed` under the already installed account. Indicative quotes can exist, but do **not** supply the complete validated Greeks/option selection evidence. Missing option OPRA access must never be replaced by guessed deltas, synthetic prices, or stale Robinhood observations.
- **Block:** Alpaca's index option data offering does **not** include the separately required current, timestamped **cash SPX/XSP index spot**, and no other verified server-side index spot adapter is configured. SPY*10 and indicative option implied values are **not** an acceptable substitute.

`state/server_collector_health.json` records `PROVIDER_EVIDENCE_INCOMPLETE` with independent per-provider checks and the actual blockers. The script deliberately **does not publish `state/incoming_robinhood_batch.json`** or pretend `ALPACA_MARKET_DATA` was collected from `ROBINHOOD_MCP_RESEARCH`. The old `refresh_recovery.py` and scan selector remain unchanged, and cannot create trades without authenticated, compatible, complete provider data. All order submission is disabled.

## Schedule and failure transparency

`options-sandbox-collector.timer` runs 15 seconds before these Chicago-time checkpoints: 10:00, 10:15, 10:20, 10:30, 10:45, 11:30, 12:15, 14:20, 14:25. Each run is one GET-only preflight, not yet a successful entry scan. `options-sandbox-observer.timer` still checks exact required trading windows, rejects old quotes, and escalates any request older than two minutes to `REFRESH_OVERDUE_AUTHENTICATED_COLLECTOR`. Its diagnostic now exposes `SERVER_PROVIDER_EVIDENCE_INCOMPLETE`, exact provider gap codes, and the collector's timestamp rather than a generic wait.

## Before autonomous SPX/XSP live-market **paper** entries

1. The account owner must complete OPRA data agreement/entitlement for Alpaca. Reprobe the specific SPXW and XSP same-day quotes and verify quote timestamps, bid/ask sizes, execution legality, and genuine delta/Greek coverage for all selected shorts and protective wings.
2. Configure a legally authorized provider and authenticated credentials for real **cash SPX/XSP index spot** (not an ETF proxy). Validate source and timestamps for both products.
3. Once these pass, explicitly add a `ALPACA_MARKET_DATA` normalization adapter matching the existing strategy's index metadata and exact-leg quote schema, **without** rewriting the source to Robinhood; regression-test two-batch natural fills, event gates, index-expiration conditions and $400 per-book maximum defined risk.
4. Run a complete verified session scan in a test book; only enable the collector-to-scan auto-handoff after cross-provider consistency, option leg identities and no-live-orders assertions pass.

Do not remove this fail-closed preflight merely to obtain a green status. The new schedule **cannot** repair missing API entitlements by itself.
