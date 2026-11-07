"""Validate read-only Robinhood batches against the sandbox replay input contract."""

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from replay import CT, ET, MAX_AGE, compare, iso, market_open, number, quotes_at, spot_at, timestamp


PRODUCTS = {
    "SPX": ("SPXW", "https://www.cboe.com/tradable-products/sp-500/spx-options/spx-specifications/"),
    "XSP": ("XSP", "https://www.cboe.com/tradable_products/sp_500/mini_spx_options/specifications"),
}
RIGHTS = {"put": "P", "call": "C"}
BAR_LENGTH = timedelta(minutes=5)


def calendar_sessions(rows):
    sessions = {}
    for row in rows:
        day = date.fromisoformat(row["date"])
        start, end = (datetime.fromisoformat(row[key]) for key in ("open", "close"))
        start, end = (value.replace(tzinfo=ET) if value.tzinfo is None else value.astimezone(ET)
                      for value in (start, end))
        if start.date() != day or end.date() != day or start >= end or day in sessions:
            raise ValueError(f"Invalid or duplicate market session for {day}")
        sessions[day] = {"date": str(day), "open": iso(start), "close": iso(end), "verified": True}
    return sessions


def normalize_products(products, sessions):
    contracts = {}
    for product in products:
        symbol, chain = product["symbol"], product["chain"]
        root, metadata_source = PRODUCTS[symbol]
        if (chain["symbol"] != root or chain["settle_on_open"] is not False
                or number(chain["trade_value_multiplier"]) != 100):
            raise ValueError(f"Unexpected chain metadata for {symbol}")
        for instrument in product["instruments"]:
            key = instrument["id"]
            expiry = date.fromisoformat(instrument["expiration_date"])
            exercise = instrument.get("exercise_style")
            if (not key or key in contracts or instrument["chain_id"] != chain["id"]
                    or instrument["chain_symbol"] != root or instrument["underlying_type"] != "index"
                    or instrument["state"] != "active" or instrument["tradability"] != "tradable"
                    or (exercise is not None and exercise.upper() != "EUROPEAN")
                    or str(expiry) not in chain["expiration_dates"] or expiry not in sessions):
                raise ValueError(f"Unverified or duplicate option contract for {symbol}")
            close = sessions[expiry]["close"]
            contracts[key] = {
                "id": key, "symbol": symbol, "root": root, "expiry": str(expiry),
                "strike": instrument["strike_price"], "right": RIGHTS[instrument["type"]],
                "exercise": "EUROPEAN", "settlement": "PM_CASH", "multiplier": 100,
                "last_trade": close, "settles_at": close, "verified": True,
                "product_metadata_source": metadata_source,
            }
    if not contracts:
        raise ValueError("No verified option contracts")
    return contracts


def chart_context(chart, session, now):
    """Compute the sandbox's candidate SPY VWAP signal using completed RTH bars."""
    unavailable = {"signal_reason": "SPY_BARS_UNAVAILABLE"}
    if not isinstance(chart, dict) or (chart.get("symbol"), chart.get("interval"), chart.get("bounds")) != (
            "SPY", "5minute", "regular"):
        return unavailable
    try:
        opened, closed = timestamp(session["open"]), timestamp(session["close"])
        bars = {}
        for raw in chart["bars"]:
            at = timestamp(raw["begins_at"])
            if raw.get("session") != "reg" or not opened <= at or at + BAR_LENGTH > min(now, closed):
                continue
            high, low, close, volume = map(number, (
                raw["high_price"], raw["low_price"], raw["close_price"], raw["volume"]))
            if at in bars or not 0 < low <= close <= high or volume <= 0:
                return unavailable
            bars[at] = (high, low, close, volume)
        times = sorted(bars)
        if (len(times) < 2 or any(at != opened + i * BAR_LENGTH for i, at in enumerate(times))
                or now - (times[-1] + BAR_LENGTH) > BAR_LENGTH + MAX_AGE):
            return unavailable
    except (KeyError, TypeError, ValueError, AttributeError, ArithmeticError):
        return unavailable

    traded = weighted = 0
    levels = []
    for at in times:
        high, low, close, volume = bars[at]
        traded += volume
        weighted += (high + low + close) * volume
        levels.append((close, low, weighted / (3 * traded)))
    prior_close, _, prior_vwap = levels[-2]
    close, low, vwap = levels[-1]
    bullish = close > vwap > prior_vwap and (prior_close <= prior_vwap or low <= vwap)
    return {
        "signal_at": iso(now), "signal_source": "ROBINHOOD_SPY_RTH_5MIN_VWAP",
        "signal_bar_at": iso(times[-1] + BAR_LENGTH),
        "signal_reason": "VWAP_RECLAIM_OR_SUPPORT" if bullish else "VWAP_SETUP_UNCONFIRMED",
        "spy_close": str(close), "spy_vwap": str(vwap),
        "direction": "UP" if bullish else "UNCONFIRMED",
        "technical_clear": bullish,
    }


def quote_frames(captures, contracts, sessions):
    symbols = sorted({c["symbol"] for c in contracts.values()})
    frames = []
    for batch in sorted(captures, key=lambda row: timestamp(row["at"])):
        now = timestamp(batch["at"])
        day = now.astimezone(ET).date()
        session = sessions.get(day, {})
        chart = chart_context(batch.get("chart"), session, now)
        for symbol in symbols:
            selected = []
            for quote in batch["quotes"]:
                contract = contracts.get(quote["instrument_id"])
                if contract is None:
                    raise ValueError("Quote has no verified contract")
                if contract["symbol"] == symbol:
                    selected.append({
                        **contract, "at": quote.get("updated_at"), "bid": quote.get("bid_price"),
                        "ask": quote.get("ask_price"), "bid_size": quote.get("bid_size"),
                        "ask_size": quote.get("ask_size"), "delta": quote.get("delta"),
                        "gamma": quote.get("gamma"), "volume": quote.get("volume"),
                        "open_interest": quote.get("open_interest"),
                    })
            frame = {
                "at": iso(now), "symbol": symbol, "session": session,
                "event_clear": False, "technical_clear": False, "range_clear": False,
                "quotes": selected, **chart,
            }
            matches = [q for q in batch.get("index_quotes", ()) if q.get("symbol") == symbol]
            if len(matches) == 1 and matches[0].get("instrument_id"):
                q = matches[0]
                evidence = {"spot": q.get("value"), "spot_at": q.get("venue_timestamp")}
                if spot_at(evidence, now) is not None:
                    frame.update(evidence, spot_source="ROBINHOOD_INDEX_QUOTE")
            frames.append(frame)
    return frames


def verify_capture(payload):
    sessions = calendar_sessions(payload["calendar"])
    contracts = normalize_products(payload["products"], sessions)
    frames = quote_frames(payload["captures"], contracts, sessions)
    if not frames:
        raise ValueError("No quote batches captured")
    dataset = {"kind": "HISTORICAL", "source": "ROBINHOOD_MCP_READ_ONLY_DRY_RUN", "frames": frames}
    report = compare(dataset)
    symbols = {}
    for symbol in sorted({c["symbol"] for c in contracts.values()}):
        expected = {key for key, c in contracts.items() if c["symbol"] == symbol}
        previous = []
        summary = {
            "contracts": len(expected), "usable_quotes": [],
            "repeatable": False, "entry_window_repeatable": False,
        }
        for frame in (f for f in frames if f["symbol"] == symbol):
            now = timestamp(frame["at"])
            quotes = {
                key: q.at for key, q in quotes_at(frame, now).items()
                if q.bid > 0 and min(q.bid_size, q.ask_size) >= 1 and q.delta is not None
            }
            summary["usable_quotes"].append(len(quotes))
            if set(quotes) != expected:
                continue
            local = now.astimezone(CT)
            in_window = local.hour == 11 and local.minute < 5 and market_open(frame, now)
            previous = [(at, prior, was_in_window) for at, prior, was_in_window in previous
                        if now - at <= MAX_AGE]
            for at, prior, was_in_window in previous:
                if now > at and all(quotes[key] > at and quotes[key] > prior[key] for key in expected):
                    summary["repeatable"] = True
                    summary["entry_window_repeatable"] |= in_window and was_in_window
            previous.append((now, quotes, in_window))
        symbols[symbol] = summary

    return {
        "mode": "QUOTE_VALIDATION_ONLY", "symbols": symbols, "coverage": report["coverage"],
        "paper_entries": sum(p["decision_counts"].get("FILLED", 0) for p in report["profiles"].values()),
        "entry_checks": {name: profile["decision_counts"] for name, profile in report["profiles"].items()},
        "capture_times": sorted({frame["at"] for frame in frames}), "dataset": dataset,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    args = parser.parse_args()
    try:
        result = verify_capture(json.loads(args.capture.read_text()))
    except (KeyError, TypeError, ValueError, OSError, ArithmeticError) as error:
        parser.exit(2, f"Capture rejected: {error}\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
