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
from observe import frames_from_snapshot
from replay import quotes_at,spot_at,timestamp,iso,recent

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

def execute_scan(root,now,snapshots,session,calendar):
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
    report=forward_cycle(root,frames,now,status,source=SOURCE)
    report['candidate_symbols']=[f['symbol'] for f in frames]
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
        outcome=execute_scan(root,now,[load(args.snapshot,{}),load(args.option_snapshot,{})],s,calendar)
        print(json.dumps({k:outcome[k] for k in ('status','feed_status','paper_entries','candidate_symbols','refresh_required','generated_at_utc','broker_submissions')},sort_keys=True))
        # Exit 66 is the same explicit connector-refresh handoff used by OPTION-001.
        raise SystemExit(66 if outcome['refresh_required'] else 0)

if __name__=='__main__':main()
