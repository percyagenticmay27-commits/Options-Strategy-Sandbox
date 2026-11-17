"""Sniper's hard 0/1-DTE and same-session holding policy."""
from datetime import datetime,time,timedelta
from replay import CT,timestamp

EXIT_TIME_CT=time(14,20)

def deadline(now,session):
    local=now.astimezone(CT)
    if (session.get('source')!='ALPACA_CALENDAR' or session.get('is_trading_day') is not True
        or session.get('date')!=local.date().isoformat()):
        raise ValueError('VERIFIED_CURRENT_SESSION_REQUIRED')
    opened=timestamp(session['open']);closed=timestamp(session['close'])
    if not opened<=now<closed:raise ValueError('OUTSIDE_VERIFIED_MARKET_SESSION')
    return min(datetime.combine(local.date(),EXIT_TIME_CT,CT),closed-timedelta(minutes=10))

def forced_exit_reason(position,now):
    opened=timestamp(position['opened_at']).astimezone(CT)
    expiry=datetime.fromisoformat(position['expiration']).date()
    if not 0<=(expiry-opened.date()).days<=1:
        return 'DTE_POLICY_MIGRATION_EXIT'
    if opened.date()<now.astimezone(CT).date():return 'OVERNIGHT_POSITION_EXIT_REQUIRED'
    cutoff=timestamp(position['mandatory_exit_at']) if position.get('mandatory_exit_at') else datetime.combine(opened.date(),EXIT_TIME_CT,CT)
    return 'INTRADAY_TIME_EXIT' if now>=cutoff else None
