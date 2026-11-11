import unittest
from datetime import datetime,timezone,timedelta
from decimal import Decimal as D
from bwb import candidate,payoff
from tests.test_replay import frame

NOW=datetime(2026,11,11,17,0,tzinfo=timezone.utc)

def fixture(symbol='XSP'):
 f=frame('XSP','UP',NOW)
 # Fixtures are actual-shaped but wholly synthetic and must never be published
 # as the real provider's observation.
 f['session']['date']='2026-11-11';f['session']['open']='2026-11-11T14:30:00Z';f['session']['close']='2026-11-11T21:00:00Z'
 f['symbol']=symbol;f['spot']='100';f['spot_at']=NOW.isoformat();f['event_clear']=True;f['calendar_status']='CLEAR'
 f['bwb_context']={'source':'VERIFIED_RTH_VWAP_EMA50_VOLUME_PROFILE','ready':True,
   'direction':'UP','target':'105','ema50':'99','ema50_slope':'0.3','vwap':'99.5','relative_volume':'1.20'}
 quotes=[]
 for strike,bid,ask in [(100,'2.0','2.05'),(105,'0.6','0.65'),(107,'0.15','0.20')]:
  quotes.append({'id':f'{symbol}_{strike}','symbol':symbol,'root':'XSP' if symbol=='XSP' else 'SPXW',
   'expiry':'2026-11-11','strike':str(strike),'right':'C','verified':True,
   'exercise':'EUROPEAN','settlement':'PM_CASH','multiplier':100,
   'last_trade':'2026-11-11T21:00:00Z','settles_at':'2026-11-11T21:00:00Z',
   'bid':bid,'ask':ask,'bid_size':10,'ask_size':10,'delta':'0.4','at':NOW.isoformat()})
 f['quotes']=quotes;return f

class BWBTests(unittest.TestCase):
 def test_bwb_unequal_wing_bounded_payoff(self):
  gain,loss,body=payoff((D('100'),D('105'),D('107')),'C',D('-1.05'))
  self.assertGreater(body,D('0'));self.assertGreater(loss,D('0'));self.assertLess(gain,D('500'))
 def test_fresh_three_strike_structure_under_500(self):
  r=candidate(fixture(),NOW)
  self.assertEqual(r['status'],'CANDIDATE');self.assertLessEqual(D(r['purchase_amount_usd']),500)
  self.assertEqual(r['quantity'],[1,-2,1]);self.assertFalse(r['paper_entry'])
 def test_no_target_blocks(self):
  f=fixture();f['bwb_context'].pop('target')
  self.assertEqual(candidate(f,NOW)['reason'],'BWB_DIRECTION_OR_TARGET_UNCONFIRMED')
 def test_no_borrowed_low_delta_signal(self):
  f=fixture();f['technical_clear']=False
  self.assertEqual(candidate(f,NOW)['status'],'CANDIDATE')
 def test_after_1230_is_rejected(self):
  f=fixture();late=NOW.replace(hour=19,minute=30)
  self.assertEqual(candidate(f,late)['reason'],'OUTSIDE_BWB_ENTRY_WINDOW')
 def test_no_over_500(self):
  f=fixture();f['quotes'][0]['ask']='50'
  self.assertEqual(candidate(f,NOW)['status'],'REJECTED')

class BWBLifecycleTests(unittest.TestCase):
 def test_requires_second_newer_held_leg_quotes(self):
  from bwb_cycle import run
  from tempfile import TemporaryDirectory
  from pathlib import Path
  f=fixture()
  with TemporaryDirectory() as root:
   a=run(root,[f],NOW)
   self.assertFalse(a['open']);self.assertEqual(len(a['pending']),1)
   now=NOW+timedelta(seconds=11)
   f2=fixture()
   for q in f2['quotes']:q['at']=(NOW+timedelta(seconds=4)).isoformat()
   f2['spot_at']=(NOW+timedelta(seconds=4)).isoformat()
   a=run(root,[f2],now)
   self.assertEqual(len(a['open']),1)
   self.assertEqual(a['events'][0]['action'],'PAPER_BUY')
   self.assertLessEqual(D(a['open'][0]['maximum_loss_usd']),D('500'))
   self.assertEqual(a['broker_submissions'],0)
   b=run(root,[f2],now)
   self.assertEqual(sum(e['action']=='PAPER_BUY' for e in b['events']),1)
 def test_no_frame_records_missing_data(self):
  from bwb_cycle import run
  from tempfile import TemporaryDirectory
  with TemporaryDirectory() as root:
   a=run(root,[],NOW)
   self.assertEqual(a['latest_evaluations'][0]['status'],'DATA_UNAVAILABLE')
   self.assertFalse(a['open'])
