"""Local production-like harness: PostgreSQL + API container + worker container(s) + mocks.

Used for the documented restart/outage/duplicate/backlog/benchmark evidence. Everything is local:
no Azure resources, no real model or news calls. Requires docker and a reachable PostgreSQL.
"""
import json, os, subprocess, time, hashlib, uuid
import requests

IMAGE = os.environ.get('MB_IMAGE', 'marketbell:assurance')
DB = os.environ.get('MB_DB', 'postgresql://postgres@127.0.0.1:55432/mbfault')
WORK = os.environ.get('MB_WORK', '/var/tmp/mbfault')
API = 'http://127.0.0.1:8080'
HOST = 'marketbell.test'
WEBHOOK = 'w' * 40
ADMIN = 'a' * 40
NEM, NEWS = WORK + '/nemotron.json', WORK + '/news.json'

def sh(cmd, check=True):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(cmd + '\n' + r.stderr[-2000:])
    return r.stdout.strip()

def control(path, **values):
    data = {}
    try: data = json.load(open(path))
    except (OSError, ValueError): pass
    data.update(values); json.dump(data, open(path, 'w'))

def common_env():
    return {'MARKETBELL_DATABASE_URL': DB, 'MARKETBELL_STRUCTURED_LOGS': '1', 'NEBIUS_API_KEY': 'mock-not-a-secret',
            'MARKETBELL_WEBHOOK_TOKEN': WEBHOOK, 'MARKETBELL_ADMIN_TOKEN': ADMIN, 'MARKETBELL_DAILY_CALLS': '2000',
            'MARKETBELL_DAILY_USD': '50', 'MARKETBELL_WEBHOOK_PER_MINUTE': '1000', 'MARKETBELL_MONTHLY_USD': '500'}

def run_api(name='mb-api'):
    env = {**common_env(), 'MARKETBELL_ENV': 'staging', 'MARKETBELL_PUBLIC_ORIGIN': 'https://' + HOST,
           'MARKETBELL_TRUSTED_PROXY_HOPS': '1', 'MARKETBELL_WORKER': '0', 'MARKETBELL_ROLE': 'api', 'MARKETBELL_ALLOW_LIVE': '1'}
    flags = ' '.join('-e %s=%s' % kv for kv in env.items())
    sh(f'docker rm -f {name} >/dev/null 2>&1', check=False)
    sh(f'docker run -d --name {name} --network host --read-only --tmpfs /tmp:size=64m --cap-drop ALL --security-opt no-new-privileges {flags} {IMAGE}')

def run_worker(name='mb-worker', port=8081, **extra):
    env = {**common_env(), 'MARKETBELL_ENV': 'test', 'MARKETBELL_ROLE': 'worker', 'MARKETBELL_ALLOW_LIVE': '1', 'MARKETBELL_WATCH': '1',
           'MARKETBELL_WATCH_SECONDS': '60', 'MARKETBELL_SCHEDULER': '0', 'MARKETBELL_HEALTH_PORT': str(port),
           'MARKETBELL_TEST_PROVIDER_URL': 'http://127.0.0.1:9101/v1/chat/completions', 'MARKETBELL_TEST_NEWS_URL': 'http://127.0.0.1:9102/rss',
           'MARKETBELL_RETRY_DELAY_SECONDS': '20', 'MARKETBELL_JOB_LEASE_SECONDS': '30', 'MARKETBELL_ROLE_LEASE_SECONDS': '20',
           'MARKETBELL_SHUTDOWN_GRACE_SECONDS': '5', **extra}
    flags = ' '.join('-e %s=%s' % kv for kv in env.items())
    sh(f'docker rm -f {name} >/dev/null 2>&1', check=False)
    sh(f'docker run -d --name {name} --network host --read-only --tmpfs /tmp:size=64m --cap-drop ALL --security-opt no-new-privileges --stop-timeout 30 {flags} {IMAGE} python -m backend.worker_main')

def headers(ip='203.0.113.10', cookie=None, csrf=None):
    h = {'Host': HOST, 'X-Forwarded-For': ip}
    if cookie: h['Cookie'] = cookie
    if csrf: h.update({'X-CSRF-Token': csrf, 'Origin': 'https://' + HOST, 'Content-Type': 'application/json'})
    return h

def session(ip='203.0.113.10'):
    r = requests.get(API + '/api/state', headers=headers(ip), timeout=30)
    cookie = r.headers['Set-Cookie'].split(';')[0]
    return cookie, r.json()['csrf']

def state(cookie, ip='203.0.113.10'):
    return requests.get(API + '/api/state', headers=headers(ip, cookie), timeout=30).json()

def webhook(symbol, text, event_id=None, published=None):
    from datetime import datetime, timezone
    body = {'symbol': symbol, 'event_id': event_id or uuid.uuid4().hex, 'text': text, 'source': 'Local controlled delivery',
            'published_at': published or datetime.now(timezone.utc).isoformat()}
    r = requests.post(API + '/api/webhook', json=body, headers={'Host': HOST, 'Authorization': 'Bearer ' + WEBHOOK}, timeout=30)
    return r.status_code, r.json()

def diagnostics():
    return requests.get(API + '/api/diagnostics', headers={'Host': HOST, 'Authorization': 'Bearer ' + ADMIN}, timeout=30).json()

def psql(sql):
    return sh(f"psql '{DB}' -At -c \"{sql}\"")

def jobs():
    rows = psql("SELECT id,kind,status,attempts,coalesce(error,''),coalesce(lease_owner,'') FROM jobs ORDER BY created")
    return [r.split('|') for r in rows.splitlines() if r]

def wait(predicate, timeout, step=2):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            value = predicate()
        except Exception:
            value = None
        if value: return value
        time.sleep(step)
    return None

def logs(name, event=None):
    out = sh(f'docker logs {name} 2>&1', check=False)
    lines = []
    for l in out.splitlines():
        try: rec = json.loads(l)
        except ValueError: continue
        if event is None or rec.get('event') == event: lines.append(rec)
    return lines
