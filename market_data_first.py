"""Fresh read-only market inputs -> independent technicals -> option collection plan.

This coordinator has no brokerage transport. The authenticated connector fetches
the requested data; option selection remains in each strategy's existing engine.
"""
import argparse
from datetime import datetime,time,timedelta,timezone
from decimal import Decimal as D
from pathlib import Path
from completed_bars import bar_close_reason,ET
from forward_paper import atomic,load
from replay import iso,recent,timestamp,CT
from robinhood_capture import chart_context
from bwb_context import context_for

def _bars(chart,now):
    if chart.get('interval')!='5minute' or chart.get('bounds')!='regular':raise ValueError('FIVE_MINUTE_RTH_CHART_REQUIRED')
    rows={}
    for b in chart.get('bars',[]):
        at=timestamp(b['begins_at']);local=at.astimezone(ET)
        if b.get('session')!='reg' or at+timedelta(minutes=5)>now:continue
        if b.get('interpolated') is True:raise ValueError('INTERPOLATED_BAR_NOT_ALLOWED')
        if not time(9,30)<=local.time().replace(tzinfo=None)<time(16):raise ValueError('RTH_BAR_OUTSIDE_SESSION')
        if local.minute%5 or local.second or local.microsecond:raise ValueError('BAR_GRID_INVALID')
        h,l,c,v=(D(str(b[k])) for k in ['high_price','low_price','close_price','volume'])
        if not all(x.is_finite() for x in (h,l,c,v)) or not 0<l<=c<=h or v<=0:raise ValueError('INVALID_BAR_VALUES')
        if at in rows:raise ValueError('DUPLICATE_BAR')
        rows[at]=b
    ordered=sorted(rows)
    if not ordered:raise ValueError('COMPLETED_BARS_UNAVAILABLE')
    for a,b in zip(ordered,ordered[1:]):
        if a.astimezone(ET).date()==b.astimezone(ET).date() and b-a!=timedelta(minutes=5):raise ValueError('BAR_GRID_GAP')
    reason=bar_close_reason(ordered[-1]+timedelta(minutes=5),now)
    if reason:raise ValueError(reason)
    return [rows[t] for t in ordered]

def technical(chart,quote,now):
    bars=_bars(chart,now)
    if len(bars)<20:raise ValueError('TWENTY_COMPLETED_BARS_REQUIRED')
    if not recent(quote.get('venue_last_trade_time'),now):raise ValueError('FRESH_EQUITY_REFERENCE_QUOTE_REQUIRED')
    today=[b for b in bars if timestamp(b['begins_at']).astimezone(ET).date()==now.astimezone(ET).date()]
    if len(today)<2:raise ValueError('CURRENT_SESSION_BARS_REQUIRED')
    # Session VWAP resets today; EMA uses genuine previous-session warmup.
    volume=sum(D(str(b['volume'])) for b in today)
    vwap=sum((D(b['high_price'])+D(b['low_price'])+D(b['close_price']))/3*D(str(b['volume'])) for b in today)/volume
    alpha=D(2)/21;state=D(bars[0]['close_price']);values=[]
    for b in bars:
        state=alpha*D(b['close_price'])+(1-alpha)*state;values.append(state)
    last=today[-1];prior=today[-2];close=D(last['close_price']);spot=D(quote['last_trade_price'])
    if not spot.is_finite() or spot<=0:raise ValueError('INVALID_EQUITY_REFERENCE_PRICE')
    bullish=close>D(prior['high_price']) and close>vwap and close>values[-1]>values[-2] and spot>vwap
    bearish=close<D(prior['low_price']) and close<vwap and close<values[-1]<values[-2] and spot<vwap
    return {'source':chart['symbol']+'_5MINUTE_RTH','bars_completed':len(bars),
        'bar_close_at':iso(timestamp(last['begins_at'])+timedelta(minutes=5)),
        'bar_close':str(close),'prior_completed_high':prior['high_price'],
        'prior_completed_low':prior['low_price'],'vwap':str(vwap),
        'ema20':str(values[-1]),'ema20_prior':str(values[-2]),
        'signal':'BULLISH' if bullish else 'BEARISH' if bearish else 'WAIT'}

def plan(raw,session,now):
    report={'generated_at_utc':iso(now),'market_batch_at_utc':raw.get('generated_at_utc'),
        'sequence':['FETCH_MARKET_DATA','VALIDATE_HISTORY_AND_QUOTES','EVALUATE_TECHNICALS',
                    'COLLECT_STRATEGY_OPTION_STRIKES','REFRESH_EXECUTABLE_QUOTES','PAPER_EVALUATE'],
        'paper_only':True,'broker_submissions':0,'strategies':{},'sniper_inputs':{}}
    try:
        if raw.get('source')!='ROBINHOOD_MCP_RESEARCH' or raw.get('research_only') is not True or raw.get('order_submission_enabled') is not False:
            raise ValueError('READ_ONLY_MARKET_PROVENANCE_REQUIRED')
        if not recent(raw.get('generated_at_utc'),now):raise ValueError('FRESH_MARKET_BATCH_REQUIRED')
        if session.get('source')!='ALPACA_CALENDAR' or session.get('is_trading_day') is not True or session.get('date')!=now.astimezone(ET).date().isoformat():
            raise ValueError('VERIFIED_CURRENT_SESSION_REQUIRED')
        if not timestamp(session['open'])<=now<=timestamp(session['close']):raise ValueError('OUTSIDE_VERIFIED_MARKET_SESSION')
    except (ValueError,TypeError,KeyError,AttributeError) as exc:
        report.update(status='FETCH_MARKET_DATA_REQUIRED',reason=str(exc));return report
    ctx=raw.get('market_context') or {}
    charts={x.get('symbol'):x for x in ctx.get('historicals',[])}
    quotes={x.get('symbol') or x.get('quote',{}).get('symbol'):x.get('quote',{}) for x in ctx.get('equity_quotes',[])}
    indices={x.get('symbol'):x for x in ctx.get('index_quotes',[])}
    for symbol in ('SPY','QQQ'):
        key='SNIPER_'+symbol
        try:
            t=technical(charts.get(symbol,{}),quotes.get(symbol,{}),now)
            direction=t['signal'];rights=['call'] if direction=='BULLISH' else ['put'] if direction=='BEARISH' else []
            report['strategies'][key]={'status':'COLLECT_OPTIONS' if rights else 'WAIT',
                'direction':direction,'rights':rights,'dte':[0,1],'preferred_dte':0,'holding_policy':'SAME_SESSION_EXIT','mandatory_exit_ct':'14:20',
                'spot':quotes[symbol]['last_trade_price'],'technical':t,
                'strike_policy':'RESOLVE_EXISTING_CHAIN_STRIKES_AROUND_CURRENT_SPOT_WITH_PROTECTIVE_VERTICAL;EVALUATE_NATURAL_DEBIT_AND_400_FEE_INCLUSIVE_RISK'}
            report['sniper_inputs'][symbol]={'symbol':symbol,'technical':t,'session':session,
                'underlying':{'last_price':quotes[symbol]['last_trade_price'],'updated_at':quotes[symbol]['venue_last_trade_time']}}
        except (ValueError,TypeError,KeyError,ArithmeticError) as exc:
            report['strategies'][key]={'status':'FETCH_MARKET_DATA_REQUIRED','reason':str(exc),'rights':[]}
    for symbol in ('SPX','XSP'):
        try:
            index=indices[symbol]
            if not recent(index.get('venue_timestamp'),now):raise ValueError('FRESH_INDEX_REFERENCE_QUOTE_REQUIRED')
            _bars(charts.get('SPY',{}),now)
            signal=chart_context(charts.get('SPY'),session,now)
            frame={'symbol':symbol,'spot':index['value'],'spot_at':index['venue_timestamp']}
            report['strategies']['CREDIT_'+symbol]={'status':'COLLECT_OPTIONS' if signal.get('technical_clear') else 'WAIT',
                'reason':signal.get('signal_reason'),'rights':['put'] if signal.get('technical_clear') else [],
                'spot':index['value'],'delta_bands':[[0.10,0.15],[0.15,0.25]],
                'wing_offsets':[5,10,15] if symbol=='SPX' else [1,2,3],
                'strike_policy':'EXISTING_OTM_PUT_SHORTS_IN_BOTH_DELTA_BANDS_PLUS_LOWER_PROTECTIVE_WINGS'}
            b=context_for(frame,raw,now)
            volok=b.get('ready') and D(b.get('relative_volume','0'))>=1
            report['strategies']['BWB_'+symbol]={'status':'COLLECT_OPTIONS' if volok else 'WAIT' if b.get('ready') or b.get('reason') in {'BWB_VWAP_EMA50_CONFLICT','BWB_NO_FORWARD_PRICE_TARGET'} else 'FETCH_MARKET_DATA_REQUIRED',
                'reason':b.get('reason') if not b.get('ready') else 'BWB_CONTEXT_CONFIRMED' if volok else 'BWB_RELATIVE_VOLUME_BELOW_ONE',
                'rights':['call' if b['direction']=='UP' else 'put'] if volok else [],
                'spot':index['value'],'context':b,'strike_policy':'AUTHENTIC_THREE_STRIKE_UNEQUAL_WING_LADDER_AROUND_PROJECTED_TARGET;BUY_1_SELL_2_BUY_1;500_MAX_RISK'}
        except (ValueError,TypeError,KeyError,ArithmeticError) as exc:
            for strategy in ('CREDIT_','BWB_'):
                report['strategies'][strategy+symbol]={'status':'FETCH_MARKET_DATA_REQUIRED','reason':str(exc),'rights':[]}
    report['status']='TECHNICALS_EVALUATED'
    report['entry_windows_ct']={'CREDIT':'10:00-11:00','SNIPER':'10:00-11:00','BWB':'10:00-12:30'}
    report['notice']='Collection plan only. Existing event, entry-time, risk and second-quote checks remain mandatory.'
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',required=True,type=Path)
    p.add_argument('--session',type=Path,default=Path('/home/tradebot/Trade-PMCC-Bot/inputs/alpaca_pmcc_research.json'))
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent)
    a=p.parse_args()
    from scan_paper import option_bot_idle
    if not option_bot_idle():raise SystemExit('OPTION_BOT_PRIORITY_DEFERRED')
    r=plan(load(a.input,{}),load(a.session,{}).get('session',{}),datetime.now(timezone.utc))
    atomic(a.root/'state/technical_collection_plan.json',r)
    import json
    print(json.dumps({'status':r['status'],'strategies':{k:{f:v for f,v in x.items() if f in ('status','reason','rights','direction')} for k,x in r['strategies'].items()}}))
    return 66 if r['status']=='FETCH_MARKET_DATA_REQUIRED' else 0

if __name__=='__main__':raise SystemExit(main())
