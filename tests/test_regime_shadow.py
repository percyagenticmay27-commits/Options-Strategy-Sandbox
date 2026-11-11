import unittest,tempfile
from datetime import datetime,timezone
from decimal import Decimal as D
from regime_shadow import classify,observe,aggregate
from tests.test_bwb import fixture

NOW=datetime(2026,11,11,16,0,tzinfo=timezone.utc) # 10am CT
class ShadowTests(unittest.TestCase):
 def test_all_four_evaluated_without_trades(self):
  with tempfile.TemporaryDirectory() as tmp:
   rows=observe(tmp,[fixture()],NOW)
   self.assertEqual({r['strategy'] for r in rows['latest_evaluations']},
                    {'ZEBRA','SAFE_INCOME','SNIPER_SUPPORT','CREDIT_ROLLS_GROWTH'})
   self.assertEqual(rows['stats']['closed_verified'],0)
   self.assertEqual(rows['stats']['win_rate_pct'],None)
   self.assertEqual(rows['broker_submissions'],0)
   self.assertFalse(rows['open'])
   self.assertEqual(len(observe(tmp,[fixture()],NOW)['observations']),4)
 def test_income_requires_iv_rank_and_range_evidence(self):
  f=fixture();f['spot_at']=datetime(2026,11,11,15,50,tzinfo=timezone.utc).isoformat()
  r=classify('SAFE_INCOME',f,datetime(2026,11,11,15,50,tzinfo=timezone.utc))
  self.assertEqual(r['reason'],'IV_RANK_EXPECTED_MOVE_AND_RANGE_NOT_VERIFIED')
 def test_support_requires_verified_level_not_just_vwap(self):
  f=fixture();f['spot_at']=NOW.isoformat()
  r=classify('SNIPER_SUPPORT',f,NOW)
  self.assertEqual(r['reason'],'SUPPORT_RSI_AND_MACRO_TREND_NOT_VERIFIED')
 def test_incomplete_fills_excluded_from_win_rate(self):
  r=aggregate({'completed':[{'realized_pnl_usd':'500.00'}]})
  self.assertEqual(r['closed_verified'],0)
  self.assertEqual(r['unverified_fills_excluded'],1)
