"""Independent SPX/XSP BWB experimental local-paper lifecycle, no broker order code.

One four-contract 0DTE BWB per day, $500 maximum paid and defined loss;
second genuinely newer quote observation required for a simulated fill.
Never assumes expiration income without settlement verification.
"""
from __future__ import annotations
from datetime import datetime,time
from decimal import Decimal as D
from pathlib import Path
from forward_paper import atomic,load
from replay import CT,quotes_at,market_open,spot_at,iso,timestamp
from bwb import candidate,payoff,MAX_RISK,MAX_PURCHASE,FEE_ROUND_TRIP

EXIT_TIME=time(14,25)   # CT, prior to normal cash settlement

def _mark(frame,leg_ids,right,qty,now,close=False):
    quotes=quotes_at(frame,now)
    legs=[quotes.get(k) for k in leg_ids]
    if any(v is None or v.contract.right!=right or v.bid_size<abs(D(n)) or v.ask_size<abs(D(n)) for v,n in zip(legs,qty)):return None
    # Convention: + quantity means long, - means short. Longs bought at ask,
    # shorts sold at bid; opposite sides to close.
    cashflow=D('0')
    for q,n in zip(legs,qty):
        side_long=n>0
        px=q.bid if (side_long and close) or (not side_long and not close) else q.ask
        cashflow+=(D('1') if side_long else D('-1'))*px*abs(D(n))*D('100')
    return cashflow, [iso(q.at) for q in legs]

def run(root,frames,now,calendar_status='CLEAR'):
    root=Path(root);state=root/'state';state.mkdir(parents=True,exist_ok=True)
    ledger=load(state/'bwb_paper_ledger.json',{'schema_version':'1.0','strategy':'BWB_0DTE',
        'paper_only':True,'broker_submissions':0,'open':[],'completed':[],
        'events':[],'pending':{},'daily_evaluations':{}})
    day=now.astimezone(CT).date().isoformat()
    new_events=[]
    evaluations=[]
    # Prioritize closing a held butterfly before considering another entry.
    for pos in list(ledger['open']):
        row=next((f for f in frames if f['symbol']==pos['symbol']),None)
        if not row or not market_open(row,now):
            pos['last_reason']='FRESH_HELD_LEG_QUOTES_REQUIRED';continue
        marked=_mark(row,pos['leg_ids'],pos['right'],pos['quantity'],now,close=True)
        if not marked:
            pos['last_reason']='FRESH_HELD_LEG_QUOTES_REQUIRED';continue
        close_cash,quote_times=marked
        # Opening cashflow and closing cashflow are opposite signs.
        pnl=-D(pos['opening_debit_usd'])+close_cash-D('5.20')
        pos['unrealized_pnl_usd']=str(pnl.quantize(D('.01')))
        pos['last_reason']='FRESH_MARK'
        pos['last_quote_at']=iso(now)
        maximum_loss=D(pos['maximum_loss_usd'])
        max_profit=D(pos['maximum_profit_usd'])
        ct=now.astimezone(CT).time()
        reason=('MANDATORY_TIME_EXIT' if ct>=EXIT_TIME else
                'HALF_MAX_LOSS' if pnl<=-maximum_loss*D('.5') else
                'TAKE_PROFIT_50_PERCENT' if pnl>=max_profit*D('.5') else None)
        if reason:
            pos.update(status='CLOSED',closed_at=iso(now),realized_pnl_usd=pos['unrealized_pnl_usd'],
                       exit_reason=reason,closing_cashflow_usd=str(close_cash))
            ledger['open'].remove(pos);ledger['completed'].append(pos)
            event={'action':'PAPER_SELL','position_id':pos['id'],'symbol':pos['symbol'],
                   'at':iso(now),'realized_pnl_usd':pos['realized_pnl_usd'],
                   'reason':reason,'paper_only':True,'broker_submissions':0}
            new_events.append(event)
    existing_today=any(p['opened_at'].startswith(day) for p in ledger['completed']+ledger['open'])
    for f in frames:
        evidence=candidate(f,now)
        evaluations.append(evidence)
        if (not existing_today and not ledger['open'] and evidence['status']=='CANDIDATE'
            and calendar_status=='CLEAR'):
            prior=ledger['pending'].get(f['symbol'])
            keys=['leg_ids','strikes','quantity','direction','right','expiry']
            if prior and all(prior.get(k)==evidence[k] for k in keys):
                age=(now-timestamp(prior['at'])).total_seconds()
                marked=_mark(f,evidence['leg_ids'],evidence['right'],evidence['quantity'],now)
                if 0<age<=60 and marked and all(timestamp(a)>timestamp(b) for a,b in zip(marked[1],prior['quotes_at'])):
                    debit=marked[0]  # positive cost to buy four-leg structure
                    credit=-debit/D('100')
                    _,maxloss,maxprofit=payoff(evidence['strikes'],evidence['right'],credit)
                    paid=max(D('0'),debit)+D('2.60')
                    risk=maxloss+FEE_ROUND_TRIP
                    if (paid<=MAX_PURCHASE and 0<risk<=MAX_RISK and
                        maxprofit-FEE_ROUND_TRIP>=max(D('20'),risk*D('.25')) and
                        debit<=D(evidence['net_debit_usd'])):
                        pos={'id':f'BWB-PAPER-{day}-{f["symbol"]}','status':'OPEN',
                             'symbol':f['symbol'],'opened_at':iso(now),
                             'right':evidence['right'],'direction':evidence['direction'],
                             'expiry':evidence['expiry'],'strikes':evidence['strikes'],
                             'leg_ids':evidence['leg_ids'],'quantity':evidence['quantity'],
                             'opening_debit_usd':str(debit.quantize(D('.01'))),
                             'purchase_amount_usd':str(paid.quantize(D('.01'))),
                             'maximum_loss_usd':str(risk.quantize(D('.01'))),
                             'maximum_profit_usd':str((maxprofit-FEE_ROUND_TRIP).quantize(D('.01'))),
                             'target':evidence['target'],'paper_only':True,'broker_submissions':0}
                        ledger['open'].append(pos);existing_today=True
                        new_events.append({'action':'PAPER_BUY','position_id':pos['id'],
                           'symbol':f['symbol'],'at':iso(now),'debit_usd':pos['opening_debit_usd'],
                           'purchase_amount_usd':pos['purchase_amount_usd'],
                           'risk_usd':pos['maximum_loss_usd'],'paper_only':True,'broker_submissions':0})
            if not existing_today:
                ledger['pending'][f['symbol']]={k:evidence[k] for k in keys}
                ledger['pending'][f['symbol']].update(at=iso(now),quotes_at=evidence['quoted_at'],
                  net_debit_usd=evidence['net_debit_usd'])
        else:
            ledger['pending'].pop(f['symbol'],None)
    # Existing prior-day candidate is ineligible; no forced paper entry.
    ledger['pending']={k:v for k,v in ledger['pending'].items() if timestamp(v['at']).astimezone(CT).date().isoformat()==day}
    if not evaluations:
        evaluations=[{'strategy':'BWB_0DTE','status':'DATA_UNAVAILABLE',
           'reason':'NO_CURRENT_VERIFIED_SPX_XSP_FRAME','paper_entry':False}]
    ledger['latest_evaluations']=evaluations
    ledger['daily_evaluations'][day]={'at':iso(now),'results':evaluations}
    ledger['events'].extend(new_events)
    ledger['new_events']=new_events
    ledger['paper_only']=True;ledger['broker_submissions']=0
    ledger['purchase_limit_usd']='500.00'
    ledger['max_defined_risk_usd']='500.00'
    ledger['last_scan_utc']=iso(now)
    atomic(state/'bwb_paper_ledger.json',ledger)
    atomic(state/'bwb_daily_evaluation.json',{'at':iso(now),'date':day,
       'strategy':'BWB_0DTE','results':evaluations,
       'open_positions':len(ledger['open']), 'completed_positions':len(ledger['completed']),
       'new_paper_events':new_events,'paper_only':True,'broker_submissions':0})
    return ledger
