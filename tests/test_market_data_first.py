import unittest
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from decimal import Decimal as D
from completed_bars import bar_close_reason
from market_data_first import plan
from sniper import evaluate
from tests.test_sniper import evidence,NOW as SNIPER_NOW

NOW=datetime(2026,11,17,16,27,44,tzinfo=timezone.utc)

def market():
    bars=[]
    for day,count in [(16,78),(17,23)]:
        start=NOW.replace(day=day,hour=14,minute=30,second=0)
        for i in range(count):
            # Downward drift with genuine two-session warmup in a test fixture.
            price=D(740)-D(i)/10-(D(5) if day==17 else 0)
            bars.append(dict(begins_at=(start+timedelta(minutes=i*5)).isoformat(),session='reg',
                open_price=str(price+D('.05')),high_price=str(price+D('.2')),low_price=str(price-D('.2')),
                close_price=str(price),volume=100))
    raw={'source':'ROBINHOOD_MCP_RESEARCH','research_only':True,'order_submission_enabled':False,
         'generated_at_utc':NOW.isoformat(),'market_context':{
         'historicals':[dict(symbol='SPY',interval='5minute',bounds='regular',bars=bars)],
         'equity_quotes':[dict(quote=dict(symbol='SPY',last_trade_price='732.7',venue_last_trade_time=NOW.isoformat()))],
         'index_quotes':[dict(symbol=s,value=v,venue_timestamp=NOW.isoformat()) for s,v in [('SPX','7330'),('XSP','733')]]}}
    session=dict(source='ALPACA_CALENDAR',is_trading_day=True,date='2026-11-17',verified=True,
                 open='2026-11-17T14:30:00Z',close='2026-11-17T21:00:00Z')
    return raw,session

class CompletedBarTests(unittest.TestCase):
    def test_normal_164_second_old_latest_candle_is_valid(self):
        self.assertIsNone(bar_close_reason('2026-11-17T16:25:00Z',NOW))
    def test_old_future_and_off_grid_candles_rejected(self):
        for at in ['2026-11-17T16:20:00Z','2026-11-17T16:30:00Z','2026-11-17T16:25:03Z','2026-11-16T16:25:00Z']:
            self.assertIsNotNone(bar_close_reason(at,NOW))
    def test_brief_boundary_publication_grace_has_finite_end(self):
        t=NOW.replace(minute=30,second=30)
        self.assertIsNone(bar_close_reason('2026-11-17T16:25:00Z',t))
        self.assertIsNotNone(bar_close_reason('2026-11-17T16:25:00Z',t+timedelta(seconds=31)))
    def test_sniper_accepts_correct_bar_age_keeps_60_second_quote_gate(self):
        t=SNIPER_NOW+timedelta(minutes=2,seconds=44)
        p=evidence();p['generated_at_utc']=t.isoformat();p['underlying']['updated_at']=t.isoformat()
        for r in p['contracts']:r['quote']['updated_at']=t.isoformat()
        self.assertEqual(evaluate(p,t)['status'],'CANDIDATE')
        p['underlying']['updated_at']=(t-timedelta(seconds=61)).isoformat()
        self.assertEqual(evaluate(p,t)['reason'],'SNIPER_SPOT_STALE')
    def test_stale_option_quotes_still_cannot_be_candidates(self):
        t=SNIPER_NOW+timedelta(minutes=2)
        p=evidence();p['generated_at_utc']=t.isoformat();p['underlying']['updated_at']=t.isoformat()
        self.assertEqual(evaluate(p,t)['reason'],'NO_VERIFIED_0_TO_1_DTE_SPREAD')

class MarketDataFirstTests(unittest.TestCase):
    def test_two_session_warmup_makes_bwb_context_ready_before_11am(self):
        raw,session=market();r=plan(raw,session,NOW)
        self.assertEqual(r['status'],'TECHNICALS_EVALUATED')
        ctx=r['strategies']['BWB_SPX']['context']
        self.assertTrue(ctx['ready'])
        self.assertGreaterEqual(ctx['completed_bars'],53)
        self.assertEqual(ctx['current_session_bars'],23)
        self.assertEqual(ctx['direction'],'DOWN')
        self.assertIn('technical',r['sniper_inputs']['SPY'])
    def test_missing_reference_quote_requests_data_not_options(self):
        raw,session=market();raw['market_context']['equity_quotes']=[]
        r=plan(raw,session,NOW)
        self.assertEqual(r['strategies']['BWB_SPX']['status'],'FETCH_MARKET_DATA_REQUIRED')
        self.assertEqual(r['strategies']['BWB_SPX']['rights'],[])
    def test_todays_22_bars_do_not_count_as_53(self):
        raw,session=market();raw['market_context']['historicals'][0]['bars']=raw['market_context']['historicals'][0]['bars'][-23:]
        r=plan(raw,session,NOW)
        self.assertEqual(r['strategies']['BWB_SPX']['context']['completed_bars'],23)
        self.assertEqual(r['strategies']['BWB_SPX']['status'],'FETCH_MARKET_DATA_REQUIRED')
    def test_stale_batch_cannot_start_technicals_or_strike_plan(self):
        raw,session=market();raw['generated_at_utc']=(NOW-timedelta(seconds=61)).isoformat()
        r=plan(raw,session,NOW)
        self.assertEqual(r['status'],'FETCH_MARKET_DATA_REQUIRED')
        self.assertFalse(r['strategies'])
    def test_gap_cannot_create_a_trade_signal(self):
        raw,session=market();del raw['market_context']['historicals'][0]['bars'][-5]
        r=plan(raw,session,NOW)
        self.assertEqual(r['strategies']['SNIPER_SPY']['reason'],'BAR_GRID_GAP')
    def test_no_data_or_ledger_mutation(self):
        raw,session=market();before=deepcopy(raw)
        self.assertEqual(plan(raw,session,NOW)['broker_submissions'],0)
        self.assertEqual(raw,before)
