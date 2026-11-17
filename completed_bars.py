"""Separate exchange-bar completion from executable quote freshness."""
from datetime import datetime,time,timedelta
from zoneinfo import ZoneInfo

ET=ZoneInfo('America/New_York')
BAR=timedelta(minutes=5)

def bar_close_reason(value,now,publication_grace_seconds=60):
    try:
        at=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if at.tzinfo is None or now.tzinfo is None:return 'BAR_TIMEZONE_REQUIRED'
        local=at.astimezone(ET);observed=now.astimezone(ET)
        if at>now:return 'BAR_NOT_COMPLETED'
        if local.date()!=observed.date():return 'BAR_NOT_CURRENT_SESSION'
        if local.second or local.microsecond or local.minute%5:return 'BAR_NOT_ON_FIVE_MINUTE_GRID'
        if not time(9,35)<=local.time().replace(tzinfo=None)<=time(16):return 'BAR_OUTSIDE_REGULAR_SESSION'
        expected=observed.replace(minute=observed.minute//5*5,second=0,microsecond=0)
        expected=min(expected,observed.replace(hour=16,minute=0,second=0,microsecond=0))
        if at==expected:return None
        if at==expected-BAR and (now-expected).total_seconds()<=publication_grace_seconds:return None
        return 'LATEST_COMPLETED_BAR_MISSING_OR_STALE'
    except (TypeError,ValueError,AttributeError):return 'BAR_TIMESTAMP_INVALID'
