import copy,json,unittest
from pathlib import Path
from replay import timestamp
from scan_paper import contract_metadata_current

class QuoteMetadataGateTests(unittest.TestCase):
 def setUp(self):
  self.now=timestamp('2026-11-09T16:46:58Z')
  self.row={
   'id':'contract-123','instrument_id':'contract-123','state':'active',
   'tradability':'tradable','settle_on_open':False,
   'trade_value_multiplier':'100','exercise_style':'european',
   'metadata_provenance':{'source':'CBOE_PRODUCT_SPECIFICATION'},
   'quote':{'instrument_id':'contract-123','updated_at':'2026-11-09T16:46:40Z',
    'bid_price':'0.40','ask_price':'0.45','bid_size':100,'ask_size':100,
    'mark_price':'0.425','delta':'-0.12','gamma':'0.001',
    'theta':'-1.0','vega':'0.05','rho':'-0.001',
    'implied_volatility':'0.25','volume':10,'open_interest':20}}
 def test_complete_provider_greeks_and_contract_metadata_are_required(self):
  self.assertTrue(contract_metadata_current(self.row,self.now))
  for field in ('gamma','delta','theta','vega','implied_volatility','bid_price','ask_price','updated_at'):
   with self.subTest(field=field):
    x=copy.deepcopy(self.row);x['quote'][field]=None
    self.assertFalse(contract_metadata_current(x,self.now))
 def test_stale_quote_and_bad_provenance_fail(self):
  r=copy.deepcopy(self.row);r['quote']['updated_at']='2026-11-06T16:30:00Z'
  self.assertFalse(contract_metadata_current(r,self.now))
  r=copy.deepcopy(self.row);r['metadata_provenance']['source']='UNVERIFIED'
  self.assertFalse(contract_metadata_current(r,self.now))
