"""Independent Sniper equity debit-spread paper research; no broker order authority.

A daily evaluator, not an execution simulator. Does NOT inherit SPX/XSP 0DTE
entry rules. A qualified candidate is never a purchase without separately
verified later quotes, settlement/assignment handling, and a paper lifecycle.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation
from typing import Mapping
from replay import iso,CT

D=Decimal

@dataclass(frozen=True)
class ScanPolicy:
    min_dte:int=14
    max_dte:int=30
    max_risk:Decimal=D('100')
    capital:Decimal=D('500')
    max_snapshot_seconds:int=60
    symbols:tuple[str,...]=('SPY','QQQ')


def _num(v):
    x=D(str(v))
    if not x.is_finite():raise ValueError('NONFINITE_EVIDENCE')
    return x

def _age(v,now,limit=60):
    at=datetime.fromisoformat(str(v).replace('Z','+00:00'))
    return at.tzinfo is not None and -5<=(now-at).total_seconds()<=limit

def evaluate(payload:Mapping, now:datetime, policy=ScanPolicy()):
    """Evaluate completed-bar breakout with funded, capped long-call vertical.

    Evaluation never mutates an account. Absence of fresh independent quote
    observations is explicitly a pending fill, not a profitable paper purchase.
    """
    out={'strategy':'SNIPER','profile':'EQUITY_14_30_DTE','as_of_utc':iso(now),
         'status':'DATA_UNAVAILABLE','reason':'SNIPER_EQUITY_RESEARCH_NOT_PUBLISHED',
         'paper_entry':False,'broker_submissions':0,'paper_only':True,
         'execution_status':'NO_EXECUTABLE_PAPER_FILL'}
    if not isinstance(payload,Mapping) or not payload:return out
    if (payload.get('source')!='ROBINHOOD_MCP_RESEARCH' or payload.get('research_only') is not True
        or payload.get('paper_only') is not True or payload.get('order_submission_enabled') is not False
        or payload.get('broker_execution_enabled') is not False):
        out['reason']='SNIPER_RESEARCH_GOVERNANCE_INVALID';return out
    if not _age(payload.get('generated_at_utc'),now):
        out['reason']='SNIPER_SNAPSHOT_STALE';return out
    symbol=payload.get('symbol')
    out['symbol']=symbol
    if symbol not in policy.symbols:
        out['reason']='UNSUPPORTED_EQUITY_SYMBOL';return out
    underlying=payload.get('underlying') or {}
    tech=payload.get('technical') or {}
    events=payload.get('events') or {}
    try:
        if not _age(underlying.get('updated_at'),now) or not _age(tech.get('bar_close_at'),now):
            out['reason']='SNIPER_SPOT_OR_TECHNICAL_STALE';return out
        if events.get('earnings_clearance')!='CLEAR' or events.get('dividend_clearance')!='CLEAR':
            out['reason']='CORPORATE_EVENT_CLEARANCE_UNAVAILABLE';return out
        if not _age(events.get('verified_at_utc'),now,3600):
            out['reason']='CORPORATE_EVENT_CLEARANCE_STALE';return out
        if not events.get('provenance'):
            out['reason']='CORPORATE_EVENT_PROVENANCE_UNVERIFIED';return out
        if tech.get('source')!=f'{symbol}_5MINUTE_RTH':
            out['reason']='TECHNICAL_SOURCE_UNVERIFIED';return out
        if int(tech.get('bars_completed',0))<20:
            out['reason']='INSUFFICIENT_COMPLETED_TECHNICAL_BARS';return out
        spot=_num(underlying['last_price']);close=_num(tech['bar_close'])
        vwap=_num(tech['vwap']);ema20=_num(tech['ema20']);prior_ema20=_num(tech['ema20_prior'])
        prior_high=_num(tech['prior_completed_high'])
        prior_low=_num(tech.get('prior_completed_low',tech['prior_completed_high']))
        out['signal_evidence']={'close':str(close),'vwap':str(vwap),'ema20':str(ema20),
                                'prior_high':str(prior_high),'prior_low':str(prior_low),'bar_close_at':tech['bar_close_at']}
        bullish=close>prior_high and close>vwap and close>ema20>prior_ema20 and spot>vwap
        bearish=close<prior_low and close<vwap and close<ema20<prior_ema20 and spot<vwap
        if not bullish and not bearish:
            out.update(status='REJECTED',reason='BREAKOUT_UNCONFIRMED');return out
        right='CALL' if bullish else 'PUT'
        structure='CALL_DEBIT_SPREAD' if bullish else 'PUT_DEBIT_SPREAD'
    except (KeyError,TypeError,ValueError,ArithmeticError,InvalidOperation):
        out['reason']='SNIPER_TECHNICAL_EVIDENCE_INVALID';return out
    rows=[]
    for row in payload.get('contracts',[]):
        try:
            q=row['quote']
            expiry=datetime.fromisoformat(str(row['expiration_date'])).date()
            dte=(expiry-now.astimezone(CT).date()).days
            if datetime.fromisoformat(events.get('clear_through_date','1900-01-01')).date()<expiry:
                continue
            bid,ask=_num(q['bid_price']),_num(q['ask_price'])
            if (row.get('symbol')!=symbol or row.get('right')!=right
                or row.get('exercise_style')!='AMERICAN' or row.get('settlement_type')!='PHYSICAL'
                or row.get('state')!='active' or row.get('tradability')!='tradable'
                or int(row.get('multiplier'))!=100 or not policy.min_dte<=dte<=policy.max_dte
                or q.get('instrument_id')!=row.get('instrument_id') or not _age(q['updated_at'],now)
                or bid<=0 or ask<bid or int(q.get('bid_size',0))<1 or int(q.get('ask_size',0))<1
                or _num(q['open_interest'])<100 or _num(q['volume'])<1
                or not D('-1')<=_num(q['delta'])<=D('1')
                or _num(q['implied_volatility'])<=0):continue
            rows.append((row,expiry,_num(row['strike_price']),bid,ask))
        except (ValueError,KeyError,TypeError,ArithmeticError,InvalidOperation):continue
    best=None
    for long,exp,strike_long,bid_long,ask_long in rows:
        for short,exp2,strike_short,bid_short,ask_short in rows:
            if long is short or exp!=exp2:continue
            if (right=='CALL' and strike_short<=strike_long) or (right=='PUT' and strike_short>=strike_long):continue
            debit=ask_long-bid_short
            width=abs(strike_short-strike_long)
            if not D('0')<debit<width or debit*100>policy.max_risk:continue
            # Natural bid/ask, conservative debit. Risk includes $0.65 x4 commissions.
            fees=D('2.60');max_loss=debit*100+fees
            if max_loss>policy.max_risk:continue
            max_profit=(width-debit)*100-fees
            if max_profit<=max_loss:continue
            proposal={'symbol':symbol,'strategy':'SNIPER','structure':structure,
                'expiration':str(exp),'long_contract_id':long['instrument_id'],
                'short_contract_id':short['instrument_id'],
                'long_strike':str(strike_long),'short_strike':str(strike_short),
                'max_loss_usd':str(max_loss),'maximum_loss_usd':str(max_loss),
                'maximum_profit_usd':str(max_profit),'net_debit':str(debit),
                'paper_entry':False,'execution_status':'PENDING_NEWER_QUOTES'}
            key=(-max_profit/max_loss,max_loss,abs(strike_long-spot),long['instrument_id'],short['instrument_id'])
            if best is None or key<best[0]:best=(key,proposal)
    if best is None:
        out.update(status='REJECTED',reason='NO_VERIFIED_14_TO_30_DTE_SPREAD');return out
    out.update(best[1]);out.update(status='CANDIDATE',reason='PENDING_FRESH_SECOND_QUOTE_AND_PAPER_EXECUTION')
    return out
