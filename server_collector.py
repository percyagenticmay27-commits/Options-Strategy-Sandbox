"""Independent Alpaca READ-ONLY Sandbox provider capability and freshness collector.

Never submits orders, manufactures index spot, invents delta or stamps stale
quotes with a new time. Only publishes provider evidence diagnostics until a
complete, supported, authenticated SPX/XSP source adapter exists.
"""
from __future__ import annotations
import json,os,sys,urllib.error,urllib.parse,urllib.request
from datetime import datetime,time,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from forward_paper import atomic,load
from replay import timestamp

ROOT=Path(__file__).resolve().parent
ET=ZoneInfo('America/New_York')
ALPACA_DATA='https://data.alpaca.markets'
ALPACA_PAPER='https://paper-api.alpaca.markets'

class ProviderIssue(Exception):
    def __init__(self,code):super().__init__(code);self.code=code


def get_json(base,path,params,env,timeout=9):
    """Fixed allowlisted READ operations only, never a trading endpoint."""
    if base==ALPACA_DATA:
        if not path.startswith(('/v2/stocks/SPY/bars','/v1beta1/options/snapshots')):
            raise ProviderIssue('UNAPPROVED_MARKET_DATA_PATH')
    elif base==ALPACA_PAPER:
        if path!='/v2/options/contracts':raise ProviderIssue('UNAPPROVED_CONTRACT_PATH')
    else:raise ProviderIssue('UNAPPROVED_PROVIDER_HOST')
    if not env.get('APCA_API_KEY_ID') or not env.get('APCA_API_SECRET_KEY'):
        raise ProviderIssue('ALPACA_READ_ONLY_CREDENTIALS_MISSING')
    url=base+path+'?'+urllib.parse.urlencode(params)
    req=urllib.request.Request(url,headers={
        'APCA-API-KEY-ID':env['APCA_API_KEY_ID'],
        'APCA-API-SECRET-KEY':env['APCA_API_SECRET_KEY'],
        'Accept':'application/json'},method='GET')
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:
            data=json.load(r)
            if not isinstance(data,dict):raise ProviderIssue('PROVIDER_PAYLOAD_NOT_OBJECT')
            return data
    except urllib.error.HTTPError as exc:
        if exc.code==403:raise ProviderIssue('PROVIDER_403_OPRA_ENTITLEMENT_OR_AGREEMENT_REQUIRED') from None
        if exc.code==401:raise ProviderIssue('PROVIDER_401_CREDENTIALS_INVALID') from None
        if exc.code==429:raise ProviderIssue('PROVIDER_429_RATE_LIMIT') from None
        raise ProviderIssue('PROVIDER_HTTP_'+str(exc.code)) from None
    except (TimeoutError,urllib.error.URLError):
        raise ProviderIssue('PROVIDER_NETWORK_UNAVAILABLE') from None
    except (ValueError,TypeError,KeyError):
        raise ProviderIssue('PROVIDER_RESPONSE_MALFORMED') from None


def collect(now,fetch,*,session=None):
    """Probe independently authenticated capabilities; fail closed on gaps."""
    if now.tzinfo is None:raise ValueError('TIMEZONE_REQUIRED')
    observed=now.astimezone(ET)
    result={'generated_at_utc':now.isoformat(),'collector':'ALPACA_SERVER_READ_ONLY',
        'mode':'CAPABILITY_AND_FRESHNESS_PREFLIGHT','status':'PROVIDER_EVIDENCE_INCOMPLETE',
        'paper_only':True,'live_trading_enabled':False,'order_submission_enabled':False,
        'broker_submissions':0,'source_substitution_allowed':False,
        'published_actionable_provider_batch':False,
        'provider_capabilities':{},'blockers':[],
        'requested_research_symbols':['SPXW','XSP'],'strategy_symbol':['SPX','XSP'],
        'next_action':'VERIFY_PROVIDERS_AND_KEEP_SANDBOX_ENTRY_BLOCKED'}
    def failure(code):
        if code not in result['blockers']:result['blockers'].append(code)
    same_day=bool(session and session.get('source')=='ALPACA_CALENDAR'
        and session.get('date')==observed.date().isoformat()
        and session.get('is_trading_day') is True)
    if not same_day:
        failure('ALPACA_SESSION_VERIFICATION_REQUIRED')
    if not time(9,30)<=observed.time().replace(tzinfo=None)<time(16):
        result['status']='OUTSIDE_REGULAR_TRADING_HOURS'
        result['next_action']='WAIT_FOR_NEXT_VERIFIED_REGULAR_SESSION'
        return result
    # Verify SPY chart acquisition, not just an HTTP 200 response.
    open_time=observed.replace(hour=9,minute=30,second=0,microsecond=0)
    try:
        bars=fetch(ALPACA_DATA,'/v2/stocks/SPY/bars',{
            'timeframe':'5Min','start':open_time.isoformat(),
            'end':now.isoformat(),'feed':'iex','limit':120})
        rows=bars.get('bars') or []
        if not rows:raise ProviderIssue('SPY_FIVE_MINUTE_BARS_MISSING')
        last=timestamp(rows[-1]['t'])
        if last>now or now-(last+timedelta(minutes=5))>timedelta(minutes=6):
            raise ProviderIssue('SPY_FIVE_MINUTE_BARS_STALE')
        result['provider_capabilities']['SPY_5MIN_IEX']={'status':'AVAILABLE',
             'bar_count':len(rows),'last_bar_started_at_utc':last.isoformat(),
             'feed':'IEX_SINGLE_EXCHANGE_VOLUME_NOT_SIP'}
    except (ProviderIssue,KeyError,ValueError,TypeError) as exc:
        reason=exc.code if isinstance(exc,ProviderIssue) else 'SPY_BARS_INVALID'
        result['provider_capabilities']['SPY_5MIN_IEX']={'status':'UNAVAILABLE','reason':reason}
        failure(reason)
    for symbol,root in (('SPX','SPXW'),('XSP','XSP')):
        key=f'{root}_PM_SETTLED_CONTRACT_LOOKUP'
        try:
            contract=fetch(ALPACA_PAPER,'/v2/options/contracts',{
                'underlying_symbols':symbol,'root_symbol':root,
                'expiration_date':observed.date().isoformat(),
                'status':'active','limit':25})
            contracts=contract.get('option_contracts') or []
            good=[v for v in contracts if v.get('root_symbol')==root and
                  v.get('expiration_date')==observed.date().isoformat()]
            if not good:raise ProviderIssue(root+'_VERIFIED_CONTRACTS_MISSING')
            result['provider_capabilities'][key]={'status':'AVAILABLE',
                'returned':len(good),'pm_settled_root':root,
                'expiration_date':observed.date().isoformat()}
        except (ProviderIssue,KeyError,TypeError,ValueError) as exc:
            reason=exc.code if isinstance(exc,ProviderIssue) else 'CONTRACT_METADATA_MALFORMED'
            result['provider_capabilities'][key]={'status':'UNAVAILABLE','reason':reason}
            failure(reason)
    # OPRA rights are account-specific. If subscription/agreement is absent,
    # an indicative quote is NOT permission to bypass verified real Greeks.
    # Use a real OCC symbol from a returned contract; never guess strikes.
    # The lookup above is intentionally limited, preventing bulk chain
    # retrieval or user-secret exposure when entitlement is missing.
    # OPRA quote access is explicitly probed once with an actual listed OCC.
    if not result['provider_capabilities'].get('XSP_PM_SETTLED_CONTRACT_LOOKUP',{}).get('status')=='AVAILABLE':
        failure('OPRA_INDEX_OPTION_QUOTE_CAPABILITY_UNVERIFIED')
    else:
        try:
            options=fetch(ALPACA_PAPER,'/v2/options/contracts',{
                'underlying_symbols':'XSP','root_symbol':'XSP',
                'expiration_date':observed.date().isoformat(),
                'status':'active','limit':2})
            occ=next((v.get('symbol') for v in options.get('option_contracts',[])
                   if v.get('root_symbol')=='XSP' and v.get('symbol')),None)
            if not occ:raise ProviderIssue('XSP_OPTION_SYMBOL_MISSING')
            data=fetch(ALPACA_DATA,'/v1beta1/options/snapshots',{
                'symbols':occ,'feed':'opra'})
            snapshot=(data.get('snapshots') or {}).get(occ) or {}
            quote=snapshot.get('latestQuote') or {}
            if not quote:raise ProviderIssue('OPRA_OPTIONS_QUOTE_MISSING')
            ts=timestamp(quote['t'])
            if ts>now or (now-ts).total_seconds()>60:raise ProviderIssue('OPRA_OPTIONS_QUOTE_STALE')
            if not isinstance(snapshot.get('greeks'),dict) or snapshot['greeks'].get('delta') is None:
                raise ProviderIssue('OPRA_DELTA_GREEKS_UNAVAILABLE')
            result['provider_capabilities']['SPXW_XSP_OPRA']={
                'status':'PARTIAL_VERIFIED_SAMPLE','sample_quote_timestamp_utc':ts.isoformat()}
            failure('COMPLETE_SPXW_XSP_OPRA_CHAIN_AND_PROTECTIVE_WINGS_NOT_YET_COLLECTED')
        except (ProviderIssue,KeyError,TypeError,ValueError) as exc:
            reason=exc.code if isinstance(exc,ProviderIssue) else 'OPRA_OPTIONS_RESPONSE_INVALID'
            result['provider_capabilities']['SPXW_XSP_OPRA']={'status':'UNAVAILABLE','reason':reason}
            failure(reason)
    # Alpaca's current offering does not supply real cash SPX/XSP index spot.
    # Critically, neither SPY*10 nor contract mids can be substituted.
    result['provider_capabilities']['SPX_XSP_CASH_INDEX_SPOT']={
        'status':'UNAVAILABLE','reason':'AUTHORIZED_TIMESTAMPED_INDEX_SPOT_PROVIDER_NOT_CONFIGURED'}
    failure('AUTHORIZED_TIMESTAMPED_INDEX_SPOT_PROVIDER_NOT_CONFIGURED')
    if result['blockers']:
        result['status']='PROVIDER_EVIDENCE_INCOMPLETE'
        result['next_action']='FIX_ENTITLEMENTS_AND_INDEX_SPOT_PROVIDER_BEFORE_ENABLED_PUBLISH_AND_SCAN'
    return result


def main():
    root=ROOT
    now=datetime.now(timezone.utc)
    session=load(Path('/home/tradebot/Trade-PMCC-Bot/inputs/alpaca_pmcc_research.json'),{}).get('session',{})
    def fetch(base,path,params):return get_json(base,path,params,os.environ)
    try:result=collect(now,fetch,session=session)
    except Exception as exc:
        result={'status':'COLLECTOR_INTERNAL_ERROR','reason':type(exc).__name__,
            'generated_at_utc':now.isoformat(),'paper_only':True,
            'broker_submissions':0,'published_actionable_provider_batch':False}
    atomic(root/'state/server_collector_health.json',result)
    print(json.dumps(result,sort_keys=True),flush=True)
    return 0 if result['status'] in ('PROVIDER_EVIDENCE_INCOMPLETE','OUTSIDE_REGULAR_TRADING_HOURS') else 66

if __name__=='__main__':raise SystemExit(main())
