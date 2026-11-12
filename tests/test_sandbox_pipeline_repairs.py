import json,tempfile,unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
from replay import entry_gate_detail
from forward_paper import forward_cycle
from tests.test_replay import frame
from scan_paper import execute_scan

class PipelineRegressionTests(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
  self.root=Path(t.name);(self.root/'state').mkdir()
  self.now=datetime(2026,11,5,16,40,tzinfo=timezone.utc)
 def test_event_and_technical_rejections_are_separate(self):
  f=frame(0)
  f['technical_clear']=False
  f['event_clear']=False
  d=entry_gate_detail(f,self.now)
  self.assertIn('EVENT_NOT_CLEAR',d['reasons'])
  self.assertIn('TECHNICAL_SETUP_UNCONFIRMED',d['reasons'])
 def test_technical_only_not_misreported_as_event(self):
  f=frame(0);f['technical_clear']=False
  d=entry_gate_detail(f,self.now)
  self.assertIn('TECHNICAL_SETUP_UNCONFIRMED',d['reasons'])
  self.assertNotIn('EVENT_NOT_CLEAR',d['reasons'])
 def test_delta_books_remain_independent_and_unfilled_has_no_pnl(self):
  report=forward_cycle(self.root,[frame(0)],self.now,'FRESH')
  for name in ('low_delta','comparison'):
   path=self.root/'state'/('paper_book_'+name+'.json')
   self.assertTrue(path.exists())
   book=json.loads(path.read_text())
   self.assertIsNone(book['win_rate_pct'])
   self.assertEqual(book['broker_submissions'],0)
   self.assertEqual(book['open_count'],0)
 def test_second_fresh_frame_writes_distinct_paper_positions(self):
  forward_cycle(self.root,[frame(0)],self.now,'FRESH')
  res=forward_cycle(self.root,[frame(30)],self.now+timedelta(seconds=30),'FRESH')
  self.assertEqual(res['profiles']['low_delta']['open_count'],1)
  self.assertEqual(res['profiles']['comparison']['open_count'],1)
  low=json.loads((self.root/'state/paper_book_low_delta.json').read_text())
  comparison=json.loads((self.root/'state/paper_book_comparison.json').read_text())
  self.assertEqual(low['open_count'],1)
  self.assertEqual(comparison['open_count'],1)
  self.assertEqual(res['broker_submissions'],0)
 def test_stale_data_runs_audit_not_fake_trade(self):
  self.assertEqual(forward_cycle(self.root,[],self.now,'SNAPSHOT_STALE_OR_FUTURE')['paper_entries'],0)
  self.assertEqual(json.loads((self.root/'state/paper_book_low_delta.json').read_text())['open_count'],0)
 def test_bwb_independent_invalid_source_does_not_force_market_trade(self):
  with patch('scan_paper.option_bot_idle',return_value=True):
   r=execute_scan(self.root,self.now,[],{}, {},bwb_snapshots=[{'source':'UNVERIFIED'}])
  self.assertEqual(r['paper_entries'],0)
  self.assertEqual(r['bwb_0dte']['open_positions'],0)
  self.assertEqual(r['broker_submissions'],0)
  self.assertTrue((self.root/'state/bwb_coverage_audit.json').exists())

if __name__=='__main__':unittest.main()
