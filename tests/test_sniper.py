import unittest
from datetime import datetime,timezone,timedelta
from decimal import Decimal as D
from sniper import evaluate,ScanPolicy

NOW=datetime(2026,11,11,16,20,tzinfo=timezone.utc)

def evidence(*,symbol='SPY',spot='600',prev_high='598',close='601',vwap='599',ema20='596',strike='605',expiry='2026-12-04',fresh=True,earnings='CLEAR'):
 at=(NOW-timedelta(seconds=8 if fresh else 100)).isoformat()
 q=lambda x,i,delta:dict(instrument_id=i,bid_price=x[0],ask_price=x[1],bid_size=10,ask_size=12,updated_at=at,delta=delta,implied_volatility='0.23',open_interest=300,volume=150)
 return {'source':'ROBINHOOD_MCP_RESEARCH','paper_only':True,'research_only':True,'order_submission_enabled':False,
 'broker_execution_enabled':False,'generated_at_utc':at,'symbol':symbol,'underlying':{'last_price':spot,'updated_at':at},
 'technical':{'bar_close':close,'bar_close_at':at,'prior_completed_high':prev_high,'prior_completed_low':'597','vwap':vwap,'ema20':ema20,'ema20_prior':'595', 'bars_completed':22,'source':'SPY_5MINUTE_RTH'},
 'events':{'earnings_clearance':earnings,'dividend_clearance':'CLEAR',
     'verified_at_utc':at,'clear_through_date':'2026-12-18','provenance':'VERIFIED_ISSUER_CORPORATE_ACTION_CALENDAR'},
 'contracts':[dict(instrument_id='short',symbol=symbol,expiration_date=expiry,right='CALL',strike_price=strike,
                    exercise_style='AMERICAN',settlement_type='PHYSICAL',multiplier=100,state='active',tradability='tradable',quote=q(('3.90','4.05'),'short','0.45')),
              dict(instrument_id='long',symbol=symbol,expiration_date=expiry,right='CALL',strike_price='600',
                    exercise_style='AMERICAN',settlement_type='PHYSICAL',multiplier=100,state='active',tradability='tradable',quote=q(('4.30','4.45'),'long','0.60'))]}

class SniperTests(unittest.TestCase):
 def test_no_data_is_still_a_daily_evaluation(self):
  r=evaluate({},NOW);self.assertEqual(r['status'],'DATA_UNAVAILABLE');self.assertFalse(r['paper_entry'])
 def test_matching_breakout_returns_candidate_with_risk(self):
  r=evaluate(evidence(),NOW)
  self.assertEqual(r['status'],'CANDIDATE');self.assertEqual(r['strategy'],'SNIPER')
  self.assertEqual(r['structure'],'CALL_DEBIT_SPREAD');self.assertLessEqual(D(r['maximum_loss_usd']),D('100'))
 def test_bearish_breakout_put_debit_spread(self):
  e=evidence(spot='590',close='590',vwap='595',ema20='598',prev_high='600')
  e['technical']['prior_completed_low']='591'
  e['technical']['ema20_prior']='599'
  for row in e['contracts']:
   row['right']='PUT'
   if row['instrument_id']=='long':row['strike_price']='600'
   if row['instrument_id']=='short':row['strike_price']='595'
  r=evaluate(e,NOW)
  self.assertEqual(r['status'],'CANDIDATE')
  self.assertEqual(r['structure'],'PUT_DEBIT_SPREAD')

 def test_no_breakout_is_rejected_not_relabelled(self):
  r=evaluate(evidence(prev_high='602'),NOW);self.assertEqual(r['status'],'REJECTED');self.assertEqual(r['reason'],'BREAKOUT_UNCONFIRMED')
 def test_stale_contract_or_underlying_rejected(self):
  r=evaluate(evidence(fresh=False),NOW);self.assertEqual(r['status'],'DATA_UNAVAILABLE')
 def test_unverified_earnings_blocks(self):
  r=evaluate(evidence(earnings='UNKNOWN'),NOW);self.assertEqual(r['reason'],'CORPORATE_EVENT_CLEARANCE_UNAVAILABLE')
 def test_stale_corporate_event_evidence_blocks(self):
  e=evidence();e['events']['verified_at_utc']=(NOW-timedelta(days=1)).isoformat()
  self.assertEqual(evaluate(e,NOW)['reason'],'CORPORATE_EVENT_CLEARANCE_STALE')
 def test_event_clearance_must_cover_expiration(self):
  e=evidence();e['events']['clear_through_date']='2026-11-20'
  self.assertEqual(evaluate(e,NOW)['reason'],'NO_VERIFIED_14_TO_30_DTE_SPREAD')

 def test_bad_dte_blocks(self):
  r=evaluate(evidence(expiry='2026-11-13'),NOW);self.assertEqual(r['reason'],'NO_VERIFIED_14_TO_30_DTE_SPREAD')
 def test_not_automatic_fill_without_second_quote_batch(self):
  r=evaluate(evidence(),NOW);self.assertFalse(r['paper_entry']);self.assertEqual(r['execution_status'],'PENDING_NEWER_QUOTES')
 def test_one_symbol_does_not_cause_cross_book_risk(self):
  self.assertEqual(ScanPolicy().max_risk,D('100'))

class SniperCycleTests(unittest.TestCase):
 def test_two_separate_actual_quote_batches_needed_for_paper_buy(self):
  import tempfile
  from pathlib import Path
  from sniper_cycle import run
  with tempfile.TemporaryDirectory() as d:
   base=Path(d)
   t=NOW
   first=evidence()
   r=run(base,first,t)
   self.assertFalse(r['open']);self.assertEqual(len(r['pending_candidates']),1)
   t2=t+timedelta(seconds=11)
   second=evidence()
   stamp=(NOW+timedelta(seconds=3)).isoformat()
   second['generated_at_utc']=stamp
   second['underlying']['updated_at']=stamp
   second['technical']['bar_close_at']=stamp
   for row in second['contracts']:row['quote']['updated_at']=stamp
   r2=run(base,second,t2)
   self.assertEqual(len(r2['open']),1)
   self.assertEqual(r2['events'][0]['action'],'PAPER_BUY')
   self.assertEqual(r2['broker_submissions'],0)
   followup=run(base,second,t2)
   self.assertEqual(sum(e['action']=='PAPER_BUY' for e in followup['events']),1)
   self.assertEqual(followup['broker_submissions'],0)
 def test_missing_data_still_records_daily_scan(self):
  import tempfile
  from sniper_cycle import run
  with tempfile.TemporaryDirectory() as d:
   r=run(d,{},NOW)
   self.assertEqual(r['daily_evaluations']['2026-11-11']['status'],'DATA_UNAVAILABLE')
   self.assertFalse(r['open'])
