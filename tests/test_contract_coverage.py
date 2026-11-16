import unittest
from datetime import datetime,timezone
from contract_coverage import audit,request_plan
from replay import RISK_CAP,MIN_RETURN
NOW=datetime(2026,11,16,17,27,31,tzinfo=timezone.utc)
class CoverageTests(unittest.TestCase):
 def test_plan_preserves_limits_and_requires_wings_and_second_fresh_leg_quotes(self):
  plan=request_plan(NOW)
  self.assertEqual(plan['max_risk_per_book_usd'],str(RISK_CAP))
  self.assertEqual(plan['minimum_net_profit_to_max_loss'],str(MIN_RETURN))
  self.assertEqual(plan['max_quote_age_seconds'],60)
  self.assertEqual(plan['symbols']['XSP']['protective_wing_offsets_points'],['1','2','3'])
  self.assertEqual(plan['symbols']['SPX']['protective_wing_offsets_points'],['5','10','15'])
  self.assertIn('ONLY_IF',plan['second_quote_batch'])
 def test_fresh_but_two_contracts_is_insufficient_coverage(self):
  def contract(i,strike,delta):
   return {'id':str(i),'type':'put','strike_price':str(strike),'expiration_date':'2026-11-16',
     'state':'active','tradability':'tradable','settle_on_open':False,
     'metadata_provenance':{'source':'CBOE_PRODUCT_SPECIFICATION'},
     'quote':{'updated_at':'2026-11-16T17:27:30Z','bid_price':'0.40',
       'ask_price':'0.45','bid_size':'10','ask_size':'10','delta':str(delta)}}
  data={'symbols':{'SPX':{'contracts':[]},'XSP':{'contracts':[contract('a',744,-.14),contract('b',745,-.20)]}}}
  result=audit(data,NOW)['symbols']['XSP']
  self.assertFalse(result['coverage_target_met'])
  self.assertEqual(result['fresh_executable_contracts'],2)
  self.assertEqual(result['potential_wing_pairs']['low_delta'],0)

 def test_published_metadata_is_counted_only_with_real_quotes(self):
  from copy import deepcopy
  def row(i,strike,delta):
   return {'id':str(i),'type':'put','strike_price':str(strike),'expiration_date':'2026-11-16',
     'state':'active','tradability':'tradable','settle_on_open':False,
     'metadata_provenance':{'source':'CBOE_PRODUCT_SPECIFICATION'},
     'quote':{'updated_at':'2026-11-16T17:27:30Z','bid_price':'0.40',
       'ask_price':'0.45','bid_size':'10','ask_size':'10','delta':str(delta)}}
  rows=[row(i,734+i, -0.12 if i<6 else -0.19) for i in range(16)]
  target=audit({'symbols':{'XSP':{'contracts':rows}}},NOW)['symbols']['XSP']
  self.assertTrue(target['coverage_target_met'])
  self.assertGreater(target['potential_wing_pairs']['comparison'],0)
  self.assertEqual(target['fresh_executable_contracts'],16)
 def test_stale_quotes_do_not_count(self):
  r={'symbols':{'XSP':{'contracts':[{'id':'x','type':'put','strike_price':'745',
    'state':'active','tradability':'tradable','settle_on_open':False,
    'metadata_provenance':{'source':'CBOE_PRODUCT_SPECIFICATION'},
    'quote':{'updated_at':'2026-11-16T17:20:30Z','bid_price':'1',
             'ask_price':'1.1','bid_size':'1','ask_size':'1','delta':'-.14'}}]}}}
  self.assertEqual(audit(r,NOW)['symbols']['XSP']['fresh_executable_contracts'],0)
if __name__=='__main__':unittest.main()
