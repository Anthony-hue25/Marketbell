"""Ask MarketBell: answers about what it read, did and did not do — from its own records.

Two modes, both cite records the person can open:
  * records — deterministic lookup, no model call (always available, also in the public demo);
  * model   — Nemotron writes the answer from the same records, every claim an exact quote.
Neither mode gives buy/sell advice; a model answer that drifts into a recommendation is refused.
"""
import re

from .config import SYMBOLS, now
from .store import digest

ASK_SCHEMA = {'answer': 'plain answer, at most 900 characters, no buy/sell/hold recommendation',
    'uncertainty': 'what the records cannot tell, at most 300 characters',
    'claims': [{'evidence_id': 'an input id', 'quote': 'exact substring from that record, 8 to 300 characters'}]}
ADVICE = re.compile(r"\b(you should|i (?:would )?recommend|i suggest|consider)\s+(?:buying|selling|purchasing|shorting|trimming|adding to|exiting)\b"
    r"|\b(strong )?(buy|sell) (rating|signal|recommendation)\b|\btime to (buy|sell)\b", re.I)


def mentioned(question):
    q = question.lower()
    found = []
    for symbol, (name, *_rest) in SYMBOLS.items():
        if re.search(r'(?<![A-Za-z])' + re.escape(symbol) + r'(?![A-Za-z])', question) or name.lower().split(' ')[0] in q and len(name.split(' ')[0]) > 3:
            found.append(symbol)
    return found


def records(store, db, tenant, question):
    """Bounded, citable records relevant to the question (newest first, at most 24)."""
    symbols = mentioned(question)
    portfolio = store.get(db, tenant, 'portfolio', 'main') or {'holdings': [], 'watchlist': []}
    mine = [h['symbol'] for h in portfolio['holdings']] + portfolio['watchlist']
    focus = set(symbols or mine)
    out = []
    events = store.all(db, tenant, 'event') + store.all(db, 'monitor-public', 'event')
    for e in sorted(events, key=lambda e: e.get('created', ''), reverse=True):
        if e.get('symbol') in focus and e.get('summary'):
            out.append({'id': 'case-' + e['id'][:12], 'symbol': e['symbol'], 'kind': 'case', 'at': e.get('resolved_at') or e.get('created'),
                'status': e.get('status'), 'text': f"{e['symbol']} case ({e.get('status')}): {e['summary']}"[:600]})
    for q in sorted(store.all(db, 'monitor-public', 'quiet'), key=lambda q: q['at'], reverse=True):
        if q['symbol'] in focus:
            out.append({'id': 'quiet-' + q['id'][:12], 'symbol': q['symbol'], 'kind': 'quiet', 'at': q['at'], 'reason': q['reason'],
                'text': f"\"{q['headline'][:200]}\" ({q.get('publisher') or 'unknown publisher'}). {q['plain']}"[:600]})
    return symbols, out[:24]


def from_records(symbols, items, question):
    """No-model answer: what happened, in MarketBell's own words, with the records it came from."""
    if not items:
        who = ', '.join(symbols) if symbols else 'your holdings and watchlist'
        return {'mode': 'records', 'answer': f'I have no cases or set-aside items for {who} in the last 14 days.',
            'uncertainty': 'An empty record means I found nothing that met my bar, not that nothing happened.', 'claims': []}
    cases = [i for i in items if i['kind'] == 'case']
    quiet = [i for i in items if i['kind'] == 'quiet']
    about = ', '.join(symbols) if symbols else 'your holdings and watchlist'
    parts = []
    if cases:
        parts.append(f"I have {len(cases)} case{'s' if len(cases) != 1 else ''} on {about}. Latest: {cases[0]['text']}")
    if quiet:
        reasons = {}
        for q in quiet:
            reasons[q['reason']] = reasons.get(q['reason'], 0) + 1
        why = ', '.join(f"{n} {r.lower().replace('_', ' ')}" for r, n in reasons.items())
        parts.append(f"I set aside {len(quiet)} item{'s' if len(quiet) != 1 else ''} on {about} ({why}). Most recent: {quiet[0]['text']}")
    return {'mode': 'records', 'answer': ' '.join(parts)[:900],
        'uncertainty': 'Full articles were not read.',
        'claims': [{'evidence_id': i['id'], 'quote': i['text'][:120]} for i in items[:3]]}


def validate(value, items):
    if type(value) is not dict or set(value) != set(ASK_SCHEMA):
        raise ValueError('ASK_SCHEMA_INVALID')
    if type(value['answer']) is not str or not 10 <= len(value['answer']) <= 1200 or type(value['uncertainty']) is not str or len(value['uncertainty']) > 400:
        raise ValueError('ASK_SCHEMA_INVALID')
    if ADVICE.search(value['answer']):
        raise ValueError('ASK_ADVICE_REFUSED')
    index = {i['id']: i['text'] for i in items}
    claims = value['claims']
    if type(claims) is not list or len(claims) > 6 or (items and not claims):
        raise ValueError('ASK_CITATIONS_REQUIRED')
    for c in claims:
        if type(c) is not dict or set(c) != {'evidence_id', 'quote'} or type(c['quote']) is not str or not 8 <= len(c['quote']) <= 300 or c['evidence_id'] not in index or c['quote'] not in index[c['evidence_id']]:
            raise ValueError('ASK_CITATION_NOT_GROUNDED')
    return value


def answer(store, tenant, question, ai=None, request_id=None):
    with store.connect() as db:
        symbols, items = records(store, db, tenant, question)
    result = from_records(symbols, items, question)
    if ai is not None and items:
        from .ai import bind_tenant
        try:
            with bind_tenant(tenant):
                trace = ai.call('ASK', {'question': question, 'records': [{'id': i['id'], 'text': i['text']} for i in items]},
                    lambda v: validate(v, items), context=digest({'ask': question, 'tenant': tenant}))
            result = {**trace['output'], 'mode': 'model', 'model': trace['model'], 'usage': trace.get('usage')}
        except ValueError as error:
            # Budget, provider or validation failure (including refused advice): the records answer stands.
            code = str(error) if re.fullmatch('[A-Z0-9_]+', str(error)) else 'MODEL_TECHNICAL_FAILURE'
            result['model_unavailable'] = code
    result.update(question=question, at=now(), symbols=symbols, sources=[{k: i.get(k) for k in ('id', 'symbol', 'kind', 'at', 'text')} for i in items[:8]])
    key = request_id or digest({'q': question, 'at': result['at']})[:24]
    with store.transaction() as db:
        store.put(db, tenant, 'answer', key, {**result, 'id': key})
        store.append(db, tenant, 'Question answered', f"{result['mode']} answer to a question about {', '.join(symbols) or 'the portfolio'}; {len(result['claims'])} citations.")
    return result
