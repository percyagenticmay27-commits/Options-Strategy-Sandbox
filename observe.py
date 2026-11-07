"""Evaluate Strategy B from read-only DigitalOcean research feeds."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from event_calendar import clearance, refresh
from replay import (CT, ENTRY_CUTOFF, ENTRY_START, OBSERVED_CUTOFF, PROFILES, RISK_CAP, economics,
                    entry_gate, iso, market_open, money, number, quotes_at, recent, selection,
                    spot_at, timestamp)
from robinhood_capture import PRODUCTS, RIGHTS, quote_frames

VERSION = "0.1.4"
DISABLED = ("order_submission_enabled", "broker_execution_enabled", "live_trading_enabled")


def read_json(path):
    try:
        result = json.loads(path.read_text())
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path, result):
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(".tmp")
    staging.write_text(json.dumps(result, indent=2) + "\n")
    staging.replace(path)


def frames_from_snapshot(snapshot, session, now):
    if (snapshot.get("source") != "ROBINHOOD_MCP_RESEARCH"
            or snapshot.get("research_only") is not True or snapshot.get("paper_only") is not True
            or any(snapshot.get(flag) is not False for flag in DISABLED)):
        return [], "SNAPSHOT_UNAVAILABLE_OR_UNVERIFIED"
    if not recent(snapshot.get("generated_at_utc"), now):
        return [], "SNAPSHOT_STALE_OR_FUTURE"
    if not market_open({"session": session}, now):
        return [], "SESSION_UNVERIFIED_OR_CLOSED"

    contracts, raw_quotes = {}, []
    for symbol, (root, metadata_source) in PRODUCTS.items():
        for row in snapshot.get("symbols", {}).get(symbol, {}).get("contracts", ()):
            try:
                key = row["id"]
                quote = row["quote"]
                if (not key or row["instrument_id"] != key or quote["instrument_id"] != key
                        or row["chain_symbol"] != root or row["underlying_type"] != "index"
                        or row["expiration_date"] != session["date"]
                        or timestamp(row["sellout_datetime"]) != timestamp(session["close"])
                        or row["settle_on_open"] is not False or row["exercise_style"].upper() != "EUROPEAN"
                        or row["metadata_provenance"]["source"] != "CBOE_PRODUCT_SPECIFICATION"
                        or row["state"] != "active" or row["tradability"] != "tradable"
                        or number(row["trade_value_multiplier"]) != 100):
                    continue
                contracts[key] = {
                    "id": key, "symbol": symbol, "root": root, "expiry": session["date"],
                    "strike": row["strike_price"], "right": RIGHTS[row["type"]],
                    "exercise": "EUROPEAN", "settlement": "PM_CASH", "multiplier": 100,
                    "last_trade": session["close"], "settles_at": session["close"], "verified": True,
                    "product_metadata_source": metadata_source,
                }
                raw_quotes.append(quote)
            except (KeyError, TypeError, ValueError, AttributeError, ArithmeticError):
                continue

    context = snapshot.get("market_context") or {}
    charts = [chart for chart in context.get("historicals", ())
              if (chart.get("symbol"), chart.get("interval"), chart.get("bounds")) == ("SPY", "5minute", "regular")]
    batch = {
        "at": iso(now), "quotes": raw_quotes, "index_quotes": context.get("index_quotes", []),
        "chart": charts[0] if len(charts) == 1 else None,
    }
    day = now.astimezone(CT).date()
    return quote_frames([batch], contracts, {day: session}), "FRESH" if contracts else "NO_VERIFIED_CONTRACTS"


def decision(frame, quotes, now, cutoff):
    if not ENTRY_START <= now.astimezone(CT).time() < cutoff:
        return {"status": "OUTSIDE_ENTRY_WINDOW"}
    if frame["calendar_status"] != "CLEAR":
        return {"status": frame["calendar_status"]}
    reason = entry_gate(frame, now, cutoff)
    if reason:
        if reason == "EVENT_OR_TECHNICAL_GATE":
            reason = "TECHNICAL_SETUP_UNCONFIRMED"
        return {"status": reason}
    if not quotes:
        return {"status": "NO_FRESH_CONTRACT_QUOTES"}
    profiles = {}
    for name, band in PROFILES.items():
        candidate = selection(frame, now, quotes, band, RISK_CAP)
        if candidate is None:
            profiles[name] = {"status": "NO_ELIGIBLE_SPREAD"}
            continue
        spread, credit = candidate
        profit, loss = economics(spread, credit)
        profiles[name] = {
            "status": "CANDIDATE", "short_strike": str(spread.shorts[0].strike),
            "long_strike": str(spread.longs[0].strike), "expiry": str(spread.shorts[0].expiry),
            "credit_per_share": str(credit), "net_max_profit": money(profit), "net_max_loss": money(loss),
        }
    return {"status": "EVALUATED", "profiles": profiles}


def observe(snapshot, session, cache, now):
    events = clearance(cache, now)
    frames, feed = frames_from_snapshot(snapshot, session, now)
    by_symbol = {}
    for frame in frames:
        frame.update(event_clear=events["event_clear"], calendar_status=events["status"])
        quotes = quotes_at(frame, now)
        spot = spot_at(frame, now)
        by_symbol[frame["symbol"]] = {
            "usable_quotes": len(quotes), "spot": str(spot) if spot is not None else None,
            "signal": frame.get("signal_reason"),
            "main_1100_ct": decision(frame, quotes, now, ENTRY_CUTOFF),
            "shadow_1030_ct": decision(frame, quotes, now, OBSERVED_CUTOFF),
        }
    for symbol in PRODUCTS.keys() - by_symbol.keys():
        by_symbol[symbol] = {"status": feed if feed != "FRESH" else "NO_VERIFIED_CONTRACTS"}
    return {
        "version": VERSION, "at": iso(now), "mode": "PAPER_RESEARCH_OBSERVATION",
        "paper_entries": 0, "order_submission_enabled": False,
        "calendar": {**events, "sources": cache.get("sources", {}), "errors": cache.get("errors", {})},
        "session_date": session.get("date"), "session_open": market_open({"session": session}, now),
        "quote_feed": feed, "snapshot_at": snapshot.get("generated_at_utc"),
        "main_entry_window_open_ct": ENTRY_START <= now.astimezone(CT).time() < ENTRY_CUTOFF,
        "symbols": by_symbol,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("snapshot", "session", "cache", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    cache = read_json(args.cache)
    if clearance(cache, now)["status"] == "CALENDAR_UNAVAILABLE":
        cache = refresh(now)
        write_json(args.cache, cache)
    session = read_json(args.session).get("session") or {}
    if session.get("source") != "ALPACA_CALENDAR" or session.get("is_trading_day") is not True:
        session = {}
    report = observe(read_json(args.snapshot), session, cache, datetime.now(timezone.utc))
    write_json(args.output, report)
    print(json.dumps(report, separators=(",", ":")))


if __name__ == "__main__":
    main()
