"""Standalone Strategy B forward paper execution using actual independently refreshed observations."""
import copy
import json
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from tests.test_replay import frame
from forward_paper import forward_cycle

class ForwardPaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.now=datetime(2026,11,5,16,40,tzinfo=timezone.utc)
    def test_two_verified_fresh_quote_batches_paper_fill_but_no_broker_orders(self):
        f=frame(0)
        first=forward_cycle(self.root,[f],self.now,'FRESH')
        self.assertEqual(first['status'],'PAPER_ACTIVE')
        self.assertEqual(first['paper_entries'],0)
        second=forward_cycle(self.root,[frame(30)],self.now+timedelta(seconds=30),'FRESH')
        self.assertEqual(second['paper_entries'],2)
        self.assertEqual(second['profiles']['low_delta']['open_count'],1)
        self.assertFalse(second['order_submission_enabled'])
        self.assertEqual(second['broker_submissions'],0)
        self.assertEqual(forward_cycle(self.root,[frame(30)],self.now+timedelta(seconds=31),'FRESH')['paper_entries'],2)
    def test_stale_feed_does_not_change_ledger_and_requests_connector_refresh(self):
        forward_cycle(self.root,[frame(0)],self.now,'FRESH')
        r=forward_cycle(self.root,[frame(30)],self.now+timedelta(seconds=30),'SNAPSHOT_STALE_OR_FUTURE')
        self.assertEqual(r['paper_entries'],0)
        self.assertEqual(r['status'],'PENDING_CONNECTOR_REFRESH')
        self.assertTrue((self.root/'state/refresh_request.json').exists())
        self.assertEqual(json.loads((self.root/'state/forward_frames.json').read_text())['frames'],[frame(0)])
    def test_unconfirmed_technical_or_event_gate_never_enters(self):
        bad=frame(0)
        bad['technical_clear']=False
        bad2=frame(30)
        bad2['technical_clear']=False
        forward_cycle(self.root,[bad],self.now,'FRESH')
        r=forward_cycle(self.root,[bad2],self.now+timedelta(seconds=30),'FRESH')
        self.assertEqual(r['paper_entries'],0)
        self.assertEqual(r['profiles']['low_delta']['open_count'],0)
    def test_realized_pnl_and_positions_persist_across_restart(self):
        forward_cycle(self.root,[frame(0)],self.now,'FRESH')
        forward_cycle(self.root,[frame(30)],self.now+timedelta(seconds=30),'FRESH')
        exit1=frame(0,minute='20:25')
        exit2=frame(30,minute='20:25')
        forward_cycle(self.root,[exit1],datetime.fromisoformat(exit1['at'].replace('Z','+00:00')),'FRESH')
        result=forward_cycle(self.root,[exit2],datetime.fromisoformat(exit2['at'].replace('Z','+00:00')),'FRESH')
        self.assertEqual(result['profiles']['low_delta']['open_count'],0)
        self.assertEqual(result['profiles']['low_delta']['completed_count'],1)
        self.assertIsNotNone(result['profiles']['low_delta']['realized_pnl'])
        again=forward_cycle(self.root,[exit2],datetime.fromisoformat(exit2['at'].replace('Z','+00:00')),'FRESH')
        self.assertEqual(again['profiles']['low_delta']['completed_count'],1)

class ExecutionEventLedgerTests(unittest.TestCase):
    def test_paper_buy_and_close_events_are_persistent_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            base=datetime(2026,11,5,16,40,tzinfo=timezone.utc)
            forward_cycle(root,[frame(0)],base,'FRESH')
            purchased=forward_cycle(root,[frame(30)],base+timedelta(seconds=30),'FRESH')
            events=json.loads((root/'state/paper_events.json').read_text())['events']
            self.assertEqual(len([x for x in events if x['type']=='PAPER_BUY']),2)
            self.assertEqual(purchased['new_paper_events'],2)
            later=frame(0,minute='20:25');future=frame(30,minute='20:25')
            for f in (later,future):
                forward_cycle(root,[f],datetime.fromisoformat(f['at'].replace('Z','+00:00')),'FRESH')
            events=json.loads((root/'state/paper_events.json').read_text())['events']
            self.assertEqual(len([x for x in events if x['type']=='PAPER_SELL']),2)
            ftime=datetime.fromisoformat(future['at'].replace('Z','+00:00'))
            repeated=forward_cycle(root,[future],ftime,'FRESH')
            self.assertEqual(repeated['new_paper_events'],0)
            self.assertEqual(len(json.loads((root/'state/paper_events.json').read_text())['events']),4)
