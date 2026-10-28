"""Bounded authentic live check (run on a machine with internet access and a Nebius key).

    .venv\\Scripts\\python -B -m scripts.live_bounded_check

1. Fetches real Google News RSS for the four core securities (no inference).
2. Runs ONE real Nemotron Research + blind Challenge on the freshest entity-matched real headline,
   through the normal engine, schema/citation validation and deterministic policy.
3. Persists the result in an isolated SQLite workspace, re-opens it (restart) and verifies the
   journal, then renders it through the same /api/state code the browser uses.
4. Writes docs/evidence/LIVE_BOUNDED_CHECK.json. No key material is read into the report.

Spend ceiling (enforced, not advisory): the limits below are FORCED for this process before any
MarketBell module loads, so neither the shell environment nor .env can raise them. Every model call
reserves a conservative cost before it is sent (input bytes treated as tokens + the full 4,096
max_tokens output at the configured rates) and the call is refused once the reservations would exceed
$0.05. The isolated workspace starts with zero spend. Model attempts are capped at 4.
"""
import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CEILING_USD = '0.05'
FORCED = {
    'MARKETBELL_DAILY_USD': CEILING_USD, 'MARKETBELL_MONTHLY_USD': CEILING_USD,
    'MARKETBELL_DAILY_CALLS': '4', 'MARKETBELL_EVENT_CALLS': '4', 'MARKETBELL_INTERACTIVE_DAILY_CALLS': '0',
    # Never below the published Nemotron 3 Super rates, so reservations cannot under-estimate.
    'MARKETBELL_INPUT_USD_M': str(max(0.30, float(os.environ.get('MARKETBELL_INPUT_USD_M', '0.30') or 0.30))),
    'MARKETBELL_OUTPUT_USD_M': str(max(0.90, float(os.environ.get('MARKETBELL_OUTPUT_USD_M', '0.90') or 0.90))),
    'MARKETBELL_ALLOW_LIVE': '1', 'MARKETBELL_ENV': 'development', 'MARKETBELL_SKIP_ROUTINE_CHALLENGE': '0',
    # Public headline only: keep the exact requests in the isolated workspace to prove Challenge blindness.
    'MARKETBELL_CAPTURE_PUBLIC_REQUESTS': '1',
}
os.environ.update(FORCED)
for name in ('MARKETBELL_DATABASE_URL', 'MARKETBELL_TEST_DATABASE_URL', 'MARKETBELL_TEST_PROVIDER_URL', 'MARKETBELL_TEST_NEWS_URL'):
    if name in os.environ and '--allow-test-endpoints' not in sys.argv:
        os.environ.pop(name)  # authentic run: never a test endpoint or another database
RUN = 'live-bounded-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + secrets.token_hex(2)
ROOT = Path(__file__).resolve().parents[1]
folder = ROOT / 'work' / 'audit' / RUN
os.environ['MARKETBELL_DATA_DIR'] = str(folder)  # before backend.config is imported
from backend.store import Store  # noqa: E402
from backend.engine import Engine  # noqa: E402
from backend.market import news_base  # noqa: E402
from backend import sources  # noqa: E402
from backend.vendor.provider import endpoint  # noqa: E402


def main():
    authentic = news_base().startswith('https://news.google.com/') and endpoint().startswith('https://api.tokenfactory.')
    report = {'at': datetime.now(timezone.utc).isoformat(), 'run': RUN, 'authentic_endpoints': authentic,
              'news_endpoint': news_base(), 'model_endpoint': endpoint(), 'enforced_limits': FORCED,
              'workspace': str(folder / 'marketbell.sqlite3'), 'news': {}, 'investigation': None, 'checks': {}}
    items = []
    for symbol in ('NVDA', 'JPM', 'PSX', 'VLO'):
        t = time.monotonic()
        try:
            got = sources.gather(symbol, followed=True)  # general news + press wires (+ SEC EDGAR when SEC_USER_AGENT is set)
            report['news'][symbol] = {'ok': True, 'items': len(got), 'entity_matched': sum(bool(i['entity_match']) for i in got),
                                      'by_tier': {t: sum(i['source_tier'] == t for i in got) for t in (1, 2, 3)},
                                      'by_channel': {c: sum(i.get('discovered_via') == c for i in got) for c in ('GOOGLE_NEWS_RSS', 'GOOGLE_NEWS_WIRE_SEARCH', 'SEC_EDGAR')},
                                      'channels': {k: dict(v) for k, v in sources.health.items()},
                                      'publishers': sorted({i.get('publisher') or '?' for i in got}),
                                      'published_at_range': [min((i['published_at'] for i in got), default=None), max((i['published_at'] for i in got), default=None)],
                                      'ms': round((time.monotonic() - t) * 1000)}
            items += [(symbol, i) for i in got if i['entity_match'] and not i.get('routine')]
        except ValueError as error:
            report['news'][symbol] = {'ok': False, 'error': str(error), 'ms': round((time.monotonic() - t) * 1000)}
    report['checks']['news_connectivity'] = any(v.get('ok') for v in report['news'].values())
    report['sec_edgar_enabled'] = bool(sources.sec_agent())  # informational; SEC needs only a contact User-Agent, no login
    if items:
        # Strongest source first (issuer/regulator, then newsroom), newest within a tier: what triage would investigate.
        symbol, item = sorted(items, key=lambda p: (-p[1]['source_tier'], p[1]['published_at']))[-1]
        store = Store()
        engine = Engine(store)
        package = {'symbol': symbol, 'session': 'unknown', 'trigger': 'Bounded live check of a real headline: ' + item['text'],
                   'source': str(item.get('discovered_via')) + '; publisher ' + str(item.get('publisher')), 'evidence': [item], 'change': 0, 'priority': 0}
        event = engine.ingest('monitor-public', package, 'autonomous', 'watch-' + item['id'])
        t = time.monotonic()
        try:
            result = engine.investigate('monitor-public', event['id'])
            traces = {k: result[k + '_trace'] for k in ('research', 'challenge')}
            report['investigation'] = {'symbol': symbol, 'headline': item['text'], 'publisher': item.get('publisher'),
                'source_tier': item.get('source_tier'), 'evidence_type': item.get('type'),
                'discovered_via': item.get('discovered_via'), 'published_at': item['published_at'], 'retrieved_at': item['retrieved_at'],
                'status': result['status'], 'kind': result['kind'], 'summary': result['summary'],
                'research': {k: result['research'][k] for k in ('decision', 'classification', 'material')},
                'challenge': {k: result['challenge'][k] for k in ('decision', 'classification', 'material')},
                'policy': result['policy'], 'seconds_total': round(time.monotonic() - t, 1),
                'models': sorted({tr.get('model') for tr in traces.values()}), 'provider': traces['research'].get('provider'),
                'usage': {k: tr.get('usage') for k, tr in traces.items()},
                'durations_ms': {k: tr.get('duration_ms') for k, tr in traces.items()},
                'attempts': {k: tr.get('attempt') for k, tr in traces.items()},
                'api_request_ids_present': all(tr.get('api_request_id') for tr in traces.values()),
                'challenge_request_captured': bool(traces['challenge'].get('request')),
                'challenge_blind': bool(traces['challenge'].get('request')) and json.dumps(result['research']['summary']) not in json.dumps(traces['challenge']['request'])}
        except ValueError as error:
            report['investigation'] = {'symbol': symbol, 'error': str(error), 'seconds_total': round(time.monotonic() - t, 1)}
        with store.connect() as db:
            calls = [dict(r) for r in db.execute('SELECT stage,status,duration_ms,reserved,usage,error FROM model_calls ORDER BY id')]
        usage_cost = 0.0
        for c in calls:
            u = json.loads(c['usage']) if c['usage'] else {}
            usage_cost += (u.get('prompt_tokens', 0) * 0.30 + u.get('completion_tokens', 0) * 0.90) / 1e6
        report['model_calls'] = {'attempts': len(calls), 'succeeded': sum(c['status'] == 'success' for c in calls),
            'per_call': [{k: c[k] for k in ('stage', 'status', 'duration_ms', 'error')} for c in calls],
            'reserved_usd': round(sum(c['reserved'] for c in calls), 6), 'usage_based_usd_at_list_rates': round(usage_cost, 6),
            'ceiling_usd': float(CEILING_USD)}
        reopened = Store(store.path)  # restart: durable persistence and journal verification
        report['checks']['persisted_after_reopen'] = bool(reopened.snapshot('monitor-public')['events'])
        report['checks']['journal_verified'] = reopened.verify()
        from backend.app import Application
        import io
        app = Application(reopened, start_worker=False)
        out = {}
        body = app({'REQUEST_METHOD': 'GET', 'PATH_INFO': '/api/state', 'HTTP_HOST': '127.0.0.1:4318', 'REMOTE_ADDR': '127.0.0.1',
                    'wsgi.input': io.BytesIO()}, lambda s, h: out.update(status=s))
        state = json.loads(body[0])
        shown = [e for e in state['monitored']['events'] if e['id'] == event['id']]
        report['checks']['ui_state_lists_case'] = bool(shown)
        report['checks']['ui_state_hides_raw_transcripts'] = bool(shown) and all(set(shown[0][k + '_trace']) <= {'stage', 'model', 'provider', 'at', 'duration_ms', 'usage', 'api_request_id', 'skipped', 'reason'} for k in ('research', 'challenge') if shown[0].get(k + '_trace'))
        report['checks']['challenge_blind'] = bool(report['investigation'] and report['investigation'].get('challenge_blind'))
        report['checks']['real_research_and_challenge'] = bool(report['investigation'] and report['investigation'].get('research') and report['investigation'].get('challenge') and authentic)
        report['checks']['spend_within_ceiling'] = report['model_calls']['reserved_usd'] <= float(CEILING_USD) + 1e-9
        report['view_in_browser'] = {
            'powershell': f"$env:MARKETBELL_DATA_DIR='{folder}'; $env:MARKETBELL_WATCH='0'; $env:MARKETBELL_SCHEDULER='0'; .venv\\Scripts\\python -B -m backend.main",
            'cmd': f'set MARKETBELL_DATA_DIR={folder} && set MARKETBELL_WATCH=0 && set MARKETBELL_SCHEDULER=0 && npm run dev',
            'then': 'open http://127.0.0.1:4318 > Today > Autonomous'}
    report['passed'] = bool(authentic and items and all(v for k, v in report['checks'].items()))
    out_file = ROOT / 'docs' / 'evidence' / 'LIVE_BOUNDED_CHECK.json'
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(report, indent=1, default=str), encoding='utf-8')
    print(json.dumps(report, indent=1, default=str))
    sys.exit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
