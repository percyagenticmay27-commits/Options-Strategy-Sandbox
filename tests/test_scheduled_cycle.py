import json,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import Mock
from forward_paper import atomic
from replay import iso
from scheduled_cycle import coordinate,INBOX

NOW=datetime(2026,11,12,19,0,tzinfo=timezone.utc)
class ScheduledCycleTests(unittest.TestCase):
 def setUp(self):
  tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
  self.root=Path(tmp.name);(self.root/'state').mkdir()
  self.scan=Mock(return_value={'status':'PAPER_ACTIVE','feed_status':'FRESH'})
  self.recover=Mock(return_value={'status':'RESUMED'})
 def pending(self):
  atomic(self.root/'state/refresh_request.json',{'status':'PENDING_CONNECTOR_REFRESH',
    'requested_at_utc':iso(NOW-timedelta(seconds=45))})
 def inbox(self,at):
  atomic(self.root/'state'/INBOX,{'source':'ROBINHOOD_MCP_RESEARCH','research_only':True,
      'order_submission_enabled':False,'generated_at_utc':iso(at),
      'symbols':{'SPX':{},'XSP':{}}})
 def test_pending_no_batch_does_not_repeat_old_scan(self):
  self.pending()
  result=coordinate(self.root,NOW,run_recovery=self.recover,run_scan=self.scan)
  self.assertEqual(result['status'],'PENDING_CONNECTOR_REFRESH')
  self.recover.assert_not_called();self.scan.assert_not_called()
 def test_old_batch_before_request_does_not_resume(self):
  self.pending();self.inbox(NOW-timedelta(seconds=50))
  result=coordinate(self.root,NOW,run_recovery=self.recover,run_scan=self.scan)
  self.assertEqual(result['reason'],'NO_NEW_BATCH_AFTER_REFRESH_REQUEST')
  self.recover.assert_not_called();self.scan.assert_not_called()
 def test_new_batch_runs_recovery_only_not_duplicate_scan(self):
  self.pending();self.inbox(NOW-timedelta(seconds=10))
  result=coordinate(self.root,NOW,run_recovery=self.recover,run_scan=self.scan)
  self.assertEqual(result['status'],'SCAN_COMPLETED')
  self.recover.assert_called_once();self.scan.assert_not_called()
 def test_refresh_fails_no_bypass(self):
  self.pending();self.inbox(NOW-timedelta(seconds=10))
  self.recover.return_value={'status':'REFRESH_RETRY_REQUIRED','reason':'STALE_RESEARCH_QUOTES'}
  result=coordinate(self.root,NOW,run_recovery=self.recover,run_scan=self.scan)
  self.assertEqual(result['status'],'PENDING_CONNECTOR_REFRESH')
  self.assertEqual(result['reason'],'STALE_RESEARCH_QUOTES')
  self.scan.assert_not_called()
 def test_regular_stale_cycle_becomes_pending(self):
  self.scan.return_value={'status':'PENDING_CONNECTOR_REFRESH','feed_status':'SNAPSHOT_STALE_OR_FUTURE'}
  result=coordinate(self.root,NOW,run_recovery=self.recover,run_scan=self.scan)
  self.assertEqual(result['status'],'PENDING_CONNECTOR_REFRESH')
  self.recover.assert_not_called();self.scan.assert_called_once()
 def test_stale_prior_result_without_request_preserves_wait(self):
  atomic(self.root/'state/latest.json',{'status':'PENDING_CONNECTOR_REFRESH','feed_status':'SNAPSHOT_STALE_OR_FUTURE'})
  result=coordinate(self.root,NOW,run_recovery=self.recover,run_scan=self.scan)
  self.assertEqual(result['status'],'PENDING_CONNECTOR_REFRESH')
  self.assertTrue((self.root/'state/refresh_request.json').exists())
  self.scan.assert_not_called()

if __name__=='__main__':unittest.main()
