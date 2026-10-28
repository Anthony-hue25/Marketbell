"""Post-deployment smoke test (read-only apart from creating one anonymous demo session).

    python -m scripts.smoke https://<app>.<region>.azurecontainerapps.io [--wait-worker 300]
    python -m scripts.smoke http://127.0.0.1:8080 --host marketbell.test --origin https://marketbell.test

Checks liveness/readiness, security headers, cookie flags, CSRF/admin rejection and that the
separately running worker has a fresh heartbeat (proof that monitoring runs, not just the website).
Optional: MARKETBELL_SMOKE_WEBHOOK_TOKEN delivers one clearly labelled test event and waits for a
durable final state. Exit code 0 = all checks passed.
"""
import argparse
import json
import os
import sys
import time
import uuid

import requests


def main():
    p = argparse.ArgumentParser()
    p.add_argument('base')
    p.add_argument('--host', help='Host header override (local testing)')
    p.add_argument('--origin', help='Expected public origin (defaults to base)')
    p.add_argument('--wait-worker', type=int, default=180)
    p.add_argument('--event-timeout', type=int, default=600)
    a = p.parse_args()
    base, origin = a.base.rstrip('/'), (a.origin or a.base).rstrip('/')
    h = {'Host': a.host} if a.host else {}
    results, ok = [], True

    def check(name, condition, detail=''):
        nonlocal ok
        ok = ok and bool(condition)
        results.append({'check': name, 'pass': bool(condition), 'detail': detail})

    r = requests.get(base + '/livez', headers=h, timeout=15)
    check('liveness', r.status_code == 200, r.status_code)
    r = requests.get(base + '/healthz', headers=h, timeout=15)
    body = r.json() if r.headers.get('Content-Type', '').startswith('application/json') else {}
    check('readiness_database', r.status_code == 200 and body.get('database') == 'ready', body)
    check('worker_not_in_web_process', body.get('worker_in_process') is False, body.get('worker_in_process'))
    r = requests.get(base + '/', headers=h, timeout=15)
    check('index', r.status_code == 200 and b'MarketBell' in r.content, r.status_code)
    csp = r.headers.get('Content-Security-Policy', '')
    check('csp', "script-src 'self'" in csp and "frame-ancestors 'none'" in csp, csp[:80])
    if origin.startswith('https://'):
        check('hsts', 'max-age=' in r.headers.get('Strict-Transport-Security', ''), r.headers.get('Strict-Transport-Security'))
    r = requests.get(base + '/api/state', headers=h, timeout=30)
    cookie = r.headers.get('Set-Cookie', '')
    check('session_cookie_flags', 'HttpOnly' in cookie and 'SameSite=Strict' in cookie and ('Secure' in cookie or not origin.startswith('https://')), cookie.split(';', 1)[-1])
    state = r.json()
    check('no_execution_capability', state.get('executionEnabled') is False)
    check('morning_brief_and_quiet_log', isinstance(state.get('brief', {}).get('sentence'), str) and isinstance(state.get('quiet'), list), list(state.get('brief') or {}))
    jar = cookie.split(';')[0]
    r = requests.post(base + '/api/action', json={'action': 'restart'}, headers={**h, 'Cookie': jar, 'Origin': origin}, timeout=15)
    check('csrf_enforced', r.status_code == 403, r.status_code)
    r = requests.get(base + '/api/diagnostics', headers=h, timeout=15)
    check('admin_protected', r.status_code == 403, r.status_code)
    deadline, healthy = time.time() + a.wait_worker, False
    while time.time() < deadline:
        s = requests.get(base + '/api/state', headers={**h, 'Cookie': jar}, timeout=30).json()
        if (s.get('worker') or {}).get('healthy'):
            healthy = True
            break
        time.sleep(10)
    check('worker_heartbeat_fresh', healthy, (s.get('worker') or {}))
    token = os.environ.get('MARKETBELL_SMOKE_WEBHOOK_TOKEN')
    if token:
        from datetime import datetime, timezone
        event = {'symbol': 'NVDA', 'event_id': 'smoke-' + uuid.uuid4().hex, 'source': 'Deployment smoke test',
                 'text': 'SMOKE TEST ONLY: synthetic deployment verification notice; not market information.',
                 'published_at': datetime.now(timezone.utc).isoformat()}
        r = requests.post(base + '/api/webhook', json=event, headers={**h, 'Authorization': 'Bearer ' + token}, timeout=30)
        check('webhook_accepted', r.status_code == 200, r.text[:120])
        deadline, final = time.time() + a.event_timeout, None
        while r.status_code == 200 and time.time() < deadline:
            s = requests.get(base + '/api/state', headers={**h, 'Cookie': jar}, timeout=30).json()
            jobs = {j['id']: j for j in (s.get('monitored') or {}).get('jobs', [])}
            job = jobs.get(r.json()['job'])
            if job and job['status'] in ('completed', 'failed', 'completed_with_errors'):
                final = job['status']
                break
            time.sleep(10)
        check('webhook_event_reached_durable_state', final is not None, final)
    print(json.dumps({'base': base, 'passed': ok, 'results': results}, indent=1, default=str))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
