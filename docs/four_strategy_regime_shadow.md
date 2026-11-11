# Four-strategy market-regime shadow — research only

November 11, 2026. No strategy, live-paper entry, exit, allocation, scheduler, or broker change is authorized by this project. This layer records potential daily opportunities and absence/quality of evidence in `state/regime_shadow_ledger.json`. It cannot place paper entries, cannot cancel existing trades, and cannot submit broker orders.

Proposed evaluation windows (Central, America/Chicago; exchange clock handles DST):

* ZEBRA: 08:30–09:15; 30–90 DTE equity synthetic-directional, opening range breakout with verified SMA/IV/GEX and option chain.
* SAFE_INCOME: 09:30–10:30; 0/1 DTE range/iron condor/credit spread, verified IV Rank >30, expected-move range and stall.
* SNIPER_SUPPORT: 10:00–12:30; bullish support pullback (RSI <35 or near verified support, macro uptrend, completed rejection). Do not confuse with independently implemented BWB Sniper.
* CREDIT_ROLLS_GROWTH: 14:30–15:00; management/weekly structures requiring real held-position/chain evidence; no new 0DTE entry by assumption.

The *existing* sandbox scan invokes `regime_shadow.observe` with verified index frames, without modifying the frames passed to Low Delta/BWB. Missing IV Rank, major support, 30–90 DTE chain, or held positions means `DATA_UNAVAILABLE`, never `NO_SIGNAL` or a qualifying entry. Evaluation-only observations are not counted as paper trades or wins. `state/shadow_comparison.json` reports independently closed OPTION-001, Low Delta, comparison and BWB paper simulated trades alongside the four-regime shadow research book. W/L, P/L, and closed-trade-sequence drawdown are computed from closed trades; shadow W/L remains N/A until a distinct quote-verified paper execution lifecycle is authorized. Closed-trade-sequence drawdown is not intratrade drawdown.

A current scheduler does not guarantee fresh market data in all four proposed windows (especially Zebra's opening-range window). Do not claim full daily coverage until time-window coverage and fresh provider evidence are verified. The current context is therefore an *observational pilot*, not a validated comparative backtest or deployed trading strategy.
