"""Strategy B local-only paper purchases, fills, exits and refresh requests.

Uses the sandbox's original low-delta versus comparison strategy and two-stage
quote-to-fill model. No broker clients, production imports or order endpoints.
"""
from __future__ import annotations
import json
import os
import tempfile
from decimal import Decimal
from datetime import datetime,timezone
from pathlib import Path
from replay import compare,iso,timestamp,PROFILES


def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as file:
            json.dump(value,file,indent=2,sort_keys=True,allow_nan=False)
            file.write('\n');file.flush();os.fsync(file.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def load(path,default):
    try:return json.loads(Path(path).read_text())
    except FileNotFoundError:return default


def forward_cycle(root,frames,now,feed,*,source='ROBINHOOD_MCP_RESEARCH'):
    root=Path(root);state=root/'state';state.mkdir(exist_ok=True)
    previous=load(state/'forward_frames.json',{'schema_version':'1.0','frames':[]})
    prior=previous.get('frames',[])
    if len(prior)>2000:raise ValueError('FORWARD_LEDGER_CAPACITY_REQUIRES_ARCHIVE')
    if not isinstance(now,datetime) or now.tzinfo is None:raise ValueError('NOW_TIMEZONE_REQUIRED')
    new=[]
    if feed=='FRESH':
        seen={(f['at'],f['symbol']) for f in prior}
        latest=max((timestamp(f['at']) for f in prior),default=None)
        for f in frames:
            stamp=timestamp(f['at']);key=(f['at'],f['symbol'])
            if stamp>now or (now-stamp).total_seconds()>60:raise ValueError('STALE_FRAME_IN_FRESH_FEED')
            if key in seen:
                previous_row=next(p for p in prior if (p['at'],p['symbol'])==key)
                if previous_row!=f:raise ValueError('CONFLICTING_FRAME_REJECTED')
                continue
            if latest is not None and stamp<latest:raise ValueError('OUT_OF_ORDER_FRAME_REJECTED')
            seen.add(key);new.append(f)
    total=prior+new
    if len(total)>2000:raise ValueError('FORWARD_LEDGER_CAPACITY_REQUIRES_ARCHIVE')
    # Replay from durable observations avoids state drift, duplicate entries,
    # and the risk of a partially updated multi-leg ledger.
    result=compare({'kind':'HISTORICAL','source':'STRATEGY_B_VERIFIED_FORWARD_OBSERVATIONS',
                    'frames':total})
    profiles={}
    all_positions=[]
    for name,model in result['profiles'].items():
        trades=model['trades'];closed=[x for x in trades if x['status']=='CLOSED']
        opened=[x for x in trades if x['status']!='CLOSED']
        profiles[name]={'starting_capital':result['capital'],
          'book_type':'PAPER_ALLOCATION' if name=='low_delta' else 'HYPOTHETICAL_COMPARISON',
          'short_delta_min':model['short_delta_min'],
          'short_delta_max':model['short_delta_max'],
          'open_count':len(opened),'completed_count':len(closed),
          'realized_pnl':str(sum((Decimal(x['pnl']) for x in closed),Decimal(0))),
          'win_rate_pct':model['metrics']['win_rate_pct'],
          'realized_max_drawdown':model['metrics']['realized_max_drawdown'],
          'observed_marked_max_drawdown':model['metrics']['observed_marked_max_drawdown'],
          'closed_wins':model['metrics']['wins'],
          'closed_losses':model['metrics']['losses'],
          'unfilled_orders':model['unfilled_orders'],
          'decision_counts':model['decision_counts'],
          'pending_settlement':model['metrics']['pending_settlement'],
          'decisions':model['decisions'][-8:],'trades':trades}
        all_positions.extend([{**p,'profile':name} for p in trades])
    # Durable exact-once simulated BUY/SELL audit: the strategy replay is
    # deterministic, so events derive from actual returned fill timestamps.
    event_file=state/'paper_events.json'
    journal=load(event_file,{'events':[]})
    past=journal.get('events',[])
    seen={(e.get('profile'),e.get('type'),e.get('opened_at'),e.get('at')) for e in past}
    added=[]
    for name,model in profiles.items():
        for trade in model['trades']:
            for kind,at in (('PAPER_BUY',trade['opened_at']),('PAPER_SELL',trade['closed_at'])):
                if not at:continue
                key=(name,kind,trade['opened_at'],at)
                if key in seen:continue
                seen.add(key)
                added.append({'profile':name,'type':kind,'opened_at':trade['opened_at'],
                   'at':at,'symbol':trade['symbol'],'legs':trade['legs'],
                   'credit':trade['credit'],'risk':trade['risk'],
                   'pnl':trade['pnl'] if kind=='PAPER_SELL' else None,
                   'exit_reason':trade['reason'] if kind=='PAPER_SELL' else None,
                   'paper_only':True,'broker_submissions':0})
    if added:atomic(event_file,{'schema_version':'1.0','events':past+added})
    filled=sum(sum(1 for x in m['trades']) for m in profiles.values())
    report={'schema_version':'1.0','strategy_id':'SANDBOX_STRATEGY_B',
       'low_delta_allocated_capital':result['capital'],
       'comparison_hypothetical_capital':result['capital'],
       'max_defined_risk_per_book':result['combined_risk_limit'],
       'generated_at_utc':iso(now),'mode':'AUTONOMOUS_PAPER_SIMULATION',
       'status':'PAPER_ACTIVE' if feed=='FRESH' else 'PENDING_CONNECTOR_REFRESH',
       'feed_status':feed,'snapshot_source':source,
       'paper_trade_mode':True,'live_trading_enabled':False,
       'broker_execution_enabled':False,'order_placement_enabled':False,
       'order_submission_enabled':False,'broker_submissions':0,
       'paper_entries':filled,'new_paper_events':len(added),
       'paper_buy_events':sum(e['type']=='PAPER_BUY' for e in past+added),
       'paper_sell_events':sum(e['type']=='PAPER_SELL' for e in past+added),
       'new_frames':len(new),'stored_frames':len(total),
       'profiles':profiles,
       'shadow_cutoff_1030':{name:{'decision_counts':val['decision_counts'],
            'metrics':val['metrics']} for name,val in result['cutoff_observation']['profiles'].items()}}
    if new:atomic(state/'forward_frames.json',{'schema_version':'1.0','frames':total})
    atomic(state/'paper_positions.json',{'generated_at_utc':iso(now),'paper_only':True,'positions':all_positions})
    atomic(state/'paper_ledger.json',report)
    # Independent ledgers and scoreboards: neither strategy's P/L or daily
    # entries are netted against the other's experimental book.
    for name,model in profiles.items():
        atomic(state/('paper_book_'+name+'.json'),{
            'schema_version':'1.0','profile':name,'book_type':model['book_type'],
            'generated_at_utc':iso(now),'feed_status':feed,
            'starting_capital':model['starting_capital'],
            'open_count':model['open_count'],'completed_count':model['completed_count'],
            'realized_pnl':model['realized_pnl'], 'win_rate_pct':model['win_rate_pct'],
            'wins':model['closed_wins'],'losses':model['closed_losses'],
            'realized_max_drawdown':model['realized_max_drawdown'],
            'observed_marked_max_drawdown':model['observed_marked_max_drawdown'],
            'unfilled_orders':model['unfilled_orders'],
            'decision_counts':model['decision_counts'],
            'positions':model['trades'],'paper_only':True,'broker_submissions':0})
    if feed!='FRESH':
        atomic(state/'refresh_request.json',{'status':'PENDING_CONNECTOR_REFRESH',
            'requested_at_utc':iso(now),'reason':feed,
            'required_sequence':['FETCH_FRESH_READ_ONLY_QUOTES','VERIFY_CONTRACT_METADATA',
               'ATOMIC_PUBLISH','RUN_SANDBOX_PAPER_CYCLE'],
            'max_quote_age_seconds':60,'broker_orders_enabled':False})
    else:
        request=state/'refresh_request.json'
        if request.exists():request.unlink()
    return report
