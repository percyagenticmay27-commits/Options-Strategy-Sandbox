import unittest
from datetime import timedelta

from event_calendar import bls_events, clearance, fed_events
from replay import iso, timestamp


class EventCalendarTests(unittest.TestCase):
    def test_calendar_clearance_replaces_blanket_block(self):
        now = timestamp("2026-11-06T14:29:00Z")
        jobs = timestamp("2026-11-06T13:30:00Z")
        cache = {
            "verified_at": iso(jobs - timedelta(hours=1)),
            "sources": {"BLS": "verified", "FED": "verified"},
            "events": [{"at": iso(jobs), "name": "Employment Situation"}],
        }
        self.assertEqual(clearance(cache, jobs)["status"], "EVENT_BLACKOUT")
        self.assertEqual(clearance(cache, now)["status"], "CLEAR")
        self.assertEqual(clearance(cache, now + timedelta(days=1))["status"], "CALENDAR_UNAVAILABLE")
        cache["sources"].pop("FED")
        self.assertEqual(clearance(cache, now)["status"], "CALENDAR_UNAVAILABLE")

    def test_parses_bls_time_zone_and_ignores_unrelated_releases(self):
        blocks = [
            "BEGIN:VEVENT\nSUMMARY:" + name + "\nDTSTART;TZID=US-Eastern:" + at + "\nEND:VEVENT"
            for name, at in (("Employment Situation", "20261106T083000"),
                             ("Consumer Price Index", "20261110T083000"),
                             ("Producer Price Index", "20261112T083000"))
        ]
        events = bls_events("BEGIN:VCALENDAR\n" + "\n".join(blocks) + "\nEND:VCALENDAR",
                            timestamp("2026-11-06T12:00:00Z").date())
        self.assertEqual([x["at"] for x in events], ["2026-11-06T13:30:00Z", "2026-11-10T13:30:00Z"])

    def test_fed_meeting_uses_second_day_and_correct_eastern_offset(self):
        days = (("January", "27-28"), ("March", "17-18"), ("April", "28-29"), ("June", "16-17"),
                ("July", "28-29"), ("September", "15-16"), ("October", "27-28"), ("December", "8-9"))
        rows = "".join(f'<div class="fomc-meeting__month">{month}</div>'
                       f'<div class="fomc-meeting__date">{span}*</div>' for month, span in days)
        result = fed_events("<h4>2026 FOMC Meetings</h4>" + rows + "<h4>2025 FOMC Meetings</h4>", 2026)
        self.assertEqual(len(result), 16)
        self.assertEqual([row["at"] for row in result[-2:]],
                         ["2026-12-09T19:00:00Z", "2026-12-09T19:30:00Z"])
