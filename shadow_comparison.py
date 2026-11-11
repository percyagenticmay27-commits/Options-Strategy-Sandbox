"""Read-only independent paper-vs-shadow comparison; no external writes except output."""
from __future__ import annotations
from decimal import Decimal as D
from pathlib import Path
from forward_paper import load,atomic

def metrics(rows):
    completed=[p for p in rows if p.get('realized_pnl') is not None]
    completed.sort(key=lambda p:p.get('closed_at') or '')
    gains=[D(str(p['realized_pnl'])) for p in completed]
    cumulative=peak=dd=D(0)
    for gain in gains:
        cumulative+=gain;peak=max(peak,cumulative);dd=max(dd,peak-cumulative)
    return {'closed':len(gains),'wins':sum(x>0 for x in gains),'losses':sum(x<0 for x in gains),
       'win_rate_pct':str((D(sum(x>0 for x in gains))*100/len(gains)).quantize(D('.01'))) if gains else None,
       'realized_pnl_usd':str(cumulative.quantize(D('.01'))),
       'realized_trade_sequence_max_drawdown_usd':str(dd.quantize(D('.01'))),
       'drawdown_scope':'CLOSED_TRADE_SEQUENCE_NOT_INTRATRADE'}

def build(root,option_path=None):
    state=Path(root)/'state'
    shadow=load(state/'regime_shadow_ledger.json',{})
    low=load(state/'paper_ledger.json',{})
    bwb=load(state/'bwb_paper_ledger.json',{})
    option=load(option_path,{}) if option_path else {}
    options=metrics(option.get('completed',[])) if option.get('paper_only') is True else {'status':'UNAVAILABLE'}
    books={}
    for name,p in low.get('profiles',{}).items():
        rows=[{'realized_pnl':x.get('pnl'),'closed_at':x.get('closed_at')} for x in p.get('trades',[]) if x.get('status')=='CLOSED']
        books[name]={**metrics(rows),'book_type':p.get('book_type')}
    bwb_rows=[{'realized_pnl':p.get('realized_pnl_usd'),'closed_at':p.get('closed_at')} for p in bwb.get('completed',[])]
    comparison={'mode':'READ_ONLY_COMPARISON','broker_submissions':0,'books':{
       'OPTION_001':options,'SANDBOX_BWB':metrics(bwb_rows),**{'SANDBOX_'+k.upper():v for k,v in books.items()},
       'SHADOW_FOUR_REGIMES':shadow.get('stats',{'status':'NO_OBSERVATIONS'})},
       'caveat':'Trade-sequence drawdowns differ from intratrade and equity-curve drawdowns. Shadow has no W/L until genuine independently verified paper fills.'}
    return comparison

def publish(root,option_path=None):
    r=build(root,option_path)
    atomic(Path(root)/'state/shadow_comparison.json',r)
    return r
