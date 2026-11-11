import unittest
from datetime import datetime,timedelta,timezone
from decimal import Decimal as D
from bwb_context import context_for
from tests.test_bwb import fixture,NOW

class BWBContextTests(unittest.TestCase):
 def research(self,count=54):
  open_at=datetime(2026,11,10,14,30,tzinfo=timezone.utc)
  bars=[]
  for day in (0,1):
   for i in range(40 if day==0 else count-40):
    at=open_at+timedelta(days=day,minutes=5*i)
    close=D('97')+D(day)*D('1.0')+D(i)*D('.09')
    bars.append({'begins_at':at.isoformat(),'session':'reg','high_price':str(close+D('.15')),
                 'low_price':str(close-D('.1')),'close_price':str(close),'volume':1000})
  return {'market_context':{'historicals':[{'symbol':'SPY','interval':'5minute','bounds':'regular','bars':bars}],
   'equity_quotes':[{'symbol':'SPY','quote':{'last_trade_price':'100','venue_last_trade_time':NOW.isoformat()}}]}}
 def test_missing_53_bars_blocks(self):
  self.assertFalse(context_for(fixture(),self.research(count=52),NOW)['ready'])
 def test_no_quote_disallows_projection(self):
  source=self.research();source['market_context']['equity_quotes']=[]
  self.assertFalse(context_for(fixture(),source,NOW)['ready'])
 def test_no_synthetic_fixed_index_ratio_when_missing_index(self):
  frame=fixture();frame['spot_at']='2026-11-10T10:00:00Z'
  self.assertFalse(context_for(frame,self.research(),NOW)['ready'])
