"""VWAP/EMA50/volume-by-bar research from actual completed SPY 5m RTH bars.

Volume profile is explicitly a *bar-level approximation*, not exchange tick
volume-at-price. Index projection uses contemporary verified index and SPY spot,
never an assumed 10x index ratio. Gamma observations are optional metadata.
"""
from __future__ import annotations
from datetime import datetime,timedelta
from decimal import Decimal as D
from replay import CT,ET,timestamp,number,spot_at

def context_for(frame,research,now):
    fail={'ready':False,'reason':'BWB_VERIFIED_53_BAR_CONTEXT_UNAVAILABLE'}
    charts=[x for x in (research.get('market_context') or {}).get('historicals',[]) if x.get('symbol')=='SPY' and x.get('interval')=='5minute' and x.get('bounds')=='regular']
    if len(charts)!=1:return fail
    quotes=[x.get('quote',{}) for x in (research.get('market_context') or {}).get('equity_quotes',[]) if x.get('symbol')=='SPY' or x.get('quote',{}).get('symbol')=='SPY']
    if len(quotes)!=1:return fail
    try:
        q=quotes[0];qt=timestamp(q['venue_last_trade_time']);spy_spot=number(q['last_trade_price']);index_spot=spot_at(frame,now)
        if spy_spot<=0 or index_spot is None or not -5<=(now-qt).total_seconds()<=60:return fail
        rows={}
        for r in charts[0]['bars']:
            at=timestamp(r['begins_at'])
            if r.get('session')!='reg' or at+timedelta(minutes=5)>now:continue
            if at in rows:return fail
            rows[at]=(number(r['high_price']),number(r['low_price']),number(r['close_price']),number(r['volume']))
        times=sorted(rows)
        if len(times)<53 or (now-times[-1]).total_seconds()>660:return fail
        recent=times[-53:]
        closes=[rows[t][2] for t in recent]
        if any(v<=0 for t in recent for v in rows[t]):return fail
        alpha=D(2)/D(51)
        # Require at least 53 historical bars. Seed the first actual close.
        state=closes[0];emas=[]
        for close in closes:
            state=alpha*close+(D('1')-alpha)*state
            emas.append(state)
        ema,slope=emas[-1],emas[-1]-emas[-4]
        today=now.astimezone(ET).date()
        today_rows=[rows[t] for t in times if t.astimezone(ET).date()==today]
        if len(today_rows)<3:return fail
        volume=sum(r[3] for r in today_rows)
        if volume<=0:return fail
        vwap=sum(((r[0]+r[1]+r[2])/D(3))*r[3] for r in today_rows)/volume
        relative=rows[times[-1]][3]/(volume/D(len(today_rows)))
        # Conservative near-term range target, derived from completed RTH bars.
        recent_bars=[rows[t] for t in times[-20:]]
        high=max(r[0] for r in recent_bars);low=min(r[1] for r in recent_bars)
        direction='UP' if spy_spot>vwap and spy_spot>ema and slope>0 else 'DOWN' if spy_spot<vwap and spy_spot<ema and slope<0 else 'UNCONFIRMED'
        if direction=='UNCONFIRMED':return {'ready':False,'reason':'BWB_VWAP_EMA50_CONFLICT'}
        # Approximate volume node by completed bar typical-price bin; not tick VPOC.
        bins={}
        for h,l,c,v in today_rows:
            typical=(h+l+c)/D(3)
            bucket=(typical/D('.50')).to_integral_value()*D('.50')
            bins[bucket]=bins.get(bucket,D(0))+v
        poc=max(bins,key=bins.get)
        # Target must lie ahead. A near-session volume node is a candidate; if
        # behind spot, use completed 20-bar resistance/support instead.
        target=poc if (poc>spy_spot if direction=='UP' else poc<spy_spot) else high if direction=='UP' else low
        if not (target>spy_spot if direction=='UP' else target<spy_spot):return {'ready':False,'reason':'BWB_NO_FORWARD_PRICE_TARGET'}
        ratio=index_spot/spy_spot
        return {'source':'VERIFIED_RTH_VWAP_EMA50_VOLUME_PROFILE','ready':True,
            'direction':direction,'target':str(target*ratio),'ema50':str(ema*ratio),
            'ema50_slope':str(slope*ratio),'vwap':str(vwap*ratio),
            'relative_volume':str(relative),'target_source':'COMPLETED_5M_BAR_VOLUME_BIN_PROXY_OR_20_BAR_RANGE',
            'volume_profile_method':'BAR_TYPICAL_PRICE_BIN_APPROXIMATION_NOT_TICK_VPOC',
            'contemporaneous_index_to_spy_spot_ratio':str(ratio),
            'underlying_price_timestamp':q['venue_last_trade_time'],
            'gamma_observation':{'status':'NOT_VERIFIED_FOR_CURRENT_SESSION'}}
    except (ValueError,TypeError,KeyError,ArithmeticError,ZeroDivisionError):return fail
