import unittest

from observe import decision, frames_from_snapshot
from replay import ENTRY_CUTOFF, timestamp


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.now = timestamp("2026-11-06T15:55:00Z")
        self.frame = {
            "symbol": "SPX",
            "session": {
                "date": "2026-11-06", "verified": True,
                "open": "2026-11-06T14:30:00Z", "close": "2026-11-06T21:00:00Z",
            },
            "spot": "7400", "spot_at": "2026-11-06T15:55:00Z",
            "signal_at": "2026-11-06T15:55:00Z", "technical_clear": False,
            "event_clear": True, "calendar_status": "CLEAR",
        }

    def test_names_technical_gate_after_calendar_clears(self):
        self.assertEqual(decision(self.frame, {}, self.now, ENTRY_CUTOFF)["status"],
                         "TECHNICAL_SETUP_UNCONFIRMED")
        self.frame["calendar_status"] = "EVENT_BLACKOUT"
        self.assertEqual(decision(self.frame, {}, self.now, ENTRY_CUTOFF)["status"], "EVENT_BLACKOUT")

    def test_stale_envelope_is_rejected_even_in_open_session(self):
        snapshot = {
            "source": "ROBINHOOD_MCP_RESEARCH", "research_only": True, "paper_only": True,
            "order_submission_enabled": False, "broker_execution_enabled": False,
            "live_trading_enabled": False, "generated_at_utc": "2026-11-06T15:53:59Z",
        }
        self.assertEqual(frames_from_snapshot(snapshot, self.frame["session"], self.now),
                         ([], "SNAPSHOT_STALE_OR_FUTURE"))
