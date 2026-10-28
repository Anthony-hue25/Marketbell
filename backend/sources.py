"""Free, public information channels and publisher tiers.

Channels (all free, no account):
  * SEC EDGAR submissions API — the company's own regulatory filings. Needs only a contact User-Agent
    (``SEC_USER_AGENT``, e.g. "MarketBell research you@example.com"); skipped when unset.
  * Press-release wires (Business Wire, PR Newswire, GlobeNewswire) via Google News site search —
    the issuer's own announcements. Holdings and watchlist only, to bound requests.
  * Google News search — general coverage, every observed symbol.

Publisher tier decides how much a source may count for, never what the model concludes:
  Tier 1  issuer / regulator (SEC, press-release wires)       may support a decision on its own
  Tier 2  established newsrooms (Reuters, AP, Bloomberg, ...)  may support a decision on its own
  Tier 3  commentary, aggregators and unknown publishers       logged quietly; never investigated alone
"""
import copy, json, os, re, threading, time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, urlparse

import requests

from .config import SYMBOLS, now
from .store import digest

TIER1_DOMAINS = {'sec.gov', 'businesswire.com', 'prnewswire.com', 'globenewswire.com', 'accesswire.com'}
TIER2_DOMAINS = {'reuters.com', 'apnews.com', 'bloomberg.com', 'cnbc.com', 'wsj.com', 'ft.com', 'barrons.com',
    'marketwatch.com', 'nytimes.com', 'washingtonpost.com', 'economist.com', 'axios.com', 'bbc.com', 'bbc.co.uk',
    'theguardian.com', 'fortune.com', 'forbes.com', 'businessinsider.com', 'investors.com', 'finance.yahoo.com',
    'fiercepharma.com', 'statnews.com', 'spglobal.com', 'americanbanker.com', 'theinformation.com', 'techcrunch.com'}
TIER_LABEL = {1: 'Issuer or regulator', 2: 'Established newsroom', 3: 'Commentary or unknown publisher'}

# Form 8-K item descriptions (SEC Form 8-K General Instructions). Plain language, used in the evidence text.
ITEMS_8K = {'1.01': 'entry into a material agreement', '1.02': 'termination of a material agreement',
    '1.05': 'material cybersecurity incident', '2.01': 'completed acquisition or disposition',
    '2.02': 'results of operations and financial condition', '2.03': 'new material financial obligation',
    '2.05': 'exit or restructuring costs', '2.06': 'material impairment', '3.01': 'delisting or listing-standard notice',
    '4.01': 'change of auditor', '4.02': 'previously issued financials should no longer be relied on',
    '5.02': 'director or officer change', '5.03': 'bylaw or fiscal-year change', '5.07': 'shareholder vote results',
    '7.01': 'Regulation FD disclosure', '8.01': 'other material event', '9.01': 'financial statements and exhibits'}
MATERIAL_FORMS = {'8-K': 'current report', '10-Q': 'quarterly report', '10-K': 'annual report', '6-K': 'foreign issuer report',
    '20-F': 'foreign annual report', 'S-4': 'merger registration', 'SC 13D': 'activist ownership stake', '8-K/A': 'amended current report'}
ROUTINE_NAMES = {'4': 'insider transaction report', '3': 'initial insider ownership report', '5': 'annual insider report',
    '144': 'planned insider sale notice', 'S-8': 'employee share plan registration', '13F-HR': 'institutional holdings report',
    'SC 13G': 'passive ownership report', 'SC 13G/A': 'passive ownership report', '424B2': 'prospectus supplement', 'FWP': 'free-writing prospectus'}
ROUTINE_FORMS = {'4', '3', '5', '144', '424B2', '424B3', 'FWP', 'S-8', '11-K', 'SD', 'DEFA14A', '13F-HR', 'SC 13G', 'SC 13G/A'}
# Fallback when the ticker file is unavailable. Verified CIKs for the demo catalog.
CIK_FALLBACK = {'PSX': 1534701, 'VLO': 1035002, 'NVDA': 1045810, 'JPM': 19617, 'MSFT': 789019, 'XOM': 34088, 'COST': 909832,
    'LLY': 59478, 'AMD': 2488, 'GS': 886982, 'AAPL': 320193, 'GOOGL': 1652044, 'AMZN': 1018724, 'META': 1326801,
    'TSLA': 1318605, 'CVX': 93410, 'COP': 1163165, 'BAC': 70858, 'V': 1403161, 'JNJ': 200406, 'UNH': 731766,
    'WMT': 104169, 'PG': 80424, 'CAT': 18230, 'GE': 40545, 'RTX': 101829, 'NEE': 753308, 'DUK': 1326160,
    'PLD': 1045609, 'AVGO': 1730168, 'NFLX': 1065280, 'DIS': 1744489}

health = {}            # channel -> {'ok': bool, 'at': iso, 'error': code}; persisted by the worker each scan
_lock = threading.Lock()
_last = [0.0]
_ciks = {}


def domain(url):
    try:
        host = (urlparse(url).hostname or '').lower()
    except ValueError:
        return ''
    return host[4:] if host.startswith('www.') else host


def tier(item):
    """Tier from the publisher's site domain (supplied by the feed), never from its display name."""
    if item.get('type') == 'regulatory_filing':
        return 1
    host = domain(item.get('publisher_site') or '')
    if any(host == d or host.endswith('.' + d) for d in TIER1_DOMAINS):
        return 1
    if any(host == d or host.endswith('.' + d) for d in TIER2_DOMAINS):
        return 2
    return 3


def classify(item):
    t = tier(item)
    item['source_tier'] = t
    item['source_tier_label'] = TIER_LABEL[t]
    return item


def mark(channel, ok, error=None, symbol=None, disabled=False):
    """Per-channel health from the latest result for every symbol, so one success cannot hide
    another symbol's failure (last-write-wins would)."""
    entry = health.setdefault(channel, {'symbols': {}})
    entry['symbols'][symbol or '*'] = error or 'ok'
    failing = sorted(k for k, v in entry['symbols'].items() if v != 'ok')
    entry.update(ok=not failing, at=now(), failing=failing, disabled=disabled)
    if error:
        entry['error'] = error
    elif not failing:
        entry.pop('error', None)


def sec_status():
    """Explicit SEC channel state: a missing or malformed contact must be visible, never a silent gap."""
    raw = (os.environ.get('SEC_USER_AGENT') or '').strip()
    if not raw:
        return 'SEC_USER_AGENT_NOT_SET'
    return None if sec_agent() else 'SEC_USER_AGENT_INVALID'


_ttl = {}
_ttl_lock = threading.Lock()


def cached(channel, symbol, run):
    """Slow-changing channels (SEC filing index, press-release search) are fetched at most once per
    MARKETBELL_SLOW_CHANNEL_SECONDS (default 15 min) per symbol, not on every 5-minute scan and every
    investigation. Callers get a copy, so later annotation never leaks into the cache."""
    ttl = int(os.environ.get('MARKETBELL_SLOW_CHANNEL_SECONDS', '900'))
    key = (channel, symbol)
    with _ttl_lock:
        hit = _ttl.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return copy.deepcopy(hit[1])
    value = run()
    with _ttl_lock:
        _ttl[key] = (time.monotonic(), copy.deepcopy(value))
    return value


def sec_agent():
    agent = (os.environ.get('SEC_USER_AGENT') or '').strip()
    # SEC fair-access policy asks for a name and contact e-mail; refuse anything that is not one.
    return agent if re.fullmatch(r'[\w .,()\-]{3,80}\s+[^@\s]+@[^@\s]+\.[A-Za-z]{2,}', agent) else None


def sec_get(url):
    agent = sec_agent()
    if not agent:
        raise ValueError('SEC_USER_AGENT_NOT_SET')
    with _lock:  # SEC allows 10 requests/second; stay well under.
        wait = _last[0] + 0.17 - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()
    r = requests.get(url, timeout=(8, 15), allow_redirects=False, headers={'User-Agent': agent, 'Accept-Encoding': 'gzip, deflate'})
    if r.status_code != 200 or len(r.content) > 8_000_000:
        raise ValueError('SEC_HTTP_OR_SIZE_FAILURE')
    return r.json()


def cik(symbol):
    if symbol in _ciks:
        return _ciks[symbol]
    if not _ciks.get('_loaded'):
        _ciks['_loaded'] = True
        try:
            rows = sec_get('https://www.sec.gov/files/company_tickers.json')
            for row in rows.values():
                if row.get('ticker') in SYMBOLS:
                    _ciks[row['ticker']] = int(row['cik_str'])
        except (requests.RequestException, ValueError, TypeError, AttributeError, KeyError):
            pass
    return _ciks.get(symbol) or CIK_FALLBACK.get(symbol)


def filing_text(name, form, items, description):
    kind = MATERIAL_FORMS.get(form) or ROUTINE_NAMES.get(form, 'filing')
    text = f'{name} filed a Form {form} ({kind}) with the U.S. SEC'
    codes = [c.strip() for c in (items or '').split(',') if c.strip()]
    if codes:
        text += ': ' + '; '.join(f'Item {c} ({ITEMS_8K.get(c, "other item")})' for c in codes)
    elif description and description.upper() != form:
        text += ': ' + re.sub(r'\s+', ' ', description)[:120]
    return text + '.'


def edgar(symbol, days=7, fetch=None):
    """Recent filings for one company. Identity is checked against the filer record; mismatch fails closed."""
    number = cik(symbol)
    if not number:
        raise ValueError('SEC_CIK_UNKNOWN')
    data = (fetch or sec_get)(f'https://data.sec.gov/submissions/CIK{number:010d}.json')
    if symbol not in (data.get('tickers') or []):
        raise ValueError('SEC_IDENTITY_MISMATCH')
    recent = data.get('filings', {}).get('recent', {})
    name = SYMBOLS[symbol][0]
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for i, form in enumerate(recent.get('form', [])[:60]):
        try:
            accepted = datetime.fromisoformat(recent['acceptanceDateTime'][i].replace('Z', '+00:00'))
        except (ValueError, KeyError, IndexError, AttributeError):
            continue
        if accepted.tzinfo is None:
            accepted = accepted.replace(tzinfo=timezone.utc)
        if accepted < cutoff or accepted > datetime.now(timezone.utc) + timedelta(minutes=5):
            continue
        accession = recent['accessionNumber'][i]
        document = (recent.get('primaryDocument') or [''] * (i + 1))[i]
        items = (recent.get('items') or [''] * (i + 1))[i]
        url = f'https://www.sec.gov/Archives/edgar/data/{number}/{accession.replace("-", "")}/{document}'
        out.append(classify({'entity_match': True, 'entity_basis': 'sec_filer_record', 'entity_limitation': 'Filer identity from the SEC record (CIK and ticker).',
            'id': digest({'sec': accession}), 'source': url, 'discovered_via': 'SEC_EDGAR', 'discovery_url': url,
            'publisher': 'U.S. SEC (EDGAR)', 'publisher_site': 'https://www.sec.gov', 'published_at': accepted.isoformat(),
            'retrieved_at': now(), 'type': 'regulatory_filing', 'form': form, 'items': items,
            'routine': form in ROUTINE_FORMS or form not in MATERIAL_FORMS,
            'text': filing_text(name, form, items, (recent.get('primaryDocDescription') or [''] * (i + 1))[i]),
            'limitation': 'Filing index metadata from SEC EDGAR; the filing document itself was not read.'}))
    return out


def wires(symbol, fetch=None):
    from .market import news
    # 30 days: issuer announcements (e.g. an earnings date) stay relevant for weeks. Items older than
    # 7 days are background evidence only; they never start an investigation on their own.
    query = '"' + SYMBOLS[symbol][0] + '" (site:businesswire.com OR site:prnewswire.com OR site:globenewswire.com) when:30d'
    items = (fetch or news)(symbol, query=query)
    for item in items:
        item['discovered_via'] = 'GOOGLE_NEWS_WIRE_SEARCH'
    return items


def gather(symbol, followed=False, news_fetch=None, sec_fetch=None):
    """All channels for one symbol. One failing channel never hides the others; all failing raises."""
    from .market import news
    channels = [('google_news', lambda: (news_fetch or news)(symbol))]
    if followed:
        channels.append(('press_wires', (lambda: wires(symbol, news_fetch)) if news_fetch else (lambda: cached('press_wires', symbol, lambda: wires(symbol)))))
        if sec_fetch:
            channels.append(('sec_edgar', lambda: edgar(symbol, fetch=sec_fetch)))
        elif sec_status():
            mark('sec_edgar', False, sec_status(), symbol, disabled=True)  # off, and visibly so
        else:
            channels.append(('sec_edgar', lambda: cached('sec_edgar', symbol, lambda: edgar(symbol))))
    found, failures = [], 0
    for channel, run in channels:
        try:
            found += [classify(item) for item in run()]
            mark(channel, True, symbol=symbol)
        except Exception as error:  # Each channel's failure is shown separately; others still count.
            code = str(error) if re.fullmatch('[A-Z0-9_]+', str(error)) else 'SOURCE_UNAVAILABLE'
            mark(channel, False, code, symbol)
            failures += 1
    if failures == len(channels):
        raise ValueError('ALL_SOURCES_UNAVAILABLE')
    # The same story from a wire and from a blog is kept once, as the strongest source's version;
    # the weaker copies are reported as duplicates so the quiet log can say so.
    items, seen = [], {}
    for item in sorted(found, key=lambda i: i['source_tier']):
        if item['id'] in seen:
            seen[item['id']].setdefault('also_reported_by', []).append(item.get('publisher') or 'unknown publisher')
            continue
        seen[item['id']] = item
        items.append(item)
    return items
