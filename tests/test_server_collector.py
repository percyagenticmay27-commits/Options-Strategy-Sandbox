"""No fake SPX index spot, Greeks, credentials or brokerage execution."""
import unittest
import urllib.error
from datetime import datetime,timedelta,timezone
from server_collector import collect,get_json,ALPACA_DATA,ALPACA_PAPER,ProviderIssue

NOW=datetime(2026,11,20,16,20,tzinfo=timezone.utc)
SESSION={'source':'ALPACA_CALENDAR','date':'2026-11-20','is_trading_day':True}

class CollectorTests(unittest.TestCase):
 def fetch(self,base,path,params):
  if path=='/v2/stocks/SPY/bars':
   return {'bars':[{'t':(NOW-timedelta(minutes=10)).isoformat()},
                   {'t':(NOW-timedelta(minutes=5)).isoformat()}]}
  if path=='/v2/options/contracts':
   root=params['root_symbol']
   return {'option_contracts':[{'symbol':root+'261120C00700000',
       'root_symbol':root,'expiration_date':'2026-11-20'}]}
  if path=='/v1beta1/options/snapshots':
   raise ProviderIssue('PROVIDER_403_OPRA_ENTITLEMENT_OR_AGREEMENT_REQUIRED')
  raise AssertionError(path)
 def test_unsigned_opra_and_no_spot_never_publish_actionable_quote(self):
  r=collect(NOW,self.fetch,session=SESSION)
  self.assertEqual(r['status'],'PROVIDER_EVIDENCE_INCOMPLETE')
  self.assertFalse(r['published_actionable_provider_batch'])
  self.assertEqual(r['broker_submissions'],0)
  self.assertIn('PROVIDER_403_OPRA_ENTITLEMENT_OR_AGREEMENT_REQUIRED',r['blockers'])
  self.assertIn('AUTHORIZED_TIMESTAMPED_INDEX_SPOT_PROVIDER_NOT_CONFIGURED',r['blockers'])
  self.assertEqual(r['provider_capabilities']['SPY_5MIN_IEX']['status'],'AVAILABLE')
  self.assertEqual(r['provider_capabilities']['XSP_PM_SETTLED_CONTRACT_LOOKUP']['status'],'AVAILABLE')
 def test_opra_available_still_cannot_substitute_spy_for_cash_index(self):
  def fetch(base,path,params):
   if path.endswith('/snapshots'):
    occ=params['symbols'];return {'snapshots':{occ:{'latestQuote':{'t':NOW.isoformat()},'greeks':{'delta':0.2}}}}
   return self.fetch(base,path,params)
  r=collect(NOW,fetch,session=SESSION)
  self.assertEqual(r['status'],'PROVIDER_EVIDENCE_INCOMPLETE')
  self.assertFalse(r['published_actionable_provider_batch'])
  self.assertIn('AUTHORIZED_TIMESTAMPED_INDEX_SPOT_PROVIDER_NOT_CONFIGURED',r['blockers'])
 def test_stale_spy_chart_blocks_even_if_http_succeeded(self):
  def stale(base,path,params):
   if path.endswith('/bars'):return {'bars':[{'t':(NOW-timedelta(hours=1)).isoformat()}]}
   return self.fetch(base,path,params)
  r=collect(NOW,stale,session=SESSION)
  self.assertIn('SPY_FIVE_MINUTE_BARS_STALE',r['blockers'])
 def test_outside_session_no_data_fetch(self):
  early=NOW-timedelta(hours=4)
  r=collect(early,lambda *_:self.fail('Should not fetch outside market hours'),session=SESSION)
  self.assertEqual(r['status'],'OUTSIDE_REGULAR_TRADING_HOURS')
 def test_no_approved_trading_endpoints(self):
  env={'APCA_API_KEY_ID':'test','APCA_API_SECRET_KEY':'not-a-real-key'}
  for base,path in [(ALPACA_PAPER,'/v2/orders'),(ALPACA_DATA,'/v2/stocks/SPX/trades'),
                    ('https://other-service.invalid','/v2/options/contracts')]:
   with self.assertRaises(ProviderIssue):get_json(base,path,{},env)
 def test_missing_auth_fails_before_network(self):
  with self.assertRaisesRegex(ProviderIssue,'ALPACA_READ_ONLY_CREDENTIALS_MISSING'):
   get_json(ALPACA_DATA,'/v2/stocks/SPY/bars',{},env={})

if __name__=='__main__':unittest.main()
