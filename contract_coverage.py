"""Read-only quote acquisition planner for the Sandbox.

The authenticated Robinhood connector performs the real fetch; this module
only describes what it needs, audits the returned coverage, and never inserts
synthetic contracts, quotes, prices or timestamps.
"""
from __future__ import annotations
import argparse,json
from collections import Counter
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
from forward_paper import atomic,load
from replay import PROFILES,RISK_CAP,MIN_RETURN,iso,recent

# For defined-risk one-lot credit spreads, maximum width is a useful PRIORITY,
# not a substitute for the exact natural-credit risk/economic computation.
# The planner asks for multiple nearby wings per short as well as both delta
# books and the independent BWB; no guessed deltas or synthetic quotes.
WING_STRIKES={'SPX':('5','10','15'),'XSP':('1','2','3')}
MIN_UNIQUE_PUT_STRIKES=6
MIN_QUOTED_CONTRACTS=12

def request_plan(now=None):
    now=now or datetime.now(timezone.utc)
    return {'status':'COLLECT_FROM_AUTHENTICATED_ROBINHOOD','generated_at_utc':iso(now),
      'symbols':{s:{
       'expiry':'CURRENT_VERIFIED_PM_SETTLED_SESSION_EXPIRATION',
       'rights':['put','call'],
       'short_delta_bands':{k:[str(b[0]),str(b[1])] for k,b in PROFILES.items()},
       'short_contracts':'ALL_MATCHING_VERIFIED_DELTA_BANDS_AND_ADJACENT_STRIKES',
       'protective_wing_offsets_points':list(WING_STRIKES[s]),
       'wing_policy':'FETCH_EXISTING_LOWER_STRIKE_PUT_WINGS_AND_HIGHER_STRIKE_CALL_WINGS;REQUIRE_SAME_EXPIRY_AND_PM_SETTLEMENT',
       'bwb_policy':'COLLECT_ADJACENT_THREE_LEG_PUT_AND_CALL_STRIKE_LADDERS_WITH_BOTH_SIDES_EXECUTABLE',
       'required_min_unique_put_strikes':MIN_UNIQUE_PUT_STRIKES,
       'target_min_quoted_contracts':MIN_QUOTED_CONTRACTS,
       'contract_metadata':'SOURCE_VERIFIED_CHAIN_ID_EXERCISE_SETTLEMENT_TRADABILITY_MULTIPLIER',
       'quote_fields':'ORIGINAL_PROVIDER_TIMESTAMPS_EXECUTABLE_BID_ASK_SIZES_ALL_GREEKS_OPEN_INTEREST_VOLUME',
      } for s in ('SPX','XSP')},
      'market_context':'CURRENT_SPX_XSP_INDEX_SPOT_AND_COMPLETED_SPY_RTH_5MIN_BARS',
      'short_delta_max':str(max(b[1] for b in PROFILES.values())),
      'max_risk_per_book_usd':str(RISK_CAP),
      'minimum_net_profit_to_max_loss':str(MIN_RETURN),
      'max_quote_age_seconds':60,
      'second_quote_batch':'ONLY_IF_AN_EXACT_ELIGIBLE_PAPER_SPREAD_WAS_QUEUED;REQUERY_ITS_LEG_IDS_WITH_NEW_PROVIDER_TIMESTAMPS',
      'paper_only':True,'broker_submissions':0,
      'important':'Coverage targets are research collection goals, not trading eligibility or a reason to invent missing instruments.'}

def audit(raw,now=None):
    now=now or datetime.now(timezone.utc)
    result={}
    for symbol in ('SPX','XSP'):
        rows=(raw.get('symbols') or {}).get(symbol,{}).get('contracts',[])
        if not isinstance(rows,list):rows=[]
        fresh=[];shorts={k:[] for k in PROFILES}
        for r in rows:
            q=r.get('quote',{}) if isinstance(r,dict) else {}
            try:
                if (r.get('state')!='active' or r.get('tradability')!='tradable' or
                    r.get('settle_on_open') is not False or
                    r.get('metadata_provenance',{}).get('source')!='CBOE_PRODUCT_SPECIFICATION' or
                    not recent(q.get('updated_at'),now) or
                    Decimal(str(q['ask_price']))<=0 or Decimal(str(q['bid_price']))<0 or
                    Decimal(str(q['ask_size']))<1 or Decimal(str(q['bid_size']))<1):continue
                strike=Decimal(str(r['strike_price']));right=r.get('type');delta=abs(Decimal(str(q['delta'])))
                if right not in ('put','call'):continue
            except (KeyError,ValueError,TypeError,ArithmeticError):continue
            fresh.append((right,strike,r.get('expiration_date'),r['id']))
            for name,(lo,hi,_) in PROFILES.items():
                if lo<=delta<=hi:shorts[name].append((right,strike,r.get('expiration_date')))
        available={(r,strike,expiry) for r,strike,expiry,_ in fresh}
        matching_wings={}
        for name,ss in shorts.items():
            pairs=[]
            for side,strike,expiry in ss:
                offsets=[Decimal(x) for x in WING_STRIKES[symbol]]
                for off in offsets:
                    other=strike-off if side=='put' else strike+off
                    if (side,other,expiry) in available:pairs.append((side,str(strike),str(other)))
            matching_wings[name]=len(pairs)
        puts=len({strike for side,strike,_,_ in fresh if side=='put'})
        result[symbol]={'received_contracts':len(rows),'fresh_executable_contracts':len(fresh),
          'unique_put_strikes':puts,'shorts_in_delta_bands':{n:len(v) for n,v in shorts.items()},
          'potential_wing_pairs':matching_wings,
          'coverage_target_met':len(fresh)>=MIN_QUOTED_CONTRACTS and puts>=MIN_UNIQUE_PUT_STRIKES,
          'reason':'INSUFFICIENT_WING_AND_STRIKE_COVERAGE' if len(fresh)<MIN_QUOTED_CONTRACTS or puts<MIN_UNIQUE_PUT_STRIKES else 'COVERAGE_TARGET_MET'}
    return {'observed_at_utc':iso(now),'symbols':result,'paper_only':True,'broker_submissions':0,
            'notice':'Coverage is not a trade approval. Use the unmodified strategy selector for economics.'}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--input',type=Path)
    p.add_argument('--output',type=Path)
    args=p.parse_args()
    data=audit(load(args.input,{})) if args.input else request_plan()
    if args.output:atomic(args.output,data)
    print(json.dumps(data,indent=2))
if __name__=='__main__':main()
