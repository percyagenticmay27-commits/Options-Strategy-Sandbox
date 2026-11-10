import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RobinhoodCaptureTest(unittest.TestCase):
    def test_connector_handoff_rejects_stale_quotes_and_never_enters(self):
        chain = {"id": "chain", "symbol": "XSP", "settle_on_open": False,
                 "trade_value_multiplier": "100", "expiration_dates": ["2026-11-06"]}
        instruments = [
            {"id": key, "chain_id": "chain", "chain_symbol": "XSP", "expiration_date": "2026-11-06",
             "strike_price": strike, "type": "put", "state": "active",
             "tradability": "tradable", "underlying_type": "index"}
            for key, strike in (("short", "745"), ("wing", "744"))
        ]
        batches = []
        for seconds in ("10", "30", "40"):
            quotes = [
                {"instrument_id": key, "updated_at": f"2026-11-06T17:00:{seconds}Z",
                 "bid_price": bid, "ask_price": ask, "bid_size": 4, "ask_size": 3,
                 "delta": delta}
                for key, bid, ask, delta in (("short", ".22", ".25", "-.12"), ("wing", ".05", ".06", "-.04"))
            ]
            if seconds == "40":
                quotes[0]["updated_at"] = "2026-11-06T16:58:00Z"
            batches.append({"at": f"2026-11-06T17:00:{seconds}Z", "quotes": quotes})
        payload = {
            "calendar": [{"date": "2026-11-06", "open": "2026-11-06T09:30:00",
                          "close": "2026-11-06T16:00:00"}],
            "products": [{"symbol": "XSP", "chain": chain, "instruments": instruments}],
            "captures": batches,
        }

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "capture.json"
            source.write_text(json.dumps(payload))
            result = subprocess.run([sys.executable, str(ROOT / "robinhood_capture.py"), str(source)],
                                    text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["symbols"]["XSP"]["usable_quotes"], [2, 2, 1])
        self.assertEqual(report["symbols"]["XSP"]["entry_window_repeatable"], True)
        self.assertEqual(report["coverage"]["eligible_decision_frames"], 0)
        self.assertEqual(report["paper_entries"], 0)

    def test_live_inputs_clear_spot_and_signal_without_authorizing_entry(self):
        from datetime import datetime, timedelta, timezone
        from replay import entry_gate, iso, timestamp
        from robinhood_capture import quote_frames

        opened = datetime(2026, 11, 6, 14, 30, tzinfo=timezone.utc)
        now = opened + timedelta(minutes=95, seconds=20)
        session = {"date": "2026-11-06", "open": iso(opened),
                   "close": iso(opened + timedelta(hours=6, minutes=30)), "verified": True}
        bars = [
            {"begins_at": iso(opened + timedelta(minutes=5*i)), "session": "reg",
             "close_price": 100 if i < 18 else 103 if i == 18 else 80,
             "high_price": 100 if i < 18 else 103 if i == 18 else 80,
             "low_price": 100 if i < 18 else 103 if i == 18 else 80, "volume": 1000}
            for i in range(20)
        ]
        batch = {"at": iso(now), "quotes": [],
                 "index_quotes": [{"symbol": "XSP", "instrument_id": "index",
                                   "value": "740.15", "venue_timestamp": iso(now)}],
                 "chart": {"symbol": "SPY", "interval": "5minute", "bounds": "regular", "bars": bars}}
        contracts = {"option": {"symbol": "XSP"}}
        frame = quote_frames([batch], contracts, {now.date(): session})[0]
        self.assertEqual(frame.get("spot"), "740.15")
        self.assertEqual(frame.get("direction"), "UP")
        self.assertTrue(frame["technical_clear"])
        self.assertFalse(frame["range_clear"])
        self.assertEqual(entry_gate(frame, timestamp(frame["at"])), "EVENT_OR_TECHNICAL_GATE")

        batch["index_quotes"][0]["venue_timestamp"] = iso(now - timedelta(seconds=61))
        frame = quote_frames([batch], contracts, {now.date(): session})[0]
        self.assertEqual(entry_gate(frame, now), "SIGNAL_OR_SPOT_UNAVAILABLE")

        del bars[4]
        frame = quote_frames([batch], contracts, {now.date(): session})[0]
        self.assertIsNone(frame.get("signal_at"))
