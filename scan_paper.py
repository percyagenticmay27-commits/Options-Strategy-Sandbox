"""Guarded, scheduled Strategy B local paper scan and stateful quote-to-fill engine.

Mirrors OPTION-001's governance pattern: read connector-published data, verify
freshness and contract metadata, request new quotes on failure, never simulate
fills from expired snapshots. Strategy rules belong to sandbox/replay.py.
"""
from __future__ import annotations
import argparse
import fcntl
import json
from copy import deepcopy
from decimal import Decimal,InvalidOperation
from datetime import datetime,timezone
from pathlib import Path
from event_calendar import clearance,refresh
from forward_paper import forward_cycle,atomic,load
from bwb_cycle import run as bwb_run
from bwb_context import context_for
from regime_shadow import observe as observe_regime_shadow
from shadow_comparison import publish as publish_shadow_comparison
from observe import frames_from_snapshot
from replay import quotes_at,spot_at,timestamp,iso,recent,entry_gate_detail,PROFILES,selection,RISK_CAP,ENTRY_START,ENTRY_CUTOFF,CT

ROOT=Path(__file__).resolve().parent
SOURCE='ROBINHOOD_MCP_RESEARCH'

# Respect OPTION-001 as the higher-priority service. Probe its existing flock
# without retaining ownership, so we never block the production Option Bot.
OPTION_LOCK=Path('/run/user') / str(__import__('os').getuid()) / 'trade-option-bot.lock'

def option_bot_idle():
    try:
        with OPTION_LOCK.open('a+') as handle:
            try:
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            finally:
                # The lock is released when the descriptor closes.
                pass
        return True
    except OSError:
        # Cannot verify Option Bot lock authority: fail closed.
        return False


REQUIRED_QUOTE_FIELDS=("bid_price","ask_price","bid_size","ask_size","mark_price",
    "delta","gamma","theta","vega","rho","implied_volatility","volume","open_interest")


def contract_metadata_current(row,now):
    """Fail closed on the same complete-Greeks research metadata as OPTION-001."""
    try:
        if (row['metadata_provenance']['source']!='CBOE_PRODUCT_SPECIFICATION' or
            row['exercise_style'].lower()!='european' or
            row['settle_on_open'] is not False or
            Decimal(str(row['trade_value_multiplier']))!=100 or
            row['instrument_id']!=row['id'] or row['state']!='active' or
            row['tradability']!='tradable'):
            return False
        quote=row['quote']
        if quote['instrument_id']!=row['id'] or not recent(quote['updated_at'],now):return False
        if not all(quote.get(key) is not None and Decimal(str(quote[key])).is_finite()
                   for key in REQUIRED_QUOTE_FIELDS):return False
        return (Decimal(str(quote['bid_price']))>=0 and
                Decimal(str(quote['ask_price']))>=Decimal(str(quote['bid_price'])) and
                Decimal(str(quote['ask_size']))>0 and Decimal(str(quote['bid_size']))>0)
    except (KeyError,ValueError,TypeError,AttributeError,InvalidOperation,ArithmeticError):
        return False


def choose_snapshot(payloads,now):
    valid=[]
    for s in payloads:
        if not isinstance(s,dict):continue
        if (s.get('source')!=SOURCE or s.get('paper_only') is not True or
            s.get('research_only') is not True or s.get('order_submission_enabled') is not False or
            s.get('broker_execution_enabled') is not False or s.get('live_trading_enabled') is not False):continue
        try:
            at=timestamp(s['generated_at_utc'])
            if not -5 <= (now-at).total_seconds() <=60:continue
            valid.append((at,s))
        except (KeyError,TypeError,AttributeError,ValueError):continue
    return max(valid,key=lambda x:x[0])[1] if valid else None

def execute_scan(root,now,snapshots,session,calendar,bwb_snapshots=None):
    root=Path(root)
    if not option_bot_idle():
        report={'status':'OPTION_BOT_PRIORITY_DEFERRED','generated_at_utc':iso(now),
             'paper_entries':0,'broker_submissions':0,'paper_trade_mode':True,
             'order_submission_enabled':False,'reason':'OPTION_001_LOCK_BUSY_OR_UNAVAILABLE'}
        atomic(root/'state/priority_deferred.json',report)
        return report
    chosen=choose_snapshot(snapshots,now)
    status='SNAPSHOT_STALE_OR_FUTURE'
    frames=[]
    if chosen:
        # Never let incomplete or stale Greeks/options metadata through to
        # the sandbox's unchanged strategy engine. A valid partial surface may
        # still be evaluated but cannot claim the missing contracts were valid.
        cleaned=deepcopy(chosen)
        invalid_metadata=0
        for symbol,records in cleaned.get('symbols',{}).items():
            if not isinstance(records,dict):continue
            contracts=records.get('contracts',[])
            if not isinstance(contracts,list):continue
            filtered=[r for r in contracts if isinstance(r,dict) and contract_metadata_current(r,now)]
            invalid_metadata+=len(contracts)-len(filtered)
            records['contracts']=filtered
        frames,status=frames_from_snapshot(cleaned,session,now)
        event=clearance(calendar,now)
        if event['status']!='CLEAR':
            status=event['status']
            frames=[]
        elif status=='FRESH':
            # Require at least one genuinely executable quote and index spot;
            # missing metadata must not make the process look healthy.
            good=[f for f in frames if quotes_at(f,now) and spot_at(f,now) is not None]
            if not good:status='NO_FRESH_VERIFIED_SPOT_OR_CONTRACT_QUOTES'
            else:
                for f in good:f.update(event_clear=True,calendar_status='CLEAR')
                frames=good
    # Per-cycle coverage and exact reasons are visible even if no spread fills.
    # Each profile is evaluated separately on the SAME verified frames; no
    # synthetic quotes, relaxed risk limits or replacement main-bot data.
    strategy_audit={}
    for f in frames:
        quotes=quotes_at(f,now)
        gate=entry_gate_detail(f,now)
        item={'symbol':f['symbol'],'at':iso(now),'usable_quotes':len(quotes),
              'gate':gate,'profiles':{},'paper_only':True}
        for name,band in PROFILES.items():
            if gate['primary']:
                item['profiles'][name]={'status':'BLOCKED','reason_codes':gate['reasons']}
            else:
                proposed=selection(f,now,quotes,band,RISK_CAP)
                if proposed is None:
                    item['profiles'][name]={'status':'NO_ELIGIBLE_SPREAD',
                        'reason_codes':['DELTA_STRIKE_LIQUIDITY_OR_MIN_RETURN'],
                        'delta_min':str(band[0]),'delta_max':str(band[1])}
                else:
                    spread,credit=proposed
                    from replay import economics
                    profit,loss=economics(spread,credit)
                    item['profiles'][name]={'status':'ELIGIBLE_PENDING_SECOND_QUOTE',
                        'short_strikes':[str(x.strike) for x in spread.shorts],
                        'long_strikes':[str(x.strike) for x in spread.longs],
                        'credit':str(credit),'maximum_loss':str(loss),'maximum_profit':str(profit)}
        strategy_audit[f['symbol']]=item
    if not frames:
        strategy_audit={'status':'DATA_UNAVAILABLE','reason':status,
                        'required':'FRESH_SPX_XSP_INDEX_QUOTES_OPTION_CONTRACTS_COMPLETED_SPY_5MIN_BARS'}
    atomic(root/'state/strategy_evaluation_audit.json',
           {'generated_at_utc':iso(now),'feed_status':status,'symbols':strategy_audit,
            'paper_only':True,'broker_submissions':0})
    report_status_reason=status
    # Append independent BWB higher-timeframe/volume-by-bar context from the
    # same *verified* provider payload, without changing Low Delta signals.
    report=forward_cycle(root,frames,now,status,source=SOURCE)
    # BWB can consume a separately verified and newly refreshed read-only
    # research snapshot. It is not constrained by Low Delta's UP-only gate.
    bwb_chosen=choose_snapshot(bwb_snapshots or [],now) if bwb_snapshots else chosen
    bwb_source='INDEPENDENT_BWB_SNAPSHOT' if bwb_snapshots and bwb_chosen else 'SHARED_VERIFIED_SNAPSHOT'
    if bwb_chosen is chosen:
        bwb_frames=frames
    elif bwb_chosen:
        bwb_clean=deepcopy(bwb_chosen)
        for symbol,records in bwb_clean.get('symbols',{}).items():
            if isinstance(records,dict) and isinstance(records.get('contracts'),list):
                records['contracts']=[r for r in records['contracts'] if isinstance(r,dict) and contract_metadata_current(r,now)]
        bwb_frames,bwb_status=frames_from_snapshot(bwb_clean,session,now)
        if bwb_status=='FRESH' and clearance(calendar,now)['status']=='CLEAR':
            bwb_frames=[{**f,'event_clear':True,'calendar_status':'CLEAR'} for f in bwb_frames
                       if quotes_at(f,now) and spot_at(f,now) is not None]
        else:bwb_frames=[]
    else:
        bwb_frames=[]
    bwb_frames=[{**f,'bwb_context':context_for(f,bwb_chosen or {},now)} for f in bwb_frames]
    bwb=bwb_run(root,bwb_frames,now)
    bwb_coverage={f['symbol']:{'context_ready':f['bwb_context'].get('ready',False),
        'context_reason':f['bwb_context'].get('reason'),
        'fresh_option_quotes':len(quotes_at(f,now)),
        'fresh_index_spot':spot_at(f,now) is not None,
        'source':bwb_source} for f in bwb_frames}
    if not bwb_coverage:
        bwb_coverage={'status':'DATA_UNAVAILABLE','reason':status,
            'required':'INDEPENDENT_SPX_XSP_CHAIN_AND_CURRENT_53_BAR_SPY_CONTEXT'}
    atomic(root/'state/bwb_coverage_audit.json',
          {'at':iso(now),'symbols':bwb_coverage,'paper_only':True,'broker_submissions':0})
    report['bwb_0dte']={'strategy':'BWB_0DTE',
       'purchase_limit_usd':'500.00','max_defined_risk_usd':'500.00',
       'evaluations':[{'symbol':r.get('symbol'),'status':r.get('status'),
          'reason':r.get('reason')} for r in bwb['latest_evaluations']],
       'open_positions':len(bwb['open']),'completed_positions':len(bwb['completed']),
       'new_paper_events':len(bwb['new_events']),'paper_only':True,'broker_submissions':0}

    # The previous 14-30 DTE equity Sniper pilot is PAUSED. The user's
    # new sniper experiment is the independent SPX/XSP 0DTE BWB above.
    report['sniper']={**report['bwb_0dte'],'pilot':'SNIPER_BWB_0DTE'}
    shadow=observe_regime_shadow(root,frames,now)
    report['regime_shadow']={'status':'OBSERVING','strategies':4,
      'evaluations':shadow['latest_evaluations'],'stats':shadow['stats'],
      'paper_entries':0,'broker_submissions':0}
    publish_shadow_comparison(root,Path('/home/tradebot/Trade-Option-Bot/results/option_001_positions.json'))
    report['candidate_symbols']=[f['symbol'] for f in frames]
    report['strategy_evaluation_audit']=strategy_audit
    report['bwb_data_coverage']=bwb_coverage
    report['metadata_rejections']=invalid_metadata if chosen else 0
    report['refresh_required']=report['status']=='PENDING_CONNECTOR_REFRESH'
    atomic(root/'state/latest.json',report)
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=ROOT)
    p.add_argument('--snapshot',type=Path,default=ROOT/'state/robinhood_research.json')
    p.add_argument('--option-snapshot',type=Path,default=Path('/home/tradebot/Trade-Option-Bot/results/robinhood_option_research.json'))
    p.add_argument('--session',type=Path,default=Path('/home/tradebot/Trade-PMCC-Bot/inputs/alpaca_pmcc_research.json'))
    p.add_argument('--calendar',type=Path,default=ROOT/'state/calendar.json')
    p.add_argument('--bwb-snapshot',type=Path,default=ROOT/'state/bwb_research.json')
    args=p.parse_args()
    root=args.root.resolve();now=datetime.now(timezone.utc)
    (root/'state').mkdir(parents=True,exist_ok=True)
    with (root/'state/paper_cycle.lock').open('a+') as lock:
        try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('SANDBOX_CYCLE_ALREADY_RUNNING')
        calendar=load(args.calendar,{})
        if clearance(calendar,now)['status']=='CALENDAR_UNAVAILABLE':
            calendar=refresh(now);atomic(args.calendar,calendar)
        s=load(args.session,{}).get('session') or {}
        if s.get('source')!='ALPACA_CALENDAR' or s.get('is_trading_day') is not True:s={}
        bwb_data=load(args.bwb_snapshot,{})
        outcome=execute_scan(root,now,[load(args.snapshot,{}),load(args.option_snapshot,{})],s,calendar,
                             bwb_snapshots=[bwb_data] if bwb_data else None)
        print(json.dumps({k:outcome[k] for k in ('status','feed_status','paper_entries','candidate_symbols','refresh_required','generated_at_utc','broker_submissions')},sort_keys=True))
        # Exit 66 is the same explicit connector-refresh handoff used by OPTION-001.
        raise SystemExit(66 if outcome['refresh_required'] else 0)

if __name__=='__main__':main()
