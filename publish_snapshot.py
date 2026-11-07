"""Publish one fresh Robinhood batch for the Strategy B observer."""

import argparse
from datetime import datetime, timezone
from pathlib import Path

from observe import frames_from_snapshot, read_json, write_json
from replay import iso, quotes_at, spot_at
from robinhood_capture import PRODUCTS


def prepare(raw, session, now, enrich_contract):
    if (session.get("source") != "ALPACA_CALENDAR"
            or session.get("is_trading_day") is not True):
        raise ValueError("Verified Alpaca trading session required")

    symbols = {}
    for symbol in PRODUCTS:
        rows = raw["symbols"][symbol]["contracts"]
        contracts = []
        for row in rows:
            try:
                contracts.append(enrich_contract(symbol, row))
            except (ValueError, ArithmeticError, TypeError):
                continue
        symbols[symbol] = {"contracts": contracts}
    snapshot = {
        "source": "ROBINHOOD_MCP_RESEARCH",
        "research_only": True, "paper_only": True,
        "order_submission_enabled": False, "broker_execution_enabled": False,
        "live_trading_enabled": False, "generated_at_utc": iso(now),
        "symbols": symbols, "market_context": raw["market_context"],
    }
    frames, status = frames_from_snapshot(snapshot, session, now)
    if status != "FRESH" or {frame["symbol"] for frame in frames} != set(PRODUCTS):
        raise ValueError(f"Incomplete snapshot: {status}")
    for frame in frames:
        if spot_at(frame, now) is None or not quotes_at(frame, now):
            raise ValueError(f"No fresh index or contract quote for {frame['symbol']}")
        if frame.get("signal_at") is None:
            raise ValueError(f"No current SPY bar context for {frame['symbol']}")
    return snapshot


def main():
    from option_bot.contract_metadata import enrich_contract

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("input", "session", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    snapshot = prepare(read_json(args.input), read_json(args.session).get("session", {}), now, enrich_contract)
    write_json(args.output, snapshot)
    print(f"Published verified read-only quotes at {iso(now)}")


if __name__ == "__main__":
    main()
