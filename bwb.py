"""Read-only 0DTE SPX/XSP broken-wing-butterfly candidate research.

Three strikes and four contracts: BUY 1 lower, SELL 2 body, BUY 1 upper.
All legs are of the same right, expiration and PM-cash-settled series.
Quotes are at the natural bid/ask; no assumed midpoint fills.
"""
from __future__ import annotations
from decimal import Decimal as D
from itertools import combinations
from datetime import datetime,time
from replay import CT,ROOTS,quotes_at,spot_at,market_open,number,iso

MAX_PURCHASE=D('500')
MAX_RISK=D('500')
FEE_ROUND_TRIP=D('5.20')  # four contracts @ $0.65 per side
START=time(10,0)
END=time(12,30)

def payoff(strikes,right,opening_credit,multiplier=D('100')):
    lo,body,hi=map(D,strikes)
    def intrinsic(s,p):return max(D('0'),p-s) if right=='C' else max(D('0'),s-p)
    def pnl(p):return (opening_credit+intrinsic(lo,p)-D('2')*intrinsic(body,p)+intrinsic(hi,p))*multiplier
    values=[pnl(p) for p in (D('0'),lo,body,hi,hi+max(D('1'),hi-lo)*2)]
    return max(values),-min(values),pnl(body)

def candidate(frame,now):
    base={'strategy':'BWB_0DTE','symbol':frame.get('symbol'),'at':iso(now),
          'status':'REJECTED','reason':'NO_QUALIFIED_BROKEN_WING',
          'paper_only':True,'paper_entry':False,'broker_submissions':0,
          'purchase_limit_usd':'500.00','max_defined_risk_usd':'500.00'}
    if not market_open(frame,now):base.update(status='DATA_UNAVAILABLE',reason='SESSION_UNAVAILABLE');return base
    ct=now.astimezone(CT)
    if not START<=ct.time()<END:base['reason']='OUTSIDE_BWB_ENTRY_WINDOW';return base
    if frame.get('calendar_status')!='CLEAR' or frame.get('event_clear') is not True:
        base.update(status='DATA_UNAVAILABLE',reason='EVENT_CALENDAR_UNAVAILABLE');return base
    if frame.get('symbol') not in ROOTS:base.update(status='DATA_UNAVAILABLE',reason='UNSUPPORTED_SYMBOL');return base
    spot=spot_at(frame,now)
    if spot is None:base.update(status='DATA_UNAVAILABLE',reason='FRESH_INDEX_SPOT_UNAVAILABLE');return base
    # This BWB deliberately uses actual validated multi-factor direction and
    # destination, not the low-delta VWAP reclaim/support flag.
    ctx=frame.get('bwb_context') or {}
    if ctx.get('source')!='VERIFIED_RTH_VWAP_EMA50_VOLUME_PROFILE' or ctx.get('ready') is not True:
        base.update(status='DATA_UNAVAILABLE',reason='BWB_PRICE_ACTION_AND_VOLUME_PROFILE_UNAVAILABLE');return base
    direction=ctx.get('direction')
    if direction not in ('UP','DOWN') or not ctx.get('target'):
        base['reason']='BWB_DIRECTION_OR_TARGET_UNCONFIRMED';return base
    try:
        target=number(ctx['target']);ema=number(ctx['ema50']);slope=number(ctx['ema50_slope']);vwap=number(ctx['vwap'])
        relvol=number(ctx['relative_volume'])
        if relvol<D('1') or (direction=='UP' and not(spot>vwap and spot>ema and slope>0 and target>spot)) or (direction=='DOWN' and not(spot<vwap and spot<ema and slope<0 and target<spot)):
            base['reason']='BWB_MOMENTUM_NOT_CONFIRMED';return base
    except (TypeError,ValueError,KeyError):
        base.update(status='DATA_UNAVAILABLE',reason='BWB_INVALID_TARGET_CONTEXT');return base
    quotes=quotes_at(frame,now)
    right='C' if direction=='UP' else 'P'
    options=[q for q in quotes.values() if q.contract.right==right and q.contract.expiry==ct.date() and q.bid_size>=2 and q.ask_size>=1]
    ranked=[]
    for one,two,three in combinations(sorted(options,key=lambda q:q.contract.strike),3):
        a,b,c=(q.contract.strike for q in (one,two,three))
        left=b-a;right_width=c-b
        if left<=0 or right_width<=0 or left==right_width:continue
        # directional butterflies, body nearer projected target than spot
        if not ((direction=='UP' and b>spot) or (direction=='DOWN' and b<spot)):continue
        # For puts same payoff decomposition applies; directional puts have
        # their payout peak at the body too.
        debit=(one.ask+three.ask-D('2')*two.bid)*D('100')
        credit=-debit/D('100')
        upper,loss,at_body=payoff((a,b,c),right,credit)
        total_loss=loss+FEE_ROUND_TRIP
        purchase=max(D('0'),debit)+D('2.60') # one-way four-contract fees
        if purchase>MAX_PURCHASE or total_loss>MAX_RISK or total_loss<=0:continue
        if at_body<=0 or at_body-FEE_ROUND_TRIP<max(D('20'),total_loss*D('.25')):continue
        # GEX is a research overlay, never interpret a missing/30-min delayed
        # estimate as a fresh executable target. Verified volume POC target
        # and price-action target are required in ctx.
        score=(abs(b-target),total_loss,purchase,frame['symbol'],str(a),str(b),str(c))
        ranked.append((score,{'status':'CANDIDATE','reason':'PENDING_SECOND_NEWER_LEG_QUOTES',
           'direction':direction,'right':right,'expiry':ct.date().isoformat(),
           'strikes':[str(a),str(b),str(c)],'leg_ids':[one.contract.key,two.contract.key,three.contract.key],
           'quantity':[1,-2,1], 'net_debit_usd':str(debit.quantize(D('.01'))),
           'purchase_amount_usd':str(purchase.quantize(D('.01'))),
           'maximum_loss_usd':str(total_loss.quantize(D('.01'))),
           'maximum_profit_usd':str((upper-FEE_ROUND_TRIP).quantize(D('.01'))),
           'target':str(target),'gex_observation':ctx.get('gamma_observation',{'status':'NOT_AVAILABLE'}),
           'quoted_at':[iso(q.at) for q in (one,two,three)]}))
    if not ranked:
        base['reason']='NO_FRESH_LIQUID_DEFINED_RISK_BWB_UNDER_500';return base
    result=min(ranked,key=lambda pair:pair[0])[1]
    return {**base,**result}
