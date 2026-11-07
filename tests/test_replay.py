import copy
import unittest
from replay import compare

DAY = "2026-11-05"
OPEN = DAY + "T14:30:00Z"
CLOSE = DAY + "T21:00:00Z"

def frame(seconds, spot="610", direction="UP", symbol="XSP", credit="0.24", minute="16:40"):
    ts = f"{DAY}T{minute}:{seconds:02}Z"
    quotes = []
    for name, strike, delta, bid, ask, right in [
        ("short", 606, "-0.12", credit, "0.27", "P"),
        ("long", 605, "-0.09", "0.02", "0.03", "P"),
        ("base", 607, "-0.18", "0.48", "0.51", "P"),
        ("call-short", 614, "0.12", "0.22", "0.24", "C"),
        ("call-long", 615, "0.09", "0.02", "0.03", "C"),
    ]:
        quotes.append(dict(id=f"{symbol}-{name}", symbol=symbol, root="SPXW" if symbol=="SPX" else symbol,
                           expiry=DAY, strike=str(strike), right=right, delta=delta, bid=bid, ask=ask,
                           bid_size=10, ask_size=10, at=ts, exercise="EUROPEAN", settlement="PM_CASH",
                           multiplier=100, last_trade=CLOSE, settles_at=CLOSE, verified=True))
    return dict(at=ts, symbol=symbol, session=dict(date=DAY, open=OPEN, close=CLOSE, verified=True),
                spot=spot, spot_at=ts, signal_at=ts, direction=direction, technical_clear=True,
                range_clear=True, event_clear=True, quotes=quotes)

def dataset(*frames, settlements=()):
    return dict(kind="SYNTHETIC_FIXTURE", source="unit test", frames=list(frames), settlements=list(settlements))

def low(d):
    return compare(d)["profiles"]["low_delta"]

class ReplayTests(unittest.TestCase):
    def test_cutoffs_compare_the_same_quotes_without_sharing_positions(self):
        report = compare(dataset(frame(0), frame(30)))
        self.assertEqual(len(report["profiles"]["low_delta"]["trades"]), 1)
        self.assertEqual(report["cutoff_observation"]["profiles"]["low_delta"]["trades"], [])
        result = low(dataset(frame(50, minute="16:59"), frame(0, minute="17:00")))
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["unfilled_orders"], 1)

    def test_0dte_exit_uses_quotes_newer_than_exit_start(self):
        trigger = frame(0, minute="20:25")
        later = frame(30, minute="20:25")
        r = low(dataset(frame(0), frame(30), trigger, later))
        self.assertEqual(r["trades"][0]["reason"], "SCHEDULED_0DTE_EXIT")
        self.assertEqual(r["trades"][0]["closed_at"], later["at"])
        self.assertFalse(r["trades"][0]["exit_deadline_missed"])

    def test_0dte_exit_retries_after_deadline_and_records_the_breach(self):
        gap = frame(0, minute="20:30")
        gap["quotes"] = []
        r = low(dataset(frame(0), frame(30), gap))
        self.assertEqual(r["metrics"]["exit_deadline_misses"], 1)
        self.assertIsNone(r["trades"][0]["closed_at"])
        recovery = frame(30, minute="20:30")
        r = low(dataset(frame(0), frame(30), gap, recovery))
        self.assertEqual(r["trades"][0]["closed_at"], recovery["at"])
        self.assertTrue(r["trades"][0]["exit_deadline_missed"])

    def test_1dte_position_gets_scheduled_exit_on_its_expiration_day(self):
        expiry = "2026-11-06"
        expiry_close = expiry + "T21:00:00Z"
        entry_frames = [frame(0), frame(30)]
        exit_frame = frame(30, minute="20:25")
        for field in ("at", "spot_at", "signal_at"):
            exit_frame[field] = exit_frame[field].replace(DAY, expiry)
        exit_frame["session"] = dict(date=expiry, open=OPEN.replace(DAY, expiry),
                                     close=expiry_close, verified=True)
        for observation in (*entry_frames, exit_frame):
            for quote in observation["quotes"]:
                quote.update(expiry=expiry, last_trade=expiry_close, settles_at=expiry_close,
                             at=observation["at"])

        result = low(dataset(*entry_frames, exit_frame))
        self.assertEqual(result["trades"][0]["closed_at"], exit_frame["at"])
        self.assertEqual(result["trades"][0]["reason"], "SCHEDULED_0DTE_EXIT")

    def test_next_frame_fill_and_settlement_net_of_costs(self):
        s=dict(verified=True, source="official test fixture", root="XSP", expiry=DAY,
               settles_at=CLOSE, at=DAY+"T21:01:00Z", value="610")
        result=low(dataset(frame(0),frame(30),settlements=[s]))
        self.assertEqual(result["trades"][0]["opened_at"],DAY+"T16:40:30Z")
        self.assertEqual(result["metrics"]["net_pnl"],"18.40")
        self.assertEqual(result["metrics"]["wins"],1)
        self.assertEqual(result["metrics"]["pending_settlement"],0)

    def test_last_observation_cannot_fill_and_duplicate_is_idempotent(self):
        f=frame(0)
        result=low(dataset(f,copy.deepcopy(f)))
        self.assertEqual(result["trades"],[])
        self.assertEqual(result["unfilled_orders"],1)

    def test_stale_and_missing_identity_do_not_fill(self):
        for change in ("stale","identity"):
            with self.subTest(change=change):
                f=frame(30)
                if change=="stale": f["quotes"][0]["at"]=DAY+"T16:58:30Z"
                else: f["quotes"][0]["root"]="SPXW"
                self.assertEqual(low(dataset(frame(0),f))["trades"],[])

    def test_bearish_gate_never_opens_put_spread(self):
        self.assertEqual(low(dataset(frame(0,direction="DOWN"),frame(30,direction="DOWN")))["trades"],[])

    def test_neutral_opens_only_four_leg_condor(self):
        r=low(dataset(frame(0,direction="NEUTRAL"),frame(30,direction="NEUTRAL")))
        self.assertEqual(len(r["trades"][0]["legs"]),4)

    def test_risk_combined_across_symbols_and_two_delta_profiles(self):
        d=dataset(frame(0),frame(0,symbol="SPX"),frame(30),frame(30,symbol="SPX"))
        r=compare(d)["profiles"]
        self.assertEqual(len(r["low_delta"]["trades"]),1)
        self.assertEqual(len(r["comparison"]["trades"]),1)
        self.assertLessEqual(float(r["low_delta"]["trades"][0]["risk"]),100)
        self.assertNotEqual(r["low_delta"]["trades"][0]["legs"],r["comparison"]["trades"][0]["legs"])

    def test_expiry_pending_is_neither_win_nor_loss(self):
        d=dataset(frame(0),frame(30))
        d["as_of"]=CLOSE
        r=low(d)
        self.assertEqual(r["metrics"]["pending_settlement"],1)
        self.assertIsNone(r["metrics"]["win_rate_pct"])
        self.assertIsNone(r["trades"][0]["pnl"])

    def test_tested_strike_waits_for_later_exit_quote(self):
        trigger=frame(40,spot="605")
        later=frame(50,spot="605")
        r=low(dataset(frame(0),frame(30),trigger,later))
        self.assertEqual(r["trades"][0]["closed_at"],later["at"])
        self.assertEqual(r["trades"][0]["reason"],"TESTED_STRIKE")

    def test_future_quote_and_inconsistent_series_rejected(self):
        for field,value in (("at",DAY+"T17:02:00Z"),("settles_at",DAY+"T21:30:00Z"),("bid_size",0)):
            with self.subTest(field=field):
                f=frame(0)
                f["quotes"][0][field]=value
                self.assertEqual(low(dataset(f,frame(30)))["trades"],[])

    def test_fill_requires_new_quotes_and_in_band_short(self):
        unchanged=frame(30)
        for quote in unchanged["quotes"]:
            quote["at"]=frame(0)["at"]
        self.assertEqual(low(dataset(frame(0),unchanged))["trades"],[])
        shifted=frame(30)
        shifted["quotes"][0]["delta"]="-0.19"
        self.assertEqual(low(dataset(frame(0),shifted))["trades"],[])

    def test_tested_strike_latches_during_option_quote_gap(self):
        gap=frame(40,spot="605")
        gap["quotes"]=[]
        r=low(dataset(frame(0),frame(30),gap,frame(50)))
        self.assertEqual(r["trades"][0]["reason"],"TESTED_STRIKE")
        self.assertEqual(r["trades"][0]["closed_at"],frame(50)["at"])

    def test_conflicting_duplicate_frames_fail(self):
        with self.assertRaises(ValueError):
            compare(dataset(frame(0),frame(0,spot="611")))

    def test_equity_profile_cannot_enter_index_trial(self):
        self.assertEqual(low(dataset(frame(0,symbol="AAPL"),frame(30,symbol="AAPL")))["trades"],[])

if __name__=="__main__":
    unittest.main()
