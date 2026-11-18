"""Regression for independent bearish research, exact-leg handoff and coverage."""
import json
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from tests.test_replay import frame,dataset
from replay import compare,MIN_RETURN,RISK_CAP
from forward_paper import forward_cycle,atomic
from refresh_recovery import pending_second_quotes, recover_cycle
from contract_coverage import audit

NOW=datetime(2026,11,5,16,40,tzinfo=timezone.utc)

class BearishResearchTests(unittest.TestCase):
 def test_bearish_call_spread_requires_second_quote_and_does_not_pollute_control(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp)
   first=forward_cycle(root,[frame(0,direction='DOWN')],NOW,'FRESH')
   self.assertEqual(first['paper_entries'],0)
   self.assertEqual(first['bearish_shadow']['open_positions'],0)
   q=pending_second_quotes(root,NOW+timedelta(seconds=1))
   self.assertEqual(len(q),1)
   self.assertEqual(q[0]['profile'],'bearish_low_delta')
   self.assertEqual(set(q[0]['leg_ids']),{'XSP-call-short','XSP-call-long'})
   self.assertEqual(set(q[0]['first_quote_at_by_id']),set(q[0]['leg_ids']))
   second=forward_cycle(root,[frame(30,direction='DOWN')],NOW+timedelta(seconds=30),'FRESH')
   self.assertEqual(second['paper_entries'],0) # Original bull/neutral control unchanged
   self.assertEqual(second['bearish_shadow']['open_positions'],1)
   shadow=json.loads((root/'state/bearish_call_shadow_ledger.json').read_text())
   trade=shadow['profiles']['low_delta']['trades'][0]
   self.assertEqual({x['right'] for x in trade['legs']},{'C'})
   self.assertLessEqual(float(trade['risk']),float(RISK_CAP))
   self.assertTrue(shadow['paper_only'])
   self.assertEqual(shadow['broker_submissions'],0)
 def test_bearish_does_not_open_without_verified_new_quotes(self):
  initial=frame(0,direction='DOWN')
  stale=frame(30,direction='DOWN')
  for row in stale['quotes']:row['at']=initial['at']
  report=compare(dataset(initial,stale),bearish_shadow=True,bearish_only=True)
  self.assertEqual(report['profiles']['low_delta']['trades'],[])
 def test_control_remains_bullish_neutral_only(self):
  report=compare(dataset(frame(0,direction='DOWN'),frame(30,direction='DOWN')))
  self.assertFalse(report['profiles']['low_delta']['trades'])
 def test_bearish_shadow_does_not_trade_on_bullish_signal(self):
  report=compare(dataset(frame(0,direction='UP'),frame(30,direction='UP')),bearish_shadow=True,bearish_only=True)
  self.assertFalse(report['profiles']['low_delta']['trades'])
 def test_queued_exact_legs_and_timestamps_are_published_atomically(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp)
   current=datetime.now(timezone.utc)
   qtime=current-timedelta(seconds=5)
   atomic(root/'state/paper_ledger.json',{'profiles':{'comparison':{'decisions':[
    {'symbol':'XSP','at':qtime.isoformat(),'reason':'ORDER_QUEUED',
     'leg_ids':['a','b'],'quoted_at':{'a':qtime.isoformat(),'b':qtime.isoformat()},
     'direction':'UP','credit':'0.90','max_loss':'210.00'}]}}})
   def publish():pass
   def validate():return True,'CURRENT'
   def run():return {'status':'PAPER_ACTIVE','feed_status':'FRESH','generated_at_utc':current.isoformat(),
                      'broker_submissions':0,'order_submission_enabled':False,
                      'paper_entries':0,'new_paper_events':0}
   with patch('refresh_recovery.mode_at',return_value='ENTRY_AND_MANAGEMENT'):
    res=recover_cycle(root,now=current,publish=publish,validate=validate,run=run)
   self.assertEqual(res['status'],'RESUMED')
   self.assertTrue(res['second_quote_required'])
   request=json.loads((root/'state/second_quote_request.json').read_text())
   self.assertEqual(request['candidates'][0]['leg_ids'],['a','b'])
   self.assertTrue(request['requires_provider_timestamp_newer_than_first_batch'])
   self.assertEqual(request['broker_submissions'],0)
 def test_option_coverage_does_not_pass_just_on_contract_count(self):
  from tests.test_contract_coverage import NOW as CTIME
  def row(i,right,strike,delta):
   return {'id':f'{right}{i}','type':right,'strike_price':str(strike),'expiration_date':'2026-11-16',
     'state':'active','tradability':'tradable','settle_on_open':False,
     'metadata_provenance':{'source':'CBOE_PRODUCT_SPECIFICATION'},
     'quote':{'updated_at':'2026-11-16T17:27:30Z','bid_price':'0.40','ask_price':'0.45',
       'bid_size':'10','ask_size':'10','delta':str(delta)}}
  rows=[row(i,'put',730+i,-.12) for i in range(13)]
  result=audit({'symbols':{'XSP':{'contracts':rows}}},CTIME)['symbols']['XSP']
  self.assertFalse(result['coverage_target_met'])
  self.assertIn('INSUFFICIENT_CALL_STRIKES',result['missing_coverage'])
  self.assertIn('LOW_DELTA_CALL_WING_MISSING',result['missing_coverage'])

if __name__=='__main__':unittest.main()
