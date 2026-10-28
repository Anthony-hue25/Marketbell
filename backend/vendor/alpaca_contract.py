"""Copied parser/downloader with isolated symbols and explicit no-activity VWAP handling."""
import hashlib
import json
import math
import re
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import pandas as pd
from .calendar import calendar
NY=ZoneInfo('America/New_York')
SIP_DELAY=timedelta(minutes=15,seconds=1)
MANDATE=()
FIELDS=('symbol','session_date','open','high','low','close','volume','trade_count','vwap','source','retrieved_at')
class HistoryError(ValueError):
    def __init__(self, code, diagnostic=None):
        super().__init__(code)
        self.diagnostic = diagnostic

def safe_bar_diagnostic(symbol, raw, field, reason, session=None):
    """Allowlisted numeric values only; never echo arbitrary remote strings."""
    names = {'o':'open','h':'high','l':'low','c':'close','v':'volume',
             'n':'trade_count','vw':'vwap'}
    values = {}
    for key, name in names.items():
        if type(raw) is not dict or key not in raw:
            values[name] = 'MISSING'
            continue
        value = raw[key]
        if value is None or type(value) is bool:
            values[name] = value
        elif type(value) in (int,float):
            try:
                values[name] = value if math.isfinite(value) and abs(value) < 1e100 else 'NONFINITE_OR_OUT_OF_DISPLAY_RANGE'
            except OverflowError:
                values[name] = 'OUT_OF_DISPLAY_RANGE'
        else:
            values[name] = 'REDACTED_NONNUMERIC_VALUE'
    stamp = raw.get('t') if type(raw) is dict else None
    safe_stamp = stamp if type(stamp) is str and len(stamp)<=64 and re.fullmatch(r'[0-9TtZz:+. \-]+',stamp) else 'MISSING_OR_REDACTED_TIMESTAMP'
    return {'symbol':symbol if symbol in MANDATE else 'UNKNOWN_SYMBOL',
            'timestamp':safe_stamp,'session':session,'failing_field':field,
            'failure_reason':reason,'values':values}

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def sessions(start, end):
    if start > end:
        return []
    return [str(d.date()) for d in calendar().sessions_in_range(start, end)]

def latest_completed(now):
    stamp = pd.Timestamp(now)
    if stamp.tzinfo is None:
        raise HistoryError('TIMEZONE_REQUIRED')
    day = stamp.tz_convert(NY).date()
    candidates = calendar().sessions_in_range(day-timedelta(days=15), day)
    for session in reversed(candidates):
        if calendar().session_close(session) <= stamp:
            return str(session.date())
    raise HistoryError('NO_COMPLETED_SESSION')

def feed_source(feed):
    if feed not in ('iex','sip'):
        raise HistoryError('UNSUPPORTED_DAILY_FEED')
    return 'ALPACA_'+feed.upper()

def numeric(value, *, positive=False):
    if type(value) not in (int,float) or not math.isfinite(value) or value < 0 or (positive and value <= 0):
        raise HistoryError('INVALID_DAILY_BAR')
    return value

def parse_bar(symbol, raw, expected, retrieved, *, feed='iex'):
    day = None
    def reject(field, reason):
        raise HistoryError('INVALID_DAILY_BAR', safe_bar_diagnostic(symbol,raw,field,reason,day))
    if type(raw) is not dict:
        reject('bar','BAR_OBJECT_REQUIRED')
    if 't' not in raw:
        reject('timestamp','REQUIRED_FIELD_MISSING')
    try:
        t = datetime.fromisoformat(raw['t'].replace('Z','+00:00'))
    except (ValueError,TypeError,AttributeError):
        reject('timestamp','TIMESTAMP_PARSE_FAILED')
    if t.tzinfo is None:
        reject('timestamp','TIMEZONE_REQUIRED')
    try:
        local = t.astimezone(NY)
        day = local.date().isoformat()
    except (ValueError,TypeError,AttributeError):
        reject('timestamp','TIMEZONE_CONVERSION_FAILED')
    if day not in expected:
        reject('session','SESSION_OUTSIDE_REQUESTED_COMPLETED_NYSE_SESSIONS')
    if (local.hour,local.minute,local.second,local.microsecond) != (0,0,0,0):
        reject('timestamp','NOT_NEW_YORK_MIDNIGHT')
    names={'o':'open','h':'high','l':'low','c':'close','v':'volume','vw':'vwap'}
    def number(key, positive=False):
        if key not in raw:
            reject(names[key],'REQUIRED_FIELD_MISSING')
        value=raw[key]
        if type(value) not in (int,float):
            reject(names[key],'NUMERIC_INT_OR_FLOAT_REQUIRED')
        if not math.isfinite(value):
            reject(names[key],'NONFINITE_NUMBER')
        if value<0:
            reject(names[key],'NEGATIVE_NUMBER')
        if positive and value==0:
            reject(names[key],'STRICTLY_POSITIVE_REQUIRED')
        return value
    o,h,l,c=[number(k,positive=True) for k in ('o','h','l','c')]
    v=number('v')
    if l>min(o,c):
        reject('low','LOW_ABOVE_OPEN_OR_CLOSE')
    if h<max(o,c):
        reject('high','HIGH_BELOW_OPEN_OR_CLOSE')
    # The former low > high clause is implied by the two comparisons above.
    n=raw.get('n')
    if n is not None:
        if type(n) is not int:
            reject('trade_count','INTEGER_REQUIRED')
        if n<0:
            reject('trade_count','NEGATIVE_NUMBER')
    vw=raw.get('vw')
    if vw is not None:
        # Preserve the provider's zero sentinel only for explicit zero activity.
        # It is not an executable price or evidence of a traded session.
        number('vw',positive=not (v == 0 and n == 0))
    return dict(zip(FIELDS,(symbol,day,o,h,l,c,v,n,vw,feed_source(feed),retrieved)))

def download(fetch, start, end, retrieved, *, feed='iex'):
    feed_source(feed)
    expected = set(sessions(start,end))
    request_end=(datetime.fromisoformat(end)+timedelta(days=1)).replace(tzinfo=NY)
    if feed=='sip':
        available_at=datetime.fromisoformat(retrieved)-SIP_DELAY
        if available_at.tzinfo is None or end>latest_completed(available_at):
            raise HistoryError('SIP_HISTORICAL_DELAY_NOT_SATISFIED')
        # Alpaca requires explicit end >=15 minutes old without real-time SIP.
        request_end=min(request_end,available_at)
    params = {'symbols':','.join(MANDATE),'timeframe':'1Day','feed':feed,'adjustment':'split',
              'start':datetime.fromisoformat(start).replace(tzinfo=NY).isoformat(),
              'end':request_end.isoformat(),
              'sort':'asc','limit':10000}
    rows, hashes, tokens, seen = [], [], set(), set()
    for _ in range(500):
        page = fetch(dict(params))
        if type(page) is not dict or type(page.get('bars')) is not dict or 'next_page_token' not in page:
            raise HistoryError('INVALID_HISTORY_RESPONSE')
        for symbol, bars in page['bars'].items():
            if symbol not in MANDATE or type(bars) is not list:
                raise HistoryError('UNEXPECTED_HISTORY_SYMBOL_OR_SHAPE')
            for raw in bars:
                row = parse_bar(symbol,raw,expected,retrieved,feed=feed)
                key = (symbol,row['session_date'])
                if key in seen:
                    raise HistoryError('DUPLICATE_SYMBOL_SESSION')
                seen.add(key);rows.append(row)
        hashes.append(sha(json.dumps(page,sort_keys=True,separators=(',',':'),allow_nan=False).encode()))
        token = page['next_page_token']
        if token is None or token == '':
            return sorted(rows,key=lambda r:(r['session_date'],r['symbol'])), hashes, params
        if type(token) is not str or len(token)>8192 or token in tokens:
            raise HistoryError('INVALID_HISTORY_PAGINATION')
        tokens.add(token);params['page_token']=token
    raise HistoryError('HISTORY_PAGE_LIMIT')
