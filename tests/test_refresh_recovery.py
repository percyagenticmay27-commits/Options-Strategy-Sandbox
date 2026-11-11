"""Verify actual connector publishing resumes paper scans without permitting stale fills."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from datetime import datetime,timezone
from refresh_recovery import recover_cycle

NOW=datetime(2026,11,10,19,30,tzinfo=timezone.utc) # 1:30 CT, outside entry

class RefreshRecoveryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name)
 def test_successful_published_batch_resumes_once_and_records_result(self):
  published=Mock();validate=Mock(return_value=(True,'CURRENT'))
  ran=Mock(return_value={'status':'PAPER_ACTIVE','paper_entries':0,'broker_submissions':0,'generated_at_utc':datetime.now(timezone.utc).isoformat(),'feed_status':'FRESH'})
  result=recover_cycle(self.root,now=NOW,publish=published,validate=validate,run=ran)
  self.assertEqual(result['status'],'RESUMED')
  self.assertEqual(result['mode'],'MANAGEMENT_ONLY')
  self.assertEqual(ran.call_count,1)
  self.assertEqual(published.call_count,1)
  self.assertEqual(result['broker_submissions'],0)
  self.assertEqual(json.loads((self.root/'state/refresh_recovery.json').read_text())['status'],'RESUMED')
 def test_failed_freshness_stops_before_scan_and_creates_refresh_request(self):
  result=recover_cycle(self.root,now=NOW,publish=Mock(),validate=Mock(return_value=(False,'STALE_RESEARCH_QUOTES')),run=Mock())
  self.assertEqual(result['status'],'REFRESH_RETRY_REQUIRED')
  self.assertEqual(result['reason'],'STALE_RESEARCH_QUOTES')
  self.assertEqual(json.loads((self.root/'state/refresh_request.json').read_text())['status'],'PENDING_CONNECTOR_REFRESH')
 def test_failed_publisher_never_calls_scanner(self):
  run=Mock()
  def fail():raise ValueError('ACTUAL_PROVIDER_DATA_UNAVAILABLE')
  result=recover_cycle(self.root,now=NOW,publish=fail,validate=Mock(),run=run)
  self.assertEqual(result['status'],'REFRESH_RETRY_REQUIRED')
  run.assert_not_called()
 def test_a_second_stale_scan_is_not_reported_as_success(self):
  ran=Mock(return_value={'status':'PENDING_CONNECTOR_REFRESH','paper_entries':0,'broker_submissions':0,'generated_at_utc':datetime.now(timezone.utc).isoformat(),'feed_status':'FRESH'})
  result=recover_cycle(self.root,now=NOW,publish=Mock(),validate=Mock(return_value=(True,'CURRENT')),run=ran)
  self.assertEqual(result['status'],'REFRESH_RETRY_REQUIRED')
  self.assertEqual(ran.call_count,1)
 def test_previous_run_report_cannot_count_as_new_refresh(self):
  stale=Mock(return_value={'status':'PAPER_ACTIVE','paper_entries':0,'broker_submissions':0,
      'generated_at_utc':'2026-11-10T15:30:00Z','feed_status':'FRESH'})
  result=recover_cycle(self.root,now=NOW,publish=Mock(),validate=Mock(return_value=(True,'CURRENT')),run=stale)
  self.assertEqual(result['status'],'REFRESH_RETRY_REQUIRED')
  self.assertEqual(result['reason'],'STALE_DURING_CYCLE')

 def test_broker_submissions_must_remain_zero(self):
  with self.assertRaises(ValueError):
   recover_cycle(self.root,now=NOW,publish=Mock(),validate=Mock(return_value=(True,'CURRENT')),run=Mock(return_value={'status':'PAPER_ACTIVE','broker_submissions':1}))
