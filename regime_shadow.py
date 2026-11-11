"""Read-only four-strategy regime shadow observer.

Records candidate decisions and opportunity coverage separately from actual
local-paper bots. Never creates fills from indicators or stale quotes and never
mutates other strategy files. All times are exchange-aware via America/Chicago.
"""
from __future__ import annotations
from datetime import datetime, time
from decimal import Decimal as D, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo
from forward_paper import atomic,load
from replay import iso,timestamp,quotes_at,spot_at

CT=ZoneInfo('America/Chicago')
WINDOWS={'ZEBRA':(time(8,30),time(9,15)),
 'SAFE_INCOME':(time(9,30),time(10,30)),
 'SNIPER_SUPPORT':(time(10,0),time(12,30)),
 'CREDIT_ROLLS_GROWTH':(time(14,30),time(15,0))}
BASE={'ZEBRA':'EQUITY_30_90_DTE_DIRECTIONAL',
      'SAFE_INCOME':'INDEX_0_1_DTE_DEFINED_RISK',
      'SNIPER_SUPPORT':'BULL_PUT_SUPPORT_RETEST',
      'CREDIT_ROLLS_GROWTH':'HELD_POSITION_MANAGEMENT_OR_WEEKLY'}

def read_numeric(record,key):
 try:
  x=D(str(record[key]));return x if x.is_finite() else None
 except (KeyError,TypeError,ValueError,InvalidOperation):return None

def classify(name,frame,now):
    ct=now.astimezone(CT);start,end=WINDOWS[name]
    result={'strategy':name,'profile':BASE[name],'at':iso(now),'status':'DATA_UNAVAILABLE',
            'reason':'REQUIRED_MARKET_EVIDENCE_UNAVAILABLE','shadow_only':True,
            'broker_submissions':0,'paper_fills':0}
    if not(start<=ct.time()<end):
        result.update(status='OUTSIDE_WINDOW',reason='NOT_IN_PROPOSED_EVALUATION_WINDOW');return result
    if not frame:
        result['reason']='NO_VERIFIED_RESEARCH_FRAME';return result
    if frame.get('calendar_status')!='CLEAR' or frame.get('event_clear') is not True:
        result['reason']='CALENDAR_NOT_CLEAR';return result
    spot=spot_at(frame,now)
    if spot is None:
        result['reason']='FRESH_UNDERLYING_SPOT_MISSING';return result
    if name=='CREDIT_ROLLS_GROWTH':
        result.update(status='DATA_UNAVAILABLE',reason='VERIFIED_HELD_POSITIONS_AND_WEEKLY_OPTION_CHAIN_REQUIRED');return result
    if name=='ZEBRA':
        result.update(status='DATA_UNAVAILABLE',reason='EQUITY_CHAIN_30_90_DTE_AND_DAILY_SMA_IV_GAMMA_REQUIRED');return result
    if name=='SNIPER_SUPPORT':
        # Real support classification is distinct from existing bullish VWAP
        # reclaim; a 200-day SMA or expected move MUST be supplied.
        ctx=frame.get('shadow_support') or {}
        level=read_numeric(ctx,'verified_support_level')
        rsi=read_numeric(ctx,'rsi14')
        if level is None or rsi is None or ctx.get('macro_uptrend') is not True:
            result['reason']='SUPPORT_RSI_AND_MACRO_TREND_NOT_VERIFIED';return result
        if (rsi<D('35') or abs(spot-level)/spot<=D('.0025')) and ctx.get('completed_rejection') is True:
            result.update(status='SIGNAL_OBSERVED',reason='SUPPORT_REJECTION_CONFIRMED_ENTRY_QUOTE_CHECK_REQUIRED')
        else:result.update(status='NO_SIGNAL',reason='SUPPORT_PULLBACK_CONDITIONS_NOT_MET')
        return result
    # Range safe income cannot classify from IV Rank without verified IV
    # history and expected-move bounds. Never treat low IV as neutral.
    ctx=frame.get('shadow_income') or {}
    ivrank=read_numeric(ctx,'iv_rank_pct')
    low=read_numeric(ctx,'expected_move_lower')
    high=read_numeric(ctx,'expected_move_upper')
    if ivrank is None or low is None or high is None or ctx.get('range_verified') is not True:
        result['reason']='IV_RANK_EXPECTED_MOVE_AND_RANGE_NOT_VERIFIED';return result
    if ivrank>D('30') and low<spot<high and ctx.get('stall_confirmed') is True:
        result.update(status='SIGNAL_OBSERVED',reason='RANGE_PREMIUM_SIGNAL_OPTION_QUOTES_REQUIRED')
    else:result.update(status='NO_SIGNAL',reason='RANGE_PREMIUM_CONDITIONS_NOT_MET')
    return result

def aggregate(ledger):
    trades=ledger.get('completed',[])
    verified=[p for p in trades if p.get('realized_pnl_usd') is not None and p.get('fill_provenance')=='TWO_NEWER_VERIFIED_PROVIDER_QUOTE_BATCHES']
    pnl=[D(p['realized_pnl_usd']) for p in verified]
    total=peak=drawdown=D('0')
    for x in pnl:
        total+=x;peak=max(peak,total);drawdown=max(drawdown,peak-total)
    return {'closed_verified':len(pnl),'wins':sum(x>0 for x in pnl),'losses':sum(x<0 for x in pnl),
            'win_rate_pct':str((D(sum(x>0 for x in pnl))*100/len(pnl)).quantize(D('.01'))) if pnl else None,
            'realized_pnl_usd':str(total.quantize(D('.01'))),
            'realized_max_drawdown_usd':str(drawdown.quantize(D('.01'))),
            'pending_candidates':sum(x.get('status')=='SIGNAL_OBSERVED' for x in ledger.get('latest_evaluations',[])),
            'unverified_fills_excluded':len(trades)-len(verified)}

def observe(root,frames,now):
    state=Path(root)/'state';state.mkdir(parents=True,exist_ok=True)
    path=state/'regime_shadow_ledger.json'
    ledger=load(path,{'schema_version':'1.0','mode':'SHADOW_OBSERVATION_ONLY',
                       'completed':[],'observations':[],'open':[],'broker_submissions':0})
    if ledger.get('mode')!='SHADOW_OBSERVATION_ONLY' or ledger.get('broker_submissions')!=0:
        raise ValueError('SHADOW_GOVERNANCE_INVALID')
    for name in WINDOWS:
        # Prefer SPX over XSP for shared index context; absence is explicit.
        frame=next((f for f in frames if f.get('symbol')=='SPX'),None)
        if frame is None:frame=next((f for f in frames if f.get('symbol')=='XSP'),None)
        result=classify(name,frame,now)
        key=(result['at'],result['strategy'])
        if not any((x['at'],x['strategy'])==key for x in ledger['observations']):
            ledger['observations'].append(result)
    if len(ledger['observations'])>25000:raise ValueError('SHADOW_ARCHIVE_CAPACITY_REACHED')
    ledger['latest_evaluations']=ledger['observations'][-4:]
    ledger['last_evaluated_at']=iso(now)
    ledger['stats']=aggregate(ledger)
    atomic(path,ledger)
    atomic(state/'regime_shadow_latest.json',{'at':iso(now),'mode':ledger['mode'],
        'results':ledger['latest_evaluations'],'stats':ledger['stats'],
        'paper_entries':0,'broker_submissions':0})
    return ledger
