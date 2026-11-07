"""Deterministic, paper-only SPX/XSP comparison. Reads no production modules."""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from itertools import product
from zoneinfo import ZoneInfo

VERSION = "0.1.2"
ET = ZoneInfo("America/New_York")
CT = ZoneInfo("America/Chicago")
D = Decimal
ZERO = D("0")
CAPITAL = D("5000")
RISK_CAP = D("100")
MAX_AGE = timedelta(seconds=60)
ENTRY_START = time(9, 30)
ENTRY_CUTOFF = time(11)
OBSERVED_CUTOFF = time(10, 30)
EXIT_CUTOFF = time(14, 30)
EXIT_LEAD = timedelta(minutes=5)
FEE = D("0.65")
MULTIPLIER = D(100)
MIN_RETURN = D(".15")
ROOTS = {"SPX": {"SPXW"}, "XSP": {"XSP"}}
PROFILES = {
    "low_delta": (D(".10"), D(".15"), D(".125")),
    "comparison": (D(".15"), D(".25"), D(".175")),
}


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp must include its timezone")
    return result.astimezone(timezone.utc)


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


def number(value):
    result = D(str(value))
    if not result.is_finite():
        raise ValueError("Nonfinite number")
    return result


def money(value):
    return format(value.quantize(D(".01")), "f")


def recent(value, now):
    try:
        return timedelta(0) <= now - timestamp(value) <= MAX_AGE
    except (AttributeError, TypeError, ValueError):
        return False


@dataclass(frozen=True)
class Contract:
    key: str
    symbol: str
    root: str
    expiry: date
    strike: Decimal
    right: str
    last_trade: datetime
    settles_at: datetime

    @property
    def series(self):
        return self.symbol, self.root, self.expiry, self.last_trade, self.settles_at


@dataclass(frozen=True)
class Quote:
    contract: Contract
    at: datetime
    bid: Decimal
    ask: Decimal
    delta: Decimal | None
    bid_size: Decimal
    ask_size: Decimal


def quotes_at(frame, now):
    quotes = {}
    seen, invalid = set(), set()
    for raw in frame.get("quotes") or []:
        try:
            symbol, key, root = raw["symbol"], raw["id"], raw["root"]
            if not isinstance(key, str) or not key.strip():
                continue
            if key in seen:
                invalid.add(key)
            seen.add(key)
            if (symbol != frame["symbol"] or root not in ROOTS.get(symbol, ())
                    or raw.get("verified") is not True or raw["exercise"] != "EUROPEAN"
                    or raw["settlement"] != "PM_CASH" or number(raw["multiplier"]) != MULTIPLIER
                    or raw["right"] not in ("P", "C") or not recent(raw["at"], now)):
                continue
            expiry = date.fromisoformat(raw["expiry"])
            last_trade, settles_at = timestamp(raw["last_trade"]), timestamp(raw["settles_at"])
            if (now >= last_trade or last_trade > settles_at
                    or last_trade.astimezone(ET).date() != expiry
                    or settles_at.astimezone(ET).date() != expiry):
                continue
            strike, bid, ask = map(number, (raw["strike"], raw["bid"], raw["ask"]))
            bid_size, ask_size = map(number, (raw["bid_size"], raw["ask_size"]))
            delta = number(raw["delta"]) if raw.get("delta") is not None else None
            if (strike <= 0 or bid < 0 or ask <= 0 or ask < bid or min(bid_size, ask_size) < 0
                    or (delta is not None and not (-1 <= delta <= 0 if raw["right"] == "P" else 0 <= delta <= 1))):
                continue
            contract = Contract(key, symbol, root, expiry, strike, raw["right"], last_trade, settles_at)
            quotes[key] = Quote(contract, timestamp(raw["at"]), bid, ask, delta, bid_size, ask_size)
        except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation):
            continue
    return {k: q for k, q in quotes.items() if k not in invalid}


def market_open(frame, now):
    try:
        s = frame["session"]
        opened, closed = timestamp(s["open"]), timestamp(s["close"])
        day = now.astimezone(ET).date()
        return (s.get("verified") is True and date.fromisoformat(s["date"]) == day
                and opened.astimezone(ET).date() == closed.astimezone(ET).date() == day
                and opened <= now < closed)
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def spot_at(frame, now):
    if recent(frame.get("spot_at"), now):
        try:
            spot = number(frame["spot"])
            return spot if spot > 0 else None
        except (KeyError, ValueError, InvalidOperation):
            pass
    return None


def entry_gate(frame, now, cutoff=ENTRY_CUTOFF):
    if frame["symbol"] not in ROOTS:
        return "UNSUPPORTED_SYMBOL"
    if not market_open(frame, now):
        return "SESSION_UNVERIFIED_OR_CLOSED"
    if not ENTRY_START <= now.astimezone(CT).time() < cutoff:
        return "OUTSIDE_ENTRY_WINDOW"
    if not recent(frame.get("signal_at"), now) or spot_at(frame, now) is None:
        return "SIGNAL_OR_SPOT_UNAVAILABLE"
    if frame.get("event_clear") is not True or frame.get("technical_clear") is not True:
        return "EVENT_OR_TECHNICAL_GATE"
    if frame.get("direction") == "UP":
        return None
    if frame.get("direction") == "NEUTRAL" and frame.get("range_clear") is True:
        return None
    return "DIRECTION_OR_RANGE_GATE"


@dataclass(frozen=True)
class Spread:
    shorts: tuple[Contract, ...]
    longs: tuple[Contract, ...]

    @property
    def width(self):
        return max(abs(short.strike - long.strike) for short, long in zip(self.shorts, self.longs))

    @property
    def costs(self):
        return FEE * 2 * (len(self.shorts) + len(self.longs))

    def payoff(self, spot):
        def intrinsic(contract):
            move = spot - contract.strike
            return max(ZERO, move if contract.right == "C" else -move)
        return sum(map(intrinsic, self.shorts)) - sum(map(intrinsic, self.longs))


def price(spread, quotes, closing=False, newer_than=None):
    credit = ZERO
    for side, legs in ((1, spread.shorts), (-1, spread.longs)):
        for leg in legs:
            q = quotes.get(leg.key)
            if q is None or q.contract != leg or (newer_than is not None and q.at <= newer_than):
                return None
            sell = (side < 0) if closing else (side > 0)
            amount, size = (q.bid, q.bid_size) if sell else (q.ask, q.ask_size)
            if size < 1:
                return None
            credit += side * amount
    return credit if 0 <= credit <= spread.width else None


def economics(spread, credit):
    profit = credit * MULTIPLIER - spread.costs
    loss = (spread.width - credit) * MULTIPLIER + spread.costs
    return profit, loss


def eligible_short(q, spot, band):
    return (q.delta is not None and band[0] <= abs(q.delta) <= band[1]
            and (q.contract.strike < spot if q.contract.right == "P" else q.contract.strike > spot))


def selection(frame, now, quotes, band, remaining):
    target = band[2]
    spot = spot_at(frame, now)
    day = now.astimezone(ET).date()
    sides = {"P": [], "C": []}
    for short in quotes.values():
        s = short.contract
        if not eligible_short(short, spot, band) or not 0 <= (s.expiry - day).days <= 1:
            continue
        if s.right == "C" and frame["direction"] != "NEUTRAL":
            continue
        for long in quotes.values():
            leg = long.contract
            if leg.series != s.series or leg.right != s.right:
                continue
            if s.right == "P" and leg.strike >= s.strike or s.right == "C" and leg.strike <= s.strike:
                continue
            side_credit = short.bid - long.ask
            if side_credit <= 0 or short.bid_size < 1 or long.ask_size < 1:
                continue
            sides[s.right].append((s, leg, abs(abs(short.delta) - target)))
    if frame["direction"] == "UP":
        candidates = (((s,), (leg,), dist) for s, leg, dist in sides["P"])
    else:
        candidates = (((ps, cs), (pl, cl), pd + cd)
                      for (ps, pl, pd), (cs, cl, cd) in product(sides["P"], sides["C"])
                      if ps.series == cs.series and ps.strike < cs.strike)
    best = None
    for shorts, longs, dist in candidates:
        spread = Spread(shorts, longs)
        credit = price(spread, quotes)
        if credit is None or credit <= 0 or credit >= spread.width:
            continue
        profit, loss = economics(spread, credit)
        if loss > remaining or profit < MIN_RETURN * loss:
            continue
        key = (dist, -profit / loss, loss, tuple(leg.key for leg in shorts + longs))
        if best is None or key < best[0]:
            best = key, spread, credit
    return (best[1], best[2]) if best else None


@dataclass
class Pending:
    spread: Spread
    credit: Decimal
    at: datetime
    direction: str


@dataclass
class Position:
    spread: Spread
    credit: Decimal
    risk: Decimal
    opened: datetime
    exit_trigger: tuple[datetime, str] | None = None

    @property
    def exit_deadline(self):
        leg = self.spread.shorts[0]
        local_deadline = datetime.combine(leg.expiry, EXIT_CUTOFF, tzinfo=CT)
        return min(local_deadline.astimezone(timezone.utc), leg.last_trade)


def unique_rows(rows, identity):
    result = {}
    for row in rows:
        key = identity(row)
        if key in result and row != result[key]:
            raise ValueError(f"Conflicting duplicate record: {key}")
        result[key] = row
    return result


def trade_row(position, closed=None, debit=None, reason=None, pending=False, horizon=None):
    spread = position.spread
    deadline = position.exit_deadline
    missed = closed > deadline if closed is not None else horizon is not None and horizon >= deadline
    result = {
        "symbol": spread.shorts[0].symbol,
        "opened_at": iso(position.opened),
        "closed_at": iso(closed) if closed else None,
        "legs": [{"id": leg.key, "side": side, "strike": str(leg.strike), "right": leg.right,
                  "root": leg.root, "expiry": leg.expiry.isoformat()}
                 for side, legs in (("SHORT", spread.shorts), ("LONG", spread.longs)) for leg in legs],
        "credit": str(position.credit),
        "exit_debit": str(debit) if debit is not None else None,
        "exit_deadline_at": iso(deadline),
        "exit_deadline_missed": missed,
        "risk": money(position.risk),
        "round_trip_cost": money(spread.costs),
        "reason": reason,
        "status": "CLOSED" if closed else "PENDING_SETTLEMENT" if pending else "OPEN_AT_END",
        "pnl": money((position.credit - debit) * MULTIPLIER - spread.costs) if closed else None,
    }
    return result


def summarize(trades, marked_dd, mark_count, mark_gaps):
    pnl = [D(trade["pnl"]) for trade in trades if trade["pnl"] is not None]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x < 0]
    curve = peak = dd = ZERO
    for x in pnl:
        curve += x
        peak = max(peak, curve)
        dd = max(dd, peak - curve)
    return {
        "scored": len(pnl), "wins": len(wins), "losses": len(losses),
        "breakeven": sum(x == 0 for x in pnl),
        "win_rate_pct": money(D(len(wins)) / len(pnl) * 100) if pnl else None,
        "gross_wins": money(sum(wins, ZERO)), "gross_losses": money(sum(losses, ZERO)),
        "net_pnl": money(sum(pnl, ZERO)), "profit_factor": money(sum(wins, ZERO) / -sum(losses)) if losses else None,
        "realized_max_drawdown": money(dd),
        "observed_marked_max_drawdown": money(marked_dd) if mark_count else None,
        "complete_exposed_mark_observations": mark_count, "incomplete_exposed_mark_observations": mark_gaps,
        "open_at_end": sum(t["status"] == "OPEN_AT_END" for t in trades),
        "pending_settlement": sum(t["status"] == "PENDING_SETTLEMENT" for t in trades),
        "exit_deadline_misses": sum(t["exit_deadline_missed"] for t in trades),
    }


def replay(frames, settlements, horizon, band, cutoff):
    open_positions = {}
    orders = {}
    trades = []
    decisions = []
    daily_entries = Counter()
    stats = Counter()
    realized = marked_peak = marked_dd = ZERO
    mark_count = mark_gaps = 0
    unfilled = 0
    for now in sorted(set(frames) | set(settlements)):
        for settlement in settlements.get(now, ()):
            for symbol, position in list(open_positions.items()):
                leg = position.spread.shorts[0]
                if (settlement["root"], settlement["expiry"], settlement["settles_at"]) != (
                        leg.root, leg.expiry.isoformat(), iso(leg.settles_at)):
                    continue
                debit = position.spread.payoff(number(settlement["value"]))
                row = trade_row(position, now, debit, "OFFICIAL_SETTLEMENT")
                trades.append(row)
                realized += D(row["pnl"])
                del open_positions[symbol]
        quotes_by_symbol = {}
        for frame in sorted(frames.get(now, ()), key=lambda r: ("SPX", "XSP").index(r["symbol"]) if r["symbol"] in ROOTS else 2):
            symbol = frame["symbol"]
            quotes = quotes_at(frame, now) if market_open(frame, now) else {}
            quotes_by_symbol[symbol] = quotes
            position = open_positions.get(symbol)
            if position:
                deadline = position.exit_deadline
                if position.exit_trigger is None and now >= deadline - EXIT_LEAD:
                    position.exit_trigger = (deadline - EXIT_LEAD, "SCHEDULED_0DTE_EXIT")
                debit = price(position.spread, quotes, closing=True)
                spot = spot_at(frame, now)
                tested = spot is not None and any(
                    spot <= leg.strike if leg.right == "P" else spot >= leg.strike
                    for leg in position.spread.shorts
                )
                if position.exit_trigger:
                    debit = price(position.spread, quotes, closing=True, newer_than=position.exit_trigger[0])
                    if debit is not None:
                        row = trade_row(position, now, debit, position.exit_trigger[1])
                        trades.append(row)
                        realized += D(row["pnl"])
                        del open_positions[symbol]
                    continue
                if tested and market_open(frame, now) and now < position.spread.shorts[0].last_trade:
                    position.exit_trigger = (now, "TESTED_STRIKE")
                elif debit is not None and debit >= 2 * position.credit:
                    position.exit_trigger = (now, "CREDIT_STOP")
                elif debit is not None and debit <= position.credit / 2:
                    position.exit_trigger = (now, "PROFIT_TARGET")
                continue
            day = now.astimezone(ET).date()
            remaining = min(RISK_CAP, CAPITAL + realized) - sum(p.risk for p in open_positions.values())
            gate = entry_gate(frame, now, cutoff)
            order = orders.pop(symbol, None)
            if order:
                fill = price(order.spread, quotes, newer_than=order.at)
                valid = (timedelta(0) < now - order.at <= MAX_AGE and not gate
                         and frame["direction"] == order.direction and daily_entries[day] < 2
                         and fill is not None and order.credit <= fill < order.spread.width
                         and all(eligible_short(quotes[leg.key], spot_at(frame, now), band)
                                 for leg in order.spread.shorts))
                if valid:
                    profit, risk = economics(order.spread, fill)
                    if risk <= remaining and profit >= MIN_RETURN * risk:
                        open_positions[symbol] = Position(order.spread, fill, risk, now)
                        daily_entries[day] += 1
                        stats["FILLED"] += 1
                        decisions.append({"at": iso(now), "symbol": symbol, "reason": "FILLED",
                                          "signal_at": iso(order.at), "credit": str(fill), "risk": money(risk)})
                        continue
                unfilled += 1
                stats["ORDER_UNFILLED"] += 1
            reason = gate
            if not reason:
                if daily_entries[day] >= 2 or remaining <= 0:
                    reason = "ACCOUNT_LIMIT"
                else:
                    candidate = selection(frame, now, quotes, band, remaining)
                    if candidate:
                        spread, credit = candidate
                        orders[symbol] = Pending(spread, credit, now, frame["direction"])
                        reason = "ORDER_QUEUED"
                    else:
                        reason = "NO_ELIGIBLE_SPREAD"
            stats[reason] += 1
            decisions.append({"at": iso(now), "symbol": symbol, "reason": reason})
        marks = [(p, price(p.spread, quotes_by_symbol.get(s, {}), closing=True)) for s, p in open_positions.items()]
        if any(mark is None for _, mark in marks):
            mark_gaps += 1
        else:
            mark_count += bool(marks)
            equity = realized + sum((p.credit - mark) * MULTIPLIER - p.spread.costs for p, mark in marks)
            marked_peak = max(marked_peak, equity)
            marked_dd = max(marked_dd, marked_peak - equity)
    for position in open_positions.values():
        expired = horizon is not None and horizon >= position.spread.shorts[0].last_trade
        exit_due = horizon is not None and horizon >= position.exit_deadline - EXIT_LEAD
        reason = "OFFICIAL_VALUE_UNAVAILABLE" if expired else "EXIT_QUOTE_UNAVAILABLE" if position.exit_trigger or exit_due else None
        trades.append(trade_row(position, reason=reason, pending=expired, horizon=horizon))
    return {"trades": trades, "decisions": decisions, "decision_counts": dict(stats),
            "unfilled_orders": unfilled + len(orders),
            "metrics": summarize(trades, marked_dd, mark_count, mark_gaps)}


def compare(dataset):
    kind = dataset["kind"]
    if kind not in {"HISTORICAL", "SYNTHETIC_FIXTURE"} or not dataset.get("source"):
        raise ValueError("Dataset must declare its kind and source")
    frames = defaultdict(list)
    raw_frames = unique_rows(dataset["frames"], lambda f: (timestamp(f["at"]), f["symbol"]))
    for (now, _), frame in raw_frames.items():
        frames[now].append(frame)
    settlements = defaultdict(list)
    official = unique_rows(dataset.get("settlements", []), lambda s: (s["root"], s["expiry"], timestamp(s["settles_at"])))
    for (_, _, due), settlement in official.items():
        now = timestamp(settlement["at"])
        if (settlement.get("verified") is True and settlement.get("source")
                and now >= due and number(settlement["value"]) > 0):
            settlements[now].append({**settlement, "settles_at": iso(due)})
    events = set(frames) | set(settlements)
    horizon = timestamp(dataset["as_of"]) if "as_of" in dataset else max(events, default=None)
    if events and horizon < max(events):
        raise ValueError("Replay horizon precedes an input observation")
    profiles_by_cutoff = {
        cutoff: {name: {"short_delta_min": str(band[0]), "short_delta_max": str(band[1]),
                        **replay(frames, settlements, horizon, band, cutoff)}
                 for name, band in PROFILES.items()}
        for cutoff in (ENTRY_CUTOFF, OBSERVED_CUTOFF)
    }
    return {
        "version": VERSION, "dataset_kind": kind, "source": dataset["source"],
        "capital": money(CAPITAL), "combined_risk_limit": money(RISK_CAP),
        "assumptions": {"paper_only": True, "quantity": 1, "fee_per_leg_per_side": str(FEE),
                        "minimum_net_return": str(MIN_RETURN), "max_quote_age_seconds": MAX_AGE.seconds,
                        "entry_order_ttl_seconds": MAX_AGE.seconds, "entry_window_central": "09:30–11:00",
                        "0dte_exit_attempt_central": "14:25", "0dte_exit_deadline_central": "14:30",
                        "0dte_exit_lead_minutes": EXIT_LEAD.seconds // 60,
                        "max_daily_entries": 2, "stop_credit_multiple": 2, "profit_capture_fraction": ".50"},
        "coverage": {"frames": len(raw_frames), "sessions": sorted({t.astimezone(ET).date().isoformat() for t, _ in raw_frames}),
                     "verified_open_frames": sum(market_open(f, t) for (t, _), f in raw_frames.items()),
                     "eligible_decision_frames": sum(entry_gate(f, t) is None for (t, _), f in raw_frames.items()),
                     "settlement_records": sum(map(len, settlements.values()))},
        "promotion_status": "RESEARCH_ONLY",
        "comparison_scope": "Matched delta-band experiment. The comparison profile is a delta-filter proxy.",
        "profiles": profiles_by_cutoff[ENTRY_CUTOFF],
        "cutoff_observation": {
            "mode": "SHADOW_REPLAY", "entry_window_central": "09:30–10:30",
            "profiles": profiles_by_cutoff[OBSERVED_CUTOFF],
        },
    }
