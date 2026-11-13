import tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from forward_paper import atomic
from replay import iso
from refresh_recovery import validate_new_provider_batch,pending_second_quotes

NOW=datetime(2026,11,13,16,24,30,tzinfo=timezone.utc)
class RecoveryHandoffTests(unittest.TestCase):
 def setUp(self):
  tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
  (self.root/'state').mkdir()
  self.raw=self.root/'state/incoming_robinhood_batch.json'
  self.batch(NOW-timedelta(seconds=5))
 def batch(self,at):
  atomic(self.raw,{'source':'ROBINHOOD_MCP_RESEARCH','research_only':True,
   'order_submission_enabled':False,'generated_at_utc':iso(at),'symbols':{'SPX':{},'XSP':{}}})
 def test_new_fresh_batch_accepted(self):
  self.assertEqual(validate_new_provider_batch(self.raw,self.root,NOW),(True,'NEW_VERIFIED_PROVIDER_BATCH'))
 def test_pending_request_demands_newer_timestamp(self):
  atomic(self.root/'state/refresh_request.json',{'status':'PENDING_CONNECTOR_REFRESH',
   'requested_at_utc':iso(NOW-timedelta(seconds=2))})
  self.assertEqual(validate_new_provider_batch(self.raw,self.root,NOW)[1],
      'RAW_CONNECTOR_BATCH_PRECEDES_REFRESH_REQUEST')
 def test_consumed_batch_not_used_twice(self):
  atomic(self.root/'state/last_consumed_provider_batch.json',
   {'generated_at_utc':iso(NOW-timedelta(seconds=5))})
  self.assertEqual(validate_new_provider_batch(self.raw,self.root,NOW)[1],
      'CONNECTOR_BATCH_ALREADY_CONSUMED')
 def test_old_batch_rejected_even_without_request(self):
  self.batch(NOW-timedelta(minutes=3))
  self.assertEqual(validate_new_provider_batch(self.raw,self.root,NOW)[1],
      'RAW_CONNECTOR_BATCH_STALE')
 def test_queued_spread_requests_new_quotes_only_if_recent(self):
  atomic(self.root/'state/paper_ledger.json',{'profiles':{'low_delta':{
   'decisions':[{'reason':'ORDER_QUEUED','at':iso(NOW-timedelta(seconds=12)),'symbol':'SPX'}]},
   'comparison':{'decisions':[{'reason':'TECHNICAL_SETUP_UNCONFIRMED',
    'at':iso(NOW-timedelta(seconds=12)),'symbol':'SPX'}]}}})
  queue=pending_second_quotes(self.root,NOW)
  self.assertEqual(len(queue),1)
  self.assertEqual(queue[0]['profile'],'low_delta')
  self.assertFalse(pending_second_quotes(self.root,NOW+timedelta(minutes=2)))
 def test_bwb_candidate_has_its_own_newer_leg_requirement(self):
  atomic(self.root/'state/bwb_paper_ledger.json',{'pending':{'XSP':{
   'at':iso(NOW-timedelta(seconds=5)),'leg_ids':['a','b','c']}}})
  q=pending_second_quotes(self.root,NOW)
  self.assertEqual(q[0]['profile'],'BWB_0DTE')
  self.assertEqual(q[0]['leg_ids'],['a','b','c'])
if __name__=='__main__':unittest.main()
