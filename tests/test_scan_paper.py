import copy,json,tempfile,unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
from scan_paper import execute_scan,choose_snapshot
from tests.test_replay import frame

UTC=timezone.utc
NOW=datetime(2026,11,9,16,46,58,tzinfo=UTC)

class ScanPaperIntegrationTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.root=Path(self.t.name)
  (self.root/'state').mkdir();(self.root/'results').mkdir()
 def test_stale_snapshot_requests_refresh_without_fake_fills(self):
  stale={'source':'ROBINHOOD_MCP_RESEARCH','research_only':True,
        'order_submission_enabled':False,'paper_only':True,'generated_at_utc':'2026-11-06T16:00:00Z'}
  outcome=execute_scan(self.root,NOW,[stale],{}, {'verified_at':NOW.isoformat()})
  self.assertEqual(outcome['status'],'PENDING_CONNECTOR_REFRESH')
  self.assertEqual(outcome['paper_entries'],0)
  request=json.loads((self.root/'state/refresh_request.json').read_text())
  self.assertEqual(request['reason'],'SNAPSHOT_STALE_OR_FUTURE')
 def test_latest_verified_source_selected_and_invalid_rejected(self):
  bad={'source':'EXTERNAL','paper_only':True,'research_only':True,'order_submission_enabled':False,'generated_at_utc':NOW.isoformat()}
  good={'source':'ROBINHOOD_MCP_RESEARCH','paper_only':True,'research_only':True,'order_submission_enabled':False,'broker_execution_enabled':False,'live_trading_enabled':False,'generated_at_utc':(NOW-timedelta(seconds=5)).isoformat()}
  self.assertEqual(choose_snapshot([bad,good],NOW),good)
  self.assertIsNone(choose_snapshot([bad],NOW))
 def test_technical_gate_remains_in_force_in_live_wiring(self):
  s={'date':'2026-11-09','open':'2026-11-09T14:30:00Z','close':'2026-11-09T21:00:00Z','verified':True,'source':'ALPACA_CALENDAR','is_trading_day':True}
  payload={'source':'ROBINHOOD_MCP_RESEARCH','research_only':True,'paper_only':True,
   'order_submission_enabled':False,'broker_execution_enabled':False,'live_trading_enabled':False,'generated_at_utc':NOW.isoformat(),
   'symbols':{},'market_context':{}}
  # Missing metadata is never fabricated into a paper entry.
  r=execute_scan(self.root,NOW,[payload],s,{'verified_at':NOW.isoformat()})
  self.assertEqual(r['paper_entries'],0)
  self.assertEqual(r['status'],'PENDING_CONNECTOR_REFRESH')

 def test_sniper_is_reported_daily_when_index_entry_blocked(self):
  from datetime import timezone
  s={'date':'2026-11-09','open':'2026-11-09T14:30:00Z','close':'2026-11-09T21:00:00Z','verified':True,'source':'ALPACA_CALENDAR','is_trading_day':True}
  result=execute_scan(self.root,NOW,[],s,{'verified_at':NOW.isoformat()})
  self.assertEqual(result['sniper']['status'],'DATA_UNAVAILABLE')
  self.assertEqual(result['sniper']['reason'],'SNIPER_EQUITY_RESEARCH_NOT_PUBLISHED')
  self.assertTrue((self.root/'state/sniper_daily_evaluation.json').exists())
