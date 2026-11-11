# SNIPER_BWB_0DTE — paper-only experimental specification (2026-11-11)

**Status:** Local evaluator, two-quote-batch paper lifecycle and independently
isolated accounting installed; fresh production provider integration pending.
The previous 14–30 DTE equity Sniper experiment is paused. OPTION-001, Barney,
and the independent Low Delta books remain unchanged.

## Limits

- SPXW PM / XSP European-style cash-settled index options only, 0 DTE.
- One four-contract broken-wing butterfly per verified day, three strikes,
  one long, two short at the body, one long; same right/expiry/settlement.
- Max single paper purchase debit INCLUDING modeled initial four-leg fees $500.
- Max expiration defined risk INCLUDING modeled $5.20 round-trip fees $500.
- Entry window 10:00 to strictly before 12:30 p.m. America/Chicago.
- Stop at 50% max initial risk, profit at 50% modeled max profit, mandatory
  exit target at 2:25 p.m. America/Chicago with newer executable quotes.
- If unfilled exit or missing official expiration settlement, unresolved means
  unresolved; NEVER fabricate cash settlement or a successful close.
- 2 successive independent timestamp-increasing actual quotes on same leg
  UUIDs required for opening; natural bid/ask, size >=2 at butterfly body.
- Source is a read-only provider snapshot; broker submissions are forbidden.

## Direction and target

A completed NY RTH SPY 5m VWAP, true EMA50 slope after >=53 completed
5m bars (history from a prior completed session), current SPY equity quote and
fresh matching SPX/XSP index quote, and directional 5m relative volume >=1.
A bar-volume-at-typical-price 0.50-SPY-point bin is an approximate volume
price profile ONLY, never actual exchange volume-at-price. Fallback target:
actual completed 20-bar swing high/low; target must be beyond current spot.
Conversion to index strike units uses contemporaneous verified INDEX/SPY spot
ratio; NEVER a hardcoded 10x. No synthetic SPX history. Gamma exposure is an
OPTIONAL separate delayed research overlay and never invented or assumed fresh.

## Status and runtime

- `bwb.py`: quote selection, four-contract payoff and $500 gates.
- `bwb_context.py`: verified higher-timeframe/time-specific evidence construction.
- `bwb_cycle.py`: isolated durable daily evidence and paper purchase/exit ledger.
- `state/bwb_daily_evaluation.json`: latest daily decisions/rejection reasons.
- `state/bwb_paper_ledger.json`: paper executions and complete position events.
- `scan_paper.py`: always evaluates BWB separately when source data available;
  Low Delta's replay inputs are unchanged.
- Sandbox user timer includes 11:25 and 12:25 CT evaluation attempts.

**Provider blocker verified November 11:** Most recent sandbox snapshot had 23
same-day SPY 5m bars and NO independently verified SPY equity quote; requires
current prior-session bars, equity spot, SPX/XSP eligible multi-leg call/put
quote surfaces. No BWB paper purchase was recorded. Existing ChatGPT automation
refresh update returned an error; its schedule/prompt needs successful update
before automated 11:20/12:20 refresh can be claimed operational.

This is a hypothesis test, NOT a claim that gamma/volume levels predict price.
