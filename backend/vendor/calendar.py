from functools import lru_cache
import pandas as pd
import exchange_calendars

@lru_cache
def calendar():
    return exchange_calendars.get_calendar('XNYS', side='left')

def sessions(start, end):
    return calendar().sessions_in_range(str(start)[:10], str(end)[:10])

def next_session(day):
    return str(calendar().session_offset(pd.Timestamp(day), 1).date())

def latest_eligible(timestamp, delay=901):
    t = pd.Timestamp(timestamp)
    if t.tzinfo is None:
        raise ValueError('TIMEZONE_REQUIRED')
    local = t.tz_convert('America/New_York')
    days = sessions(local.date()-pd.Timedelta(days=14), local.date())
    valid = [d for d in days if calendar().session_close(d)+pd.Timedelta(seconds=delay) <= t]
    return str(valid[-1].date())

def cadence(day, frequency):
    d = pd.Timestamp(day)
    previous = calendar().session_offset(d, -1)
    if frequency == 'DAILY':
        return True
    if frequency == 'WEEKLY_FIRST':
        return d.isocalendar()[:2] != previous.isocalendar()[:2]
    if frequency == 'MONTHLY':
        return pd.Timestamp(next_session(day)).month != d.month
    raise ValueError('UNKNOWN_CADENCE')
