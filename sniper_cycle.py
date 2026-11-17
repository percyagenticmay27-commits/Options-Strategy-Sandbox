"""Independent Sniper 0/1-DTE intraday debit-spread paper ledger.

No order transport. The scanner requires two distinct provider quote batches for
an executable paper entry; physical-settlement contracts must close before expiry.
"""
from __future__ import annotations
from datetime import datetime,timezone,time
from decimal import Decimal as D
from pathlib import Path
from replay import CT,iso,timestamp
from forward_paper import atomic,load
from sniper import evaluate,ScanPolicy
from sniper_intraday import deadline,forced_exit_reason

MAX_AGE_SECONDS=60
ENTRY_START=time(10,0)
ENTRY_END=time(11,0)
FEE_PER_LEG=D('0.65')

def _fresh(value,now):
 try:
  age=(now-timestamp(value)).total_seconds()
  return -5<=age<=MAX_AGE_SECONDS
 except (TypeError,ValueError,AttributeError):return False

def _quote(payload,contract,now):
 for r in payload.get('contracts',[]):
  q=r.get('quote') or {}
  if (r.get('instrument_id')==contract and q.get('instrument_id')==contract
      and _fresh(q.get('updated_at'),now) and r.get('state')=='active'
      and r.get('tradability')=='tradable'
      and int(q.get('bid_size',0))>0 and int(q.get('ask_size',0))>0):
   try:
    bid,ask=D(str(q['bid_price'])),D(str(q['ask_price']))
    if bid>=0 and ask>0 and ask>=bid and bid.is_finite() and ask.is_finite():return q,bid,ask
   except (ValueError,TypeError,KeyError,ArithmeticError):pass
 return None

def run(root,payload,now,*,close_all=False):
 root=Path(root);state=root/'state';state.mkdir(parents=True,exist_ok=True)
 ledger=load(state/'sniper_paper_ledger.json',{'schema_version':'1.0','strategy':'SNIPER',
  'paper_only':True,'broker_submissions':0,'open':[],'completed':[],'events':[],
  'pending_candidates':{},'daily_evaluations':{}})
 report=evaluate(payload,now)
 day=now.astimezone(CT).date().isoformat()
 # Always write a daily evaluation even when the equity research feed is absent.
 ledger['daily_evaluations'][day]={'at':iso(now),'status':report['status'],
  'reason':report['reason'],'symbol':report.get('symbol'),'paper_only':True}
 ledger['latest_evaluation']=report
 ledger['last_scan_utc']=iso(now)
 ledger['new_events']=[]
 ledger['broker_submissions']=0
 ledger['paper_only']=True
 ledger['order_submission_enabled']=False
 ledger['capital_usd']='500.00'
 ledger['max_open_defined_risk_usd']=format(ScanPolicy().max_risk,'.2f')
 ledger['expiration_policy']={'preferred_dte':0,'maximum_dte':1,'holding_policy':'SAME_SESSION_EXIT','mandatory_exit_ct':'14:20'}
 # An open spread can only be marked/closed from fresh natural bid/ask.
 if ledger['open']:
  for pos in list(ledger['open']):
   forced=forced_exit_reason(pos,now) or ('INTRADAY_MANAGEMENT_CLOSE' if close_all else None)
   if forced=='DTE_POLICY_MIGRATION_EXIT':
    pos.update(excluded_from_strategy_statistics=True,exclusion_reason='LEGACY_14_30_DTE_TEST_OUTSIDE_USER_SCOPE')
   if forced:pos.update(exit_pending=True,requested_exit_reason=forced)
   if payload.get('symbol')!=pos['symbol'] or not _fresh(payload.get('generated_at_utc'),now):
    pos['last_management_status']='FRESH_HELD_QUOTES_REQUIRED';continue
   long=_quote(payload,pos['long_contract_id'],now)
   short=_quote(payload,pos['short_contract_id'],now)
   if not long or not short:
    pos['last_management_status']='FRESH_HELD_QUOTES_REQUIRED';continue
   debit=D(pos['opening_debit'])
   closing_credit=long[1]-short[2]
   pnl=(closing_credit-debit)*100-D('2.60')
   pos['unrealized_pnl_usd']=str(pnl.quantize(D('.01')))
   pos['last_management_status']='FRESH_MARK'
   pos['last_mark_at']=iso(now)
   profit_target=D(pos['maximum_profit_usd'])*D('.5')
   stop=D(pos['maximum_loss_usd'])*D('.5')
   # Physical settlement / assignment cannot be simulated as cash. Close
   # before expiry with verified live quotes, fail closed otherwise.
   reason=forced or ('TAKE_PROFIT' if pnl>=profit_target else
           'HALF_MAX_LOSS' if pnl<=-stop else None)
   if reason:
    pos.update(status='CLOSED',closed_at=iso(now),exit_reason=reason,exit_pending=False,
               closing_credit=str(closing_credit),realized_pnl_usd=str(pnl.quantize(D('.01'))))
    ledger['open'].remove(pos);ledger['completed'].append(pos)
    e={'action':'PAPER_SELL','at':iso(now),'symbol':pos['symbol'],
       'position_id':pos['id'],'realized_pnl_usd':pos['realized_pnl_usd'],
       'reason':reason,'paper_only':True,'broker_submissions':0}
    ledger['events'].append(e);ledger['new_events'].append(e)
 local=now.astimezone(CT)
 try:
  exit_at=deadline(now,payload.get('session') or {})
  session_entry_ok=now<exit_at
 except (ValueError,TypeError,KeyError):
  exit_at=None;session_entry_ok=False
 allowed=local.weekday()<5 and ENTRY_START<=local.time()<ENTRY_END and session_entry_ok and not close_all
 if report['status']=='CANDIDATE' and not session_entry_ok:
  report.update(status='REJECTED',reason='VERIFIED_SESSION_OR_INTRADAY_ENTRY_DEADLINE_REQUIRED')
 # Exactly one candidate fills only after newer verified quotes on same IDs,
 # with re-confirmed strategy signal, no daily repeat and the configured fee-inclusive risk limit.
 if allowed and report['status']=='CANDIDATE' and not ledger['open']:
  key=report['symbol']
  pending=ledger['pending_candidates'].get(key)
  accepted=any(p['opened_at'].startswith(day) and not p.get('excluded_from_strategy_statistics') for p in ledger['completed'])
  account_risk=sum(D(p['maximum_loss_usd']) for p in ledger['open'])
  if not accepted and pending:
   try:
    first=timestamp(pending['at'])
    if (0<(now-first).total_seconds()<=60 and
        pending['long_contract_id']==report['long_contract_id'] and
        pending['short_contract_id']==report['short_contract_id'] and
        pending['expiration']==report['expiration']):
     long=_quote(payload,report['long_contract_id'],now)
     short=_quote(payload,report['short_contract_id'],now)
     if (long and short and timestamp(long[0]['updated_at'])>timestamp(pending['long_quote_at'])
         and timestamp(short[0]['updated_at'])>timestamp(pending['short_quote_at'])):
      debit=long[2]-short[1]
      risk=debit*100+D('2.60')
      if D('0')<debit<=D(pending['net_debit']) and risk+account_risk<=ScanPolicy().max_risk:
       entry={'id':f'SNIPER-PAPER-{day}-{key}', 'symbol':key,'status':'OPEN',
         'opened_at':iso(now),'opening_debit':str(debit),
         'maximum_loss_usd':str(risk.quantize(D('.01'))),
         'maximum_profit_usd':str(((abs(D(report['short_strike'])-D(report['long_strike']))-debit)*100-D('2.60')).quantize(D('.01'))),
         'expiration':report['expiration'],'dte':report['dte'],'holding_policy':'SAME_SESSION_EXIT',
         'mandatory_exit_at':iso(exit_at.astimezone(timezone.utc)),'long_strike':report['long_strike'],'short_strike':report['short_strike'],
         'long_contract_id':report['long_contract_id'],
         'short_contract_id':report['short_contract_id'],'paper_only':True,
         'order_submission_enabled':False,'structure':report['structure']}
       ledger['open'].append(entry)
       e={'action':'PAPER_BUY','at':iso(now),'symbol':key,'position_id':entry['id'],
          'debit':str(debit),'defined_risk_usd':entry['maximum_loss_usd'],
          'paper_only':True,'broker_submissions':0}
       ledger['events'].append(e);ledger['new_events'].append(e)
   except (ValueError,TypeError,KeyError,ArithmeticError):pass
  # No optimistic fill on first quote batch; persist candidate for 60 seconds.
  long=_quote(payload,report['long_contract_id'],now)
  short=_quote(payload,report['short_contract_id'],now)
  if long and short and not ledger['open'] and not accepted:
   ledger['pending_candidates'][key]={k:report[k] for k in ('symbol','expiration','long_contract_id','short_contract_id','net_debit')}
   ledger['pending_candidates'][key].update(at=iso(now),
    long_quote_at=long[0]['updated_at'],short_quote_at=short[0]['updated_at'])
  else:ledger['pending_candidates'].pop(key,None)
 else:
  # Expired or unavailable setups cannot authorize delayed fills.
  ledger['pending_candidates']={}
 valid_closed=[p for p in ledger['completed'] if not p.get('excluded_from_strategy_statistics') and p.get('realized_pnl_usd') is not None]
 gains=[D(p['realized_pnl_usd']) for p in valid_closed]
 ledger['performance']={'closed_verified':len(gains),'wins':sum(p>0 for p in gains),'losses':sum(p<0 for p in gains),
  'win_rate_pct':str(D(sum(p>0 for p in gains))*100/len(gains)) if gains else None,
  'realized_pnl_usd':str(sum(gains,D(0))),
  'excluded_legacy_positions':sum(bool(p.get('excluded_from_strategy_statistics')) for p in ledger['completed']+ledger['open']),
  'all_paper_realized_pnl_usd':str(sum((D(p['realized_pnl_usd']) for p in ledger['completed'] if p.get('realized_pnl_usd') is not None),D(0))),
  'statistics_scope':'QUALIFYING_0_1_DTE_INTRADAY_ONLY;ALL_PAPER_PNL_INCLUDES_LEGACY_TEST'}
 atomic(state/'sniper_paper_ledger.json',ledger)
 atomic(state/'sniper_daily_evaluation.json',{'at':iso(now),'date':day,'strategy':'SNIPER',
  'evaluation':report,'new_paper_events':ledger['new_events'],
  'open_positions':len(ledger['open']),'completed_positions':len(ledger['completed']),
  'paper_only':True,'broker_submissions':0})
 return ledger
