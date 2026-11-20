"""Directional provider handoffs: puts for bullish and calls for bearish.

These are research collection and mock paper tests, never broker orders.
"""
import unittest
from unittest.mock import patch
from tests.test_market_data_first import NOW,market
from market_data_first import plan
from contract_coverage import request_plan
from tests.test_replay import frame,dataset
from replay import compare

class DirectionalShortingCollectionTests(unittest.TestCase):
 def check_signal(self,direction):
  raw,session=market()
  technical={"direction":direction,"technical_clear":direction in ('UP','DOWN'),
             "signal_reason":'VWAP_BREAKDOWN_OR_RESISTANCE' if direction=='DOWN' else
                             'VWAP_RECLAIM_OR_SUPPORT' if direction=='UP' else
                             'VWAP_SETUP_UNCONFIRMED'}
  with patch('market_data_first.chart_context',return_value=technical):
   return plan(raw,session,NOW)
 def test_bearish_down_requests_only_otm_calls_and_higher_wings(self):
  r=self.check_signal('DOWN')
  for sym in ('SPX','XSP'):
   bearish=r['strategies']['BEAR_CALL_CREDIT_SHADOW_'+sym]
   bullish=r['strategies']['CREDIT_'+sym]
   self.assertEqual(bearish['status'],'COLLECT_OPTIONS')
   self.assertEqual(bearish['rights'],['call'])
   self.assertEqual(bearish['spread'],'BEAR_CALL_CREDIT')
   self.assertIn('HIGHER_PROTECTIVE_WINGS',bearish['strike_policy'])
   self.assertTrue(bearish['second_quote_required'])
   self.assertEqual(bearish['max_defined_risk_per_book_usd'],'400')
   self.assertEqual(bearish['min_net_profit_to_max_loss'],'0.15')
   self.assertEqual(bullish['status'],'WAIT')
   self.assertEqual(bullish['rights'],[])
  self.assertEqual(r['broker_submissions'],0)
 def test_bullish_up_requests_puts_and_no_bearish_contracts(self):
  r=self.check_signal('UP')
  for sym in ('SPX','XSP'):
   bullish=r['strategies']['CREDIT_'+sym]
   bearish=r['strategies']['BEAR_CALL_CREDIT_SHADOW_'+sym]
   self.assertEqual(bullish['status'],'COLLECT_OPTIONS')
   self.assertEqual(bullish['rights'],['put'])
   self.assertEqual(bearish['status'],'WAIT')
   self.assertEqual(bearish['rights'],[])
 def test_unconfirmed_signal_cannot_collect_or_open_a_directional_spread(self):
  r=self.check_signal('UNCONFIRMED')
  for sym in ('SPX','XSP'):
   for name in ('CREDIT_','BEAR_CALL_CREDIT_SHADOW_'):
    self.assertEqual(r['strategies'][name+sym]['status'],'WAIT')
    self.assertEqual(r['strategies'][name+sym]['rights'],[])
 def test_stale_market_batch_has_no_direction_or_contract_approval(self):
  raw,session=market()
  raw['generated_at_utc']='2026-11-17T16:20:00Z'
  r=plan(raw,session,NOW)
  self.assertEqual(r['status'],'FETCH_MARKET_DATA_REQUIRED')
  self.assertEqual(r['strategies'],{})
 def test_both_rights_in_underlying_collection_fallback_not_proof_of_execution(self):
  p=request_plan(NOW)
  for sym in ('SPX','XSP'):
   self.assertEqual(p['symbols'][sym]['rights'],['put','call'])
   self.assertIn('HIGHER_STRIKE_CALL_WINGS',p['symbols'][sym]['wing_policy'])
  self.assertEqual(p['broker_submissions'],0)
 def test_bear_call_paper_experiment_still_requires_two_live_quote_observations(self):
  first=compare(dataset(frame(0,direction='DOWN')),bearish_shadow=True,bearish_only=True)
  self.assertEqual(first['profiles']['low_delta']['trades'],[])
  second=compare(dataset(frame(0,direction='DOWN'),frame(30,direction='DOWN')),bearish_shadow=True,bearish_only=True)
  self.assertEqual({l['right'] for l in second['profiles']['low_delta']['trades'][0]['legs']},{'C'})
  self.assertEqual(compare(dataset(frame(0,direction='DOWN'),frame(30,direction='DOWN')))['profiles']['low_delta']['trades'],[])
if __name__=='__main__':unittest.main()
