# Options Strategy Sandbox 0.1.4

Standalone paper research for Strategy B, Low-Delta Income. The replay compares 0.10–0.15 short delta with a 0.15–0.25 comparison band targeting 0.175. The comparison represents the existing delta preference only. Both profiles run the same experimental rules and see the same observations. Each has its own $500 simulated account and combined $100 open defined-risk budget across SPX and XSP, including costs.

The project uses Python 3.11+ and the standard library. It runs entirely from this directory with local input files and local paper ledgers. It has no production imports, broker dependencies, credentials, or order endpoints.

## Run

```bash
python3 -m venv --without-pip .venv
.venv/bin/python -m unittest discover -s tests -q
.venv/bin/python run.py data/synthetic_xsp.json
```

The synthetic example checks quote-to-fill and settlement mechanics. It is labeled `SYNTHETIC_VALIDATION`. The command writes an immutable paper ledger under `runs/`. The engine and dataset hashes identify the run. Repeating the same inputs produces the same ledger and detects unexpected changes to that ledger. Input errors exit with status 2.

To inspect an exported archive without writing to it:

```bash
python3 archive_audit.py /path/to/copied/robinhood-archive
```

## Robinhood quote verification

```bash
python3 robinhood_capture.py data/robinhood_dry_run_2026-11-05.json
python3 robinhood_capture.py data/robinhood_context_check_2026-11-06.json
```

The capture file contains exchange-calendar rows, Robinhood chains and instruments
for SPXW and XSP, and at least two quote batches with their actual receipt times.
Resolve the PM chain and exact instrument IDs before the entry window. Collect
the same IDs twice through the read-only quote connection. Preserve the provider's
quote timestamp for each contract. The adapter validates chain membership and
uses Cboe product specifications and the supplied market calendar for the
European exercise, PM cash settlement, and expiration-day cutoff.

The verifier reports usable quote counts and whether every contract received a
new usable quote within 60 seconds of the first batch. The scheduled quote-only smoke test
requires both batches in its separate 11:00–11:05 a.m. Central window. The bundled Nov. 5
sample tests the connector handoff outside the entry window. The verifier also
emits normalized replay frames with decision clearances disabled and zero
paper entries.

A capture batch can also carry Robinhood's index quote rows in `index_quotes`
and its regular-session SPY five-minute historical result in `chart`. Use the
index venue timestamp for the 60-second spot check. The sandbox uses the actual
SPX or XSP quote for strike comparisons and SPY only for chart context.
The candidate bullish rule requires a completed SPY close above rising session
VWAP following a cross or support retest. Missing, stale, and incomplete bars
cannot establish that signal. The capture tool keeps event clearance disabled
and supplies no neutral-range clearance. Forward paper testing still requires
event clearance, exit or official settlement collection, and a scheduled
capture path for these inputs.

## Initial historical coverage

The read-only audit of top-level Option Bot Robinhood research exports found **79 files, 147 distinct SPX/XSP observation timestamps by symbol, and 21 dates** through November 5, 2026. There were **zero observations in the original 11:00–11:05 a.m. Central test window**. This audit does not certify coverage for the new entry window. The exact counts, scope, and most recent snapshot per date are in `data/archive_coverage.json`. File counts may include multiple copies of a symbol observation, so the audit reports distinct symbol and timestamp pairs separately. The archive audit uses capture timestamps and does not certify quote freshness or completed session coverage.

An independent Massive historical options NBBO request returned `NOT_ENTITLED` for the connected plan. The Alpaca calendar probe timed out. Historical win rate, P&L, and drawdown remain unavailable. The sandbox includes a synthetic ledger to demonstrate execution and accounting. The research policy remains promotion-ineligible.

## Replay input

Supply a JSON object with `kind` (`HISTORICAL` or `SYNTHETIC_FIXTURE`), `source`, `frames`, optional `settlements`, and optional `as_of`. Timestamps include offsets. Use `data/synthetic_xsp.json` as the schema example.

- A frame identifies its `at`, `symbol`, verified regular `session` date/open/close, `spot` and `spot_at`, `signal_at`, `direction`, and explicit `technical_clear`, `range_clear`, and `event_clear` flags. Historical direction, clearance, and underlying observations must be available at the frame time.
- Each quote supplies contract ID, symbol, root, expiration date, strike, P/C, European exercise, PM cash settlement, 100 multiplier, last trade and settlement timestamps, verified contract flag, bid/ask, sizes, original quote timestamp and signed delta. Missing long-leg delta is allowed. The short leg needs delta for entry and fill eligibility. SPXW and XSP are the pilot roots.
- Settlement records match root, expiration, and settlement timestamp and require the official value, provenance, verification flag, and time the value was available. Missing values leave the position pending. A run that ends before the last trading cutoff lists the position as open at end. Risk stays reserved for unresolved positions.
- Frames sort by timestamp with deterministic SPX/XSP ordering. Exact duplicates collapse. Conflicting records for the same timestamp/symbol or same settlement identity reject the dataset. Invalid or stale quotes cannot fill. The 0–1 DTE filter uses New York calendar dates.

## Experimental rules

Use a verified open session and allow entry signals and simulated fills from 10:00 a.m. to before 11:00 a.m. Central, after OPTION-001's last scheduled entry scan. The 10:30 a.m. Central cutoff is an independent shadow replay on the same inputs for each delta profile. Its separate ledger results are observational. Bullish signals qualify for OTM put spreads and verified neutral range signals qualify for iron condors. Use one contract per leg and at most two entries per session. The entry order expires after 60 seconds. A simulated fill requires newer executable quotes for every leg, a still-eligible short delta and strike, and the original credit or better. Sell at bid and buy at ask. Quotes older than 60 seconds and future quotes are rejected. Use a 15% minimum net return on maximum loss. Reserve $0.65 per leg per side, including the exit fee reserve on settlement.

A tested short strike, buyback cost at 2× opening credit, or 50% gross credit capture queues an exit. On the expiration day, remaining positions queue an exit five minutes before the 2:30 p.m. Central deadline or an earlier verified expiration-day cutoff. A position that misses the deadline is reported as a breach and remains eligible for exit attempts until trading ends. Unclosed expired positions require verified cash settlement. Execution requires newer quotes before the verified trading cutoff. An invalid executable exit debit outside the structure’s payoff range remains pending. The report exposes missing marks and pending settlement. Maximum marked drawdown covers available complete observations and does not estimate unobserved periods. Reported realized P&L includes simulated costs.

## Extensions and promotion

`data/strategy_catalog.json` registers the four approved research strategies and the future equity-weekly profile. ZEBRA, Sniper, Bull Put Rolls, and weekly-equity scanning are definitions for later implementation. Weeklies will use a separate contract universe, liquidity and corporate event rules, expiration windows, and American exercise, share settlement, and assignment workflows.

Promotion requires historical and forward paper evidence for the frozen strategy, explicit approval of the integration diff, a new Option Bot release, regression checks for the prior strategies and Barney, and a tested rollback. This package is the independent first research phase.

## Scheduled Strategy B observation

The dedicated DigitalOcean user timer runs every minute from 9:25 a.m. through
11:00 a.m. Central on weekdays. It reads the existing Robinhood research
snapshot and the verified Alpaca session. Both paths are specified in the
service unit. The producer retains control of its publishing cadence. The
observer validates the envelope timestamp, actual index timestamps, individual
option quotes, completed SPY bars, and same-day PM option contract metadata.

The observer checks official BLS CPI and Employment Situation releases and Fed
scheduled meeting dates. It applies an inclusive 30-minute window on either
side of each release, the scheduled 2 p.m. Eastern statement, and the standard
2:30 p.m. Eastern press conference. Calendar data must be verified on the
same Eastern date and be less than two hours old. The observer retries failed
refreshes on the next tick. A missing source is distinguished from a blackout.

`state/latest.json` reports the current health and candidate decisions and
the user service journal records each scan. The scheduled observer validates
same-day expirations only. It ranks candidates without opening positions.
The offline replay remains the entry/exit simulator. Automatic paper fills
and 1DTE evaluation require recurring fresh quotes and verified exit,
expiration-session, and settlement coverage.

## Autonomous Strategy B paper trading (November 9, 2026)

The Strategy B sandbox now runs **local simulated purchases**, not just an
observer. `scan_paper.py` is the supervised entry point. It uses the same
fail-closed architecture as OPTION-001, without importing OPTION-001 trading
rules or writing its files:

1. Select the newest provider-published, read-only Robinhood research snapshot
   from the sandbox and (as an optional read-only fallback) OPTION-001.
2. Reject snapshots older than 60 seconds, stale index quotes, missing SPY
   chart confirmation, incorrect Cboe exercise/settlement metadata, and option
   quotes lacking current Greeks, IV, bid/ask or size.
3. Recheck the independent official BLS/Fed blackout calendar and verified
   Alpaca exchange session. No passed entry gates are fabricated.
4. Keep the original experimental **0.10–0.15** versus **0.15–0.25** delta
   strategies unchanged. Each has its own $500 simulated account and $100
   combined SPX/XSP defined-risk ceiling; opening a spread requires TWO actual
   fresh quote observations (the second has newer executable leg quotes).
5. Save verified frames to `state/forward_frames.json` and recompute a durable
   idempotent `state/paper_ledger.json` and `state/paper_events.json` containing
   simulated BUY/SELL events, exit reasons, open exposure, P/L and win rates.
   Missing settlement remains **pending** instead of inventing a realized gain.
6. When anything essential is stale, atomically publish
   `state/refresh_request.json` with `PENDING_CONNECTOR_REFRESH`, exit 66,
   and do not open or close an unsupported simulated position.

There are **zero real orders**: brokerage submission, broker execution and live
trading are always disabled. The independent experimental script `replay.py`
remains authoritative for strategy selection, fee model, technical gates,
quote-to-fill timing, early exit triggers and settlement modeling.

The installed user-systemd timer has FOUR daily weekday checks at 10:10 a.m.,
10:25 a.m., 2:25 p.m. and 2:30 p.m. **America/Chicago**, not once per minute.
The existing ChatGPT read-only Robinhood refresh task is scheduled once at 10:05 a.m. Central and attempts two new quote
batches where available and invokes the sandbox's local `scan_paper.py` after
each. These scheduled local scans do not themselves possess Robinhood
credentials or directly fetch the read-only connector: the explicit refresh
request requires the external data publisher to respond. A fresh capture alone
never guarantees an eligible trade, a fill, or a verified settlement.

Smoke test: `python3 -m unittest discover -s tests -q` and
`python3 scan_paper.py` (after hours, this must request fresh connector data and
must not create a paper purchase). The simulator can be tested independently
with `python3 run.py data/synthetic_xsp.json`; that fixture is NEVER used for
scheduled purchases.

The strategy entry window starts at **10:00 a.m. CT**, at least 30 minutes after OPTION-001's last scheduled 10:30 a.m. ET (9:30 a.m. CT) entry scan. The sandbox first probes OPTION-001's existing process-held `flock` lock without retaining it; a busy or inaccessible lock defers sandbox activity and prevents simulated entries or connector calls. Low Delta has a **$500 paper allocation**, and its experimental 0.15–0.25 comparison has an independent **$500 hypothetical accounting book** (not a second real allocation). Neither may commit more than $100 in simultaneous defined risk. The two books are comparisons, not two real accounts or broker orders.

## Connector refresh-recovery: OPTION-001 pattern (November 10, 2026)

`refresh_recovery.py` is the sandbox's local **publish → validate → resume**
controller. It mirrors the *data-recovery protocol* used by OPTION-001 while
leaving all sandbox indicator, spread selection and risk rules in `replay.py`.
A connected authorized Robinhood **read-only** workflow obtains actual option
instruments/quotes, SPX/XSP index levels, and SPY regular-session bars, then
passes them as a unique provider-data batch to:

```bash
cd /home/tradebot/Options-Strategy-Sandbox
PYTHONPATH=/home/tradebot/Trade-Option-Bot/src \
 python3 refresh_recovery.py --input state/UNIQUE_FRESH_RAW_BATCH.json
```

It first defers if OPTION-001's current process lock is occupied. Otherwise it
calls the existing metadata-aware `publish_snapshot.py` (atomic output),
verifies the envelope and true original timestamps of leg/index quotes, runs
`scan_paper.py`, and checks the new report's time and `feed_status=FRESH`.
Successful cycles record `state/refresh_recovery.json` status `RESUMED` and
clear stale refresh requests. Missing/stale data fails closed with
`REFRESH_RETRY_REQUIRED` and a durable request. No broker credentials or live
order endpoints exist in this path. No paper entry is authorized after 11 a.m.
Central, but read-only recovery and management may run until the session ends.

The connected Robinhood automation now collects fresh data at **10:20 a.m.
and 2:20 p.m. Central** on trading weekdays and invokes this recovery command;
it may obtain a second new quote batch during the same invocation if the first
qualifies a position. This avoids per-minute ChatGPT calls. The local user
systemd timer retains its own checks, which may correctly report stale input if
no new authorized connector snapshot has arrived in the past 60 seconds.

Live-market proof on Nov. 10: both a 1:23 p.m. Central and 1:25 p.m. Central
management-only recovery returned `RESUMED`, verified current actual SPX/XSP
quotes plus SPY bars, and added new forward observations. No positions existed,
so the verified test did NOT exercise an actual held-leg exit or entry fill.

## Sniper 14–30 DTE equity options — independent experiment (November 11, 2026)

`sniper.py` and `sniper_cycle.py` are the independent **Sniper** evaluator and
paper ledger. This strategy is NOT a variation of SPX/XSP 0DTE Low Delta.
It evaluates SPY/QQQ **14–30 DTE** exchange-traded equity options with either:

- Bullish completed five-minute candle breakout above the preceding completed
  high, a rising EMA20, and SPY/QQQ above its verified same-session VWAP;
  analyze an **OTM or ATM long-call / farther OTM short-call debit spread**.
- Bearish completed five-minute candle breakdown below the preceding completed
  low, a falling EMA20, and underlying below VWAP;
  analyze a **long put / farther OTM short-put debit spread**.

The independent experimental account is $500 simulated, with a hard $100
simultaneous defined risk cap including modeled opening/closing commissions.
Candidates require a natural (buy ask / sell bid) positive debit and at least
1:1 modeled max-profit / max-loss. Contract metadata must verify *American
exercise and physical settlement*, current executable sizes, open interest,
volume, IV, true provider timestamps, and a verified 14–30 DTE expiration.
Upcoming issuer dividends and earnings/event clearance must be explicit
`CLEAR`; unknown is a blocker. Physically settled options may not be treated
as SPX cash settlement. Position management targets 50% of maximum profit,
50% of maximum defined loss, or a close by the day before expiration, using
fresh quotes; insufficient quotes remain flagged, not fabricated as fills.

Run daily after OPTION-001 (the existing sandbox timer already runs at 10:10
and 10:25 Central). `scan_paper.py` always calls `sniper_cycle.run` and writes
`state/sniper_daily_evaluation.json` and `state/sniper_paper_ledger.json`.
The connected Robinhood refresh task now requests verified equity market data
separately in `state/sniper_equity_research.json`; its exact input schema is
exercised in `tests/test_sniper.py`. If no provider-published equity data is
available the evaluator records `DATA_UNAVAILABLE` instead of silently
skipping or reusing SPX quotes. A qualifying candidate becomes an actual local
**paper BUY** only upon a second independently newer, valid quote set within
60 seconds; the first quote batch alone creates a pending candidate, never a
fill. This experiment sends **ZERO** live broker orders. Sniper has its OWN
paper-ledger/account and does not change Low Delta parameters or reports.

Sniper's initial implementation is a simple **breakout debit-spread research
hypothesis**, chosen for comparison; the previously registered Sniper catalog
contained no concrete indicator, strike, or risk formula. Do not call it a
validated or profitable strategy until real-provider forward trades exist.

Corporate action proof additionally requires a timestamped `events.verified_at_utc`
(<=1 hour old), a truthful `events.provenance`, and
`events.clear_through_date` covering the contract expiration. If missing, the
Sniper paper trade is rejected. ETF earnings exemption does not waive an
issuer-distribution/ex-dividend date check. A future management quote failure
must never be counted as a successful simulated sale.
