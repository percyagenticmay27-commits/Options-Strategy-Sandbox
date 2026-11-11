"""Independent Sniper 14-30 DTE equity call-debit paper ledger and daily evaluation.

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

def run(root,payload,now):
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
 ledger['max_open_defined_risk_usd']='100.00'
 # An open spread can only be marked/closed from fresh natural bid/ask.
 if ledger['open']:
  for pos in list(ledger['open']):
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
   days=(datetime.fromisoformat(pos['expiration']).date()-now.astimezone(CT).date()).days
   reason=('ONE_DAY_BEFORE_EXPIRATION' if days<=1 else
           'TAKE_PROFIT' if pnl>=profit_target else
           'HALF_MAX_LOSS' if pnl<=-stop else None)
   if reason and closing_credit>=0:
    pos.update(status='CLOSED',closed_at=iso(now),exit_reason=reason,
               closing_credit=str(closing_credit),realized_pnl_usd=str(pnl.quantize(D('.01'))))
    ledger['open'].remove(pos);ledger['completed'].append(pos)
    e={'action':'PAPER_SELL','at':iso(now),'symbol':pos['symbol'],
       'position_id':pos['id'],'realized_pnl_usd':pos['realized_pnl_usd'],
       'reason':reason,'paper_only':True,'broker_submissions':0}
    ledger['events'].append(e);ledger['new_events'].append(e)
 local=now.astimezone(CT)
 allowed=local.weekday()<5 and ENTRY_START<=local.time()<ENTRY_END
 # Exactly one candidate fills only after newer verified quotes on same IDs,
 # with re-confirmed strategy signal, no daily repeat and <$100 risk.
 if allowed and report['status']=='CANDIDATE' and not ledger['open']:
  key=report['symbol']
  pending=ledger['pending_candidates'].get(key)
  accepted=any(p['opened_at'].startswith(day) for p in ledger['completed'])
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
      if D('0')<debit<=D(pending['net_debit']) and risk+account_risk<=D('100'):
       entry={'id':f'SNIPER-PAPER-{day}-{key}', 'symbol':key,'status':'OPEN',
         'opened_at':iso(now),'opening_debit':str(debit),
         'maximum_loss_usd':str(risk.quantize(D('.01'))),
         'maximum_profit_usd':str(((abs(D(report['short_strike'])-D(report['long_strike']))-debit)*100-D('2.60')).quantize(D('.01'))),
         'expiration':report['expiration'],'long_contract_id':report['long_contract_id'],
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
 atomic(state/'sniper_paper_ledger.json',ledger)
 atomic(state/'sniper_daily_evaluation.json',{'at':iso(now),'date':day,'strategy':'SNIPER',
  'evaluation':report,'new_paper_events':ledger['new_events'],
  'open_positions':len(ledger['open']),'completed_positions':len(ledger['completed']),
  'paper_only':True,'broker_submissions':0})
 return ledger
