"""Bounded read-only market data; reuse existing transports and validation contracts."""
import os
import json
import re
import html
import csv
import threading
import io
import hashlib
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from types import FunctionType
from urllib.parse import quote_plus
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET
import requests
from dotenv import dotenv_values
from .config import now, SYMBOLS
from .store import digest
from .vendor.calendar import calendar, latest_eligible
from .vendor import alpaca_contract as copied

NY = ZoneInfo('America/New_York')

def session_state(at=None):
    at = at or datetime.now(timezone.utc)
    day = latest_eligible(at.isoformat())
    cal = calendar()
    nxt = cal.session_offset(day, 1)
    return {'latest':day, 'nextClose':cal.session_close(nxt).isoformat(),
        'status':'Waiting for next completed session', 'checkedAt':at.isoformat()}

def credentials():
    # Preserve the existing explicit Alpaca process-variable contract.
    return os.environ.get('APCA_API_KEY_ID'), os.environ.get('APCA_API_SECRET_KEY')

def alpaca_fetch(params):
    """One shared data-only transport for completed-session daily context."""
    key,secret=credentials()
    if not key or not secret:
        raise ValueError('ALPACA_ENVIRONMENT_CREDENTIALS_MISSING')
    if params.get('feed') not in ('iex','sip') or params.get('timeframe') != '1Day':
        raise ValueError('ALPACA_REQUEST_SCOPE_INVALID')
    if not set(params.get('symbols','').split(','))<=set(SYMBOLS):
        raise ValueError('SYMBOL_NOT_ALLOWED')
    try:
        r=requests.get('https://data.alpaca.markets/v2/stocks/bars',params=params,
            headers={'APCA-API-KEY-ID':key,'APCA-API-SECRET-KEY':secret},timeout=(8,30),allow_redirects=False)
        if r.status_code!=200:
            raise ValueError('ALPACA_HTTP_'+str(r.status_code))
        return r.json()
    except requests.RequestException:
        raise ValueError('ALPACA_TRANSPORT_FAILURE') from None

def massive_key():
    key = os.environ.get('MASSIVE_API_KEY')
    from .config import developer_path
    path = developer_path('MARKET_DATA_ENV_FILE', 'C:/Users/antho/trading-agent-lab/.env')
    return key or (dotenv_values(path).get('MASSIVE_API_KEY') if path and path.is_file() else None)

_FILE_CACHE={}
_FILE_CACHE_LOCK=threading.Lock()

def _cached(path, kind, build):
    """Parse a read-only file once per (size, mtime) instead of on every investigation. The full
    preserved download is ~150k rows / 16 MB; re-reading it per symbol per case was the slow part."""
    stat=path.stat()
    key=(str(path),kind)
    with _FILE_CACHE_LOCK:
        hit=_FILE_CACHE.get(key)
        if hit and hit[0]==(stat.st_size,stat.st_mtime_ns):
            return hit[1]
    value=build(path)
    with _FILE_CACHE_LOCK:
        _FILE_CACHE[key]=((stat.st_size,stat.st_mtime_ns),value)
    return value

def _parse_json(path):
    return json.loads(path.read_text())

def _group_bars(path):
    raw=path.read_bytes()
    grouped={}
    for r in csv.DictReader(io.StringIO(raw.decode('utf-8'),newline='')):
        grouped.setdefault(r['ticker'],[]).append(r)
    # Only the recent tail is ever used (the manifest's latest session is the requested one), so
    # keep 30 rows per symbol rather than ~150k parsed rows in memory.
    return {k:v[-30:] for k,v in grouped.items()},hashlib.sha256(raw).hexdigest()

def _cached_json(path):
    return _cached(path,'json',_parse_json)

def _bars_by_symbol(path):
    return _cached(path,'bars',lambda p:_group_bars(p))

def preserved_bars(symbol, end):
    """Read authorized, already-downloaded Alpaca data without touching the source project."""
    from .config import developer_path
    root=developer_path('MARKETBELL_ALPACA_CACHE','C:/Users/antho/adaptive-universe-lab/data/iex')
    if not root or not root.is_dir():
        raise ValueError('PRESERVED_MARKET_DATA_UNAVAILABLE')
    candidates=[]
    for path in root.glob('*/manifest.json'):
        try:
            manifest=_cached_json(path)
            if manifest.get('source')=='ALPACA_IEX' and manifest.get('latest')==end and manifest.get('price_freshness_pass') is True and symbol in manifest.get('symbols',[]):
                candidates.append((manifest['retrieved_at'],path,manifest))
        except (ValueError,KeyError,OSError):
            continue
    if not candidates:
        raise ValueError('PRESERVED_MARKET_DATA_STALE')
    _,path,manifest=max(candidates,key=lambda c:c[0])
    file=path.parent/'bars.csv'
    source=[r for r in _bars_by_symbol(file)[0].get(symbol,[]) if r['date']<=end][-7:]
    expected=set(copied.sessions(source[0]['date'],end)) if source else set()
    rows=[]
    for r in source:
        raw={'t':datetime.fromisoformat(r['date']).replace(tzinfo=NY).isoformat(),
            **{k:float(r[v]) for k,v in [('o','open'),('h','high'),('l','low'),('c','close'),('v','volume')]},
            'n':int(float(r['trade_count'])) if r.get('trade_count') else None,'vw':float(r['vwap']) if r.get('vwap') else None}
        rows.append(copied.parse_bar(symbol,raw,expected,manifest['retrieved_at']))
    if len(rows)<2 or rows[-1]['session_date']!=end:
        raise ValueError('PRESERVED_MARKET_DATA_INCOMPLETE')
    return {'source':'Alpaca IEX · preserved authorized download','retrieved_at':manifest['retrieved_at'],'adjustment':manifest.get('adjustment') or 'unknown',
        'imported_at':now(),'session':end,'bars':rows,'page_hashes':manifest.get('page_hashes',[]),
        'provenance':{'manifest_sha256':digest(manifest),'file_sha256':_bars_by_symbol(file)[1],
            'source_download':path.parent.name,'acquisition':'read-only import; not a fresh Alpaca API request'}}

def market_bars(symbol, end):
    if symbol not in SYMBOLS:
        raise ValueError('SYMBOL_NOT_ALLOWED')
    start = (datetime.fromisoformat(end) - timedelta(days=10)).date().isoformat()
    stamp = now()
    key, secret = credentials()
    if not key or not secret:
        try:
            return preserved_bars(symbol,end)
        except ValueError:
            pass
    if key and secret:
        def fetch(params):
            return alpaca_fetch(params)
        # Same per-invocation symbol scope used by acquisition.download_symbols; no globals mutated.
        scope = {**copied.__dict__, 'MANDATE':(symbol,)}
        for name in ('safe_bar_diagnostic', 'parse_bar', 'download'):
            old = getattr(copied, name)
            fn = FunctionType(old.__code__, scope, name, old.__defaults__)
            fn.__kwdefaults__ = old.__kwdefaults__
            scope[name] = fn
        rows, hashes, _ = scope['download'](fetch, start, end, stamp, feed='iex')
        source = 'Alpaca IEX'
        adjustment = 'split'
    else:
        # Adapt trading-agent-lab/src/market_download.py transport, bounded to daily bars.
        key = massive_key()
        if not key:
            raise ValueError('MARKET_DATA_CREDENTIALS_UNAVAILABLE')
        try:
            url = f'https://api.massive.com/v2/aggs/ticker/{symbol}/range/1/day/{start}/{end}'
            r = requests.get(url,params={'adjusted':'true','sort':'asc','limit':100,'apiKey':key},timeout=(10,30),allow_redirects=False)
            if r.status_code != 200:
                raise ValueError('MASSIVE_HTTP_'+str(r.status_code))
            raw = r.json()
            if raw.get('status') not in ('OK','DELAYED'):
                raise ValueError('MASSIVE_RESPONSE_INVALID')
            rows=[]
            for b in raw.get('results',[]):
                t = datetime.fromtimestamp(b['t']/1000, timezone.utc).isoformat()
                row = copied.parse_bar(symbol, {**b,'t':t},set(copied.sessions(start,end)),stamp,feed='iex')
                row['source']='MASSIVE_ADJUSTED_DAILY'
                rows.append(row)
            hashes=[digest(raw)]
            source='Massive adjusted daily'
            adjustment='split'  # Massive/Polygon 'adjusted=true' is documented as split adjustment.
        except requests.RequestException:
            raise ValueError('MASSIVE_TRANSPORT_FAILURE') from None
    rows.sort(key=lambda r:r['session_date'])
    if len(rows)<2 or rows[-1]['session_date']!=end or len({r['session_date'] for r in rows})!=len(rows):
        raise ValueError('MARKET_DATA_STALE_OR_INCOMPLETE')
    return {'source':source,'retrieved_at':stamp,'session':end,'bars':rows,'page_hashes':hashes,'adjustment':adjustment}

def sanitize(text, limit=1800):
    # Adapted from research_web.sanitize: source text remains untrusted evidence.
    text = html.unescape(str(text or '')).translate(str.maketrans({'\u2018':"'",'\u2019':"'",'\u201c':'"','\u201d':'"','\u2013':'-','\u2014':'-','\u00a0':' '}))
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>',' ',text,flags=re.S|re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    return re.sub(r'\s+',' ',re.sub(r'[\x00-\x1f\x7f]',' ',text)).strip()[:limit]

# Curated identity aliases. 'strong' names identify the company on their own; 'weak' names are also
# ordinary words (apple, amazon, visa, meta, lilly) and count only with market context or the ticker.
# First words of multi-word names ("Bank", "Advanced", "Duke", "Walt", "Eli") are never aliases.
ALIASES={
 'PSX':(['Phillips 66'],[]),'VLO':(['Valero'],[]),'NVDA':(['Nvidia'],[]),'JPM':(['JPMorgan','JP Morgan'],[]),
 'AAPL':([],['Apple']),'MSFT':(['Microsoft'],[]),'GOOGL':(['Alphabet','Google'],[]),'AMZN':([],['Amazon']),
 'META':(['Meta Platforms'],['Meta']),'TSLA':(['Tesla'],[]),'XOM':(['Exxon','ExxonMobil','Exxon Mobil'],[]),'CVX':(['Chevron'],[]),
 'COP':(['ConocoPhillips'],[]),'BAC':(['Bank of America','BofA'],[]),'GS':(['Goldman Sachs'],['Goldman']),'V':(['Visa Inc'],['Visa']),
 'JNJ':(['Johnson & Johnson','J&J'],[]),'UNH':(['UnitedHealth'],[]),'LLY':(['Eli Lilly'],['Lilly']),'WMT':(['Walmart'],[]),
 'COST':(['Costco'],[]),'PG':(['Procter & Gamble','P&G'],[]),'CAT':(['Caterpillar'],[]),'GE':(['GE Aerospace','General Electric'],[]),
 'RTX':(['RTX','Raytheon'],[]),'NEE':(['NextEra'],[]),'DUK':(['Duke Energy'],[]),'PLD':(['Prologis'],[]),
 'AMD':(['Advanced Micro Devices','AMD'],[]),'AVGO':(['Broadcom'],[]),'NFLX':(['Netflix'],[]),'DIS':(['Disney'],[])}
MARKET_CONTEXT=re.compile(r'(?<![A-Za-z])(stock|stocks|shares|share price|inc\.?|corp\.?|earnings|revenue|guidance|quarter|analyst|ceo|investors?|nasdaq|nyse|dividend|buyback|results)(?![A-Za-z])',re.I)

def entity(symbol, headline):
    """Deterministic, explainable headline identity check; ambiguity fails closed."""
    strong,weak=ALIASES.get(symbol,([SYMBOLS.get(symbol,(symbol,))[0]],[]))
    def found(alias):
        return re.search(r'(?<![A-Za-z0-9])'+re.escape(alias)+r'(?![A-Za-z0-9])',headline,re.I)
    ticker=re.search(r'(\$'+re.escape(symbol)+r'\b|\('+re.escape(symbol)+r'\)|(?:NYSE|NASDAQ|Nasdaq)\s*:\s*'+re.escape(symbol)+r'\b)',headline)
    if any(found(a) for a in strong) or ticker:
        return {'match':True,'basis':'name_or_ticker'}
    if any(found(a) for a in weak):
        return {'match':bool(MARKET_CONTEXT.search(headline)),'basis':'common_word_with_market_context' if MARKET_CONTEXT.search(headline) else 'AMBIGUOUS_COMMON_WORD'}
    return {'match':False,'basis':'no_identity_match'}

def news_base():
    """Google News RSS. A loopback fault-injection feed is accepted only outside production-like envs."""
    from .config import PRODUCTION
    override=os.environ.get('MARKETBELL_TEST_NEWS_URL')
    if override and not PRODUCTION and override.startswith(('http://127.0.0.1','http://localhost')):
        return override
    return 'https://news.google.com/rss/search'

def news(symbol, query=None):
    if symbol not in SYMBOLS:
        raise ValueError('SYMBOL_NOT_ALLOWED')
    url = news_base()+'?q='+quote_plus(query or SYMBOLS[symbol][0]+' stock when:7d')+'&hl=en-US&gl=US&ceid=US:en'
    try:
        r = requests.get(url,timeout=(8,15),allow_redirects=False,headers={'User-Agent':'MarketBell/1.0 (bounded research)'})
        if r.status_code!=200 or len(r.content)>2_000_000:
            raise ValueError('NEWS_HTTP_OR_SIZE_FAILURE')
        # Untrusted XML: refuse DTDs/entities outright (RSS never needs them) before expat sees them.
        head=r.content[:4096].upper()
        if b'<!DOCTYPE' in head or b'<!ENTITY' in r.content.upper():
            raise ValueError('NEWS_XML_REJECTED')
        root=ET.fromstring(r.content)  # nosec B314 - size-capped, DTD/entity-free input checked above
        results=[]
        for item in root.findall('./channel/item')[:5]:
            title=sanitize(item.findtext('title'),500)
            publisher_node=item.find('source')
            publisher=sanitize(publisher_node.text,120) if publisher_node is not None and publisher_node.text else None
            publisher_site=publisher_node.get('url') if publisher_node is not None and str(publisher_node.get('url','')).startswith('https://') else None
            try:
                published=parsedate_to_datetime(item.findtext('pubDate')).isoformat()
                age=(datetime.now(timezone.utc)-datetime.fromisoformat(published)).total_seconds()
                if age < -300 or age>7*86400:continue
            except (ValueError,TypeError,AttributeError):continue
            link=item.findtext('link') or ''
            if not link.startswith('https://news.google.com/'):
                continue
            story=re.sub(r'[^a-z0-9]','',title.rsplit(' - ',1)[0].lower())
            identity=entity(symbol,title.rsplit(' - ',1)[0] if publisher else title)
            results.append({'entity_match':identity['match'],'entity_basis':identity['basis'],'entity_limitation':'Headline matching only; full article entity context is unavailable.','id':digest({'story':story}), 'source':link,
                # Google News is the discovery channel, never the publisher of someone else's article.
                'discovered_via':'GOOGLE_NEWS_RSS','discovery_url':link,'publisher':publisher,'publisher_site':publisher_site,
                'published_at':published,'retrieved_at':now(),'text':title,'type':'headline_only',
                'limitation':'Headline from RSS; full article not retrieved or independently verified.'})
            from .sources import classify
            classify(results[-1])
        return results
    except (requests.RequestException, ET.ParseError, ValueError, TypeError, AttributeError):
        raise ValueError('NEWS_SOURCE_UNAVAILABLE') from None

def evidence_items(items, limit=8):
    """Evidence a model may read: identity-matched, non-routine, strongest sources first. Headlines the
    entity check rejected (another company with a shared word) never enter a case's evidence."""
    usable=[i for i in items if i.get('entity_match') is not False and not i.get('routine')]
    usable.sort(key=lambda i:(i.get('source_tier',3),-_epoch(i.get('published_at'))))
    return usable[:limit]

def _epoch(value):
    try:return datetime.fromisoformat(str(value).replace('Z','+00:00')).timestamp()
    except ValueError:return 0.0

def acquire(symbol, session=None):
    from . import sources
    session = session or session_state()['latest']
    try:
        data = market_bars(symbol, session)
        sources.mark('price_baseline', True, symbol=symbol)
    except ValueError as error:
        # A missing price source is shown as a channel state, not discovered later as "blocked" cases.
        sources.mark('price_baseline', False, str(error), symbol)
        raise
    last, prior = data['bars'][-2:]
    change = round((prior['close']/last['close']-1)*100,3)
    # Available only after the actual exchange close (EST/EDT and early closes differ), never a fixed UTC hour.
    close=calendar().session_close(prior['session_date']).isoformat()
    evidence=[{'id':digest(data),'source':data['source'],'published_at':close,
        'retrieved_at':data['retrieved_at'],'type':'daily_market_data','text':json.dumps(data),'session':session}]
    try:
        # Same channels and filters as the watch: news, issuer press releases and SEC filings; no
        # per-ticker pages. (Hard-coded issuer pages for PSX/VLO were dated and went stale.)
        evidence += evidence_items(sources.gather(symbol, followed=True))
    except ValueError:
        pass  # Independently visible source monitoring reports news failures.
    return {'symbol':symbol,'session':session,'change':change,'evidence':evidence,
        'trigger':f'{symbol} completed-session move {change:+.2f}%; '+('material price move' if abs(change)>=2 else 'routine portfolio review'),
        'priority':abs(change), 'source':data['source']}

