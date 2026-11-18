"""Barney's separate, non-executing candidate comparison in the Sandbox.

Inputs must come from the sandbox's already-verified strategy evaluators;
this file never invents legs or treats a published CANDIDATE as a fill.
"""
from __future__ import annotations
from datetime import datetime,timezone
from decimal import Decimal
from typing import Mapping
from replay import iso

MATRIX={
 'BULLISH':('BULL_PUT_CREDIT','BULL_CALL_DEBIT','PUT_BROKEN_WING','LONG_CALL_OBSERVE_ONLY'),
 'BEARISH':('BEAR_CALL_CREDIT','BEAR_PUT_DEBIT','CALL_BROKEN_WING','LONG_PUT_OBSERVE_ONLY'),
 'NEUTRAL':('IRON_CONDOR','BROKEN_WING_WITH_VERIFIED_TARGET'),
}

def compare(direction:str, *,bwb:Mapping|None=None, debit:Mapping|None=None,
            credit:Mapping|None=None, now:datetime|None=None)->dict:
    now=now or datetime.now(timezone.utc)
    out={'strategy':'BARNEY_MATRIX_SANDBOX_V1','at':iso(now), 'direction':direction,
         'status':'RESEARCH_ONLY', 'paper_only':True, 'broker_submissions':0,
         'order_submission_enabled':False,'execution_status':'NO_PAPER_FILL',
         'choices':list(MATRIX.get(direction,())), 'candidates':[]}
    if direction not in MATRIX:
        out.update(status='DATA_UNAVAILABLE',reason='DIRECTION_UNCONFIRMED');return out
    for name,source in [('BROKEN_WING',bwb),('DEBIT_VERTICAL',debit),('CREDIT_VERTICAL',credit)]:
        if not isinstance(source,Mapping):
            out['candidates'].append({'family':name,'status':'DATA_UNAVAILABLE','reason':'VERIFIED_RESEARCH_NOT_SUPPLIED','paper_fill':False});continue
        status=str(source.get('status','DATA_UNAVAILABLE'))
        row={'family':name,'status':status,'reason':str(source.get('reason','UNKNOWN')),
             'candidate_reference':source.get('candidate_id'),
             'paper_fill':False}
        # These fields are quoted evidence, not authority to open a position.
        for k in ('maximum_loss_usd','maximum_profit_usd','net_debit_usd','purchase_amount_usd'):
            if k in source:
                try:
                    number=Decimal(str(source[k]));assert number.is_finite() and number>=0
                    row[k]=str(number)
                except (AssertionError,TypeError,ValueError,ArithmeticError):
                    row.update(status='DATA_UNAVAILABLE',reason='INVALID_RISK_EVIDENCE')
        out['candidates'].append(row)
    return out
