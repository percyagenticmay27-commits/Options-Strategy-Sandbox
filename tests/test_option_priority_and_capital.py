"""Keep Option-001 priority and cap Strategy B's independent paper capital."""
import fcntl
import json
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
import replay
from scan_paper import option_bot_idle, execute_scan
from forward_paper import forward_cycle
from tests.test_replay import frame,dataset


class SandboxCapitalAndPriorityTests(unittest.TestCase):
    def test_each_profile_reports_500_paper_capital(self):
        result=replay.compare(dataset(frame(0),frame(30)))
        self.assertEqual(result['capital'],'500.00')
        self.assertEqual(result['combined_risk_limit'],'100.00')
        self.assertEqual(set(result['profiles']),{'low_delta','comparison'})

    def test_persisted_forward_ledger_reports_allocation_separately_from_shadow(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            result=forward_cycle(root,[],datetime(2026,11,9,21,0,tzinfo=timezone.utc),'SNAPSHOT_STALE_OR_FUTURE')
            self.assertEqual(result['low_delta_allocated_capital'],'500.00')
            self.assertEqual(result['comparison_hypothetical_capital'],'500.00')
            self.assertEqual(result['profiles']['low_delta']['starting_capital'],'500.00')
            self.assertEqual(result['profiles']['comparison']['starting_capital'],'500.00')
            self.assertEqual(result['max_defined_risk_per_book'],'100.00')

    def test_entry_before_10am_central_is_blocked(self):
        early=frame(0,minute='15:40') # 9:40 Central
        after=frame(0,minute='16:40')
        self.assertEqual(replay.entry_gate(early,replay.timestamp(early['at'])),'OUTSIDE_ENTRY_WINDOW')
        self.assertIsNone(replay.entry_gate(after,replay.timestamp(after['at'])))

    def test_option_lock_held_means_sandbox_defers_without_consume(self):
        with tempfile.TemporaryDirectory() as d:
            lock=Path(d)/'option.lock'
            with lock.open('w') as handle:
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                with patch('scan_paper.OPTION_LOCK',lock):
                    self.assertFalse(option_bot_idle())
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
            with patch('scan_paper.OPTION_LOCK',lock):
                self.assertTrue(option_bot_idle())

    def test_deferred_option_scan_makes_no_paper_entry(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            now=datetime(2026,11,9,16,40,tzinfo=timezone.utc)
            with patch('scan_paper.option_bot_idle',return_value=False):
                r=execute_scan(root,now,[],{}, {})
            self.assertEqual(r['status'],'OPTION_BOT_PRIORITY_DEFERRED')
            self.assertEqual(r['paper_entries'],0)
            self.assertFalse((root/'state/refresh_request.json').exists())
