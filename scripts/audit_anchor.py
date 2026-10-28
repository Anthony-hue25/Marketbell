"""Verify the audit journal and anchor its head outside the database.

The hash-linked journal detects edits inside the retained history, but a database owner could
replace everything or truncate a suffix. Writing the head (sequence + hash) to write-once storage on
a schedule, and checking each new run against the previous anchor, detects both.

    python -m scripts.audit_anchor

Targets:
  MARKETBELL_ANCHOR_CONTAINER_URL  https://<account>.blob.core.windows.net/<container> (Azure;
                                   container should have a version-level immutability policy).
                                   Authenticates with the Container Apps managed identity.
  MARKETBELL_ANCHOR_DIR            local directory (development / tests).
Exit code 0 = verified and anchored; 2 = integrity failure (alert); 1 = could not run.
"""
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from backend.store import Store
from backend.telemetry import emit


class LocalTarget:
    def __init__(self, folder):
        self.folder = Path(folder); self.folder.mkdir(parents=True, exist_ok=True)

    def latest(self):
        names = sorted(p.name for p in self.folder.glob('anchor-*.json'))
        return json.loads((self.folder / names[-1]).read_text()) if names else None

    def write(self, name, body):
        path = self.folder / name
        with path.open('x') as f:  # write-once
            f.write(json.dumps(body, sort_keys=True))


class BlobTarget:
    def __init__(self, url):
        if not url.startswith('https://'):
            raise ValueError('ANCHOR_URL_MUST_BE_HTTPS')
        self.url = url.rstrip('/')

    def headers(self):
        from backend.db import managed_identity_token
        return {'Authorization': 'Bearer ' + managed_identity_token('https://storage.azure.com/'), 'x-ms-version': '2021-08-06'}

    def latest(self):
        import requests
        r = requests.get(self.url, params={'restype': 'container', 'comp': 'list', 'prefix': 'anchor-'}, headers=self.headers(), timeout=20)
        if r.status_code != 200:
            raise ValueError('ANCHOR_LIST_FAILED_' + str(r.status_code))
        names = sorted(n.text for n in ET.fromstring(r.content).iter('Name'))  # nosec B314 - Azure response
        if not names:
            return None
        g = requests.get(self.url + '/' + names[-1], headers=self.headers(), timeout=20)
        if g.status_code != 200:
            raise ValueError('ANCHOR_READ_FAILED_' + str(g.status_code))
        return g.json()

    def write(self, name, body):
        import requests
        h = {**self.headers(), 'x-ms-blob-type': 'BlockBlob', 'Content-Type': 'application/json', 'If-None-Match': '*'}
        r = requests.put(self.url + '/' + name, data=json.dumps(body, sort_keys=True).encode(), headers=h, timeout=20)
        if r.status_code not in (201,):
            raise ValueError('ANCHOR_WRITE_FAILED_' + str(r.status_code))


def target():
    if os.environ.get('MARKETBELL_ANCHOR_CONTAINER_URL'):
        return BlobTarget(os.environ['MARKETBELL_ANCHOR_CONTAINER_URL'])
    if os.environ.get('MARKETBELL_ANCHOR_DIR'):
        return LocalTarget(os.environ['MARKETBELL_ANCHOR_DIR'])
    raise ValueError('ANCHOR_TARGET_NOT_CONFIGURED')


def run(store=None, where=None):
    store = store or Store()
    where = where or target()
    try:
        verified = store.verify()  # full chain + model/note/impact/evidence commitments
    except ValueError as error:
        emit('audit.integrity_failed', code=str(error))
        return 2, {'status': 'INTEGRITY_FAILED', 'code': str(error)}
    head = store.head()
    previous = where.latest()
    if previous:
        with store.connect() as db:
            row = db.execute('SELECT hash FROM audit WHERE seq=?', (previous['seq'],)).fetchone()
        if head['seq'] < previous['seq'] or not row or row['hash'] != previous['hash']:
            emit('audit.integrity_failed', code='ANCHORED_HISTORY_TRUNCATED_OR_REWRITTEN', anchored_seq=previous['seq'], current_seq=head['seq'])
            return 2, {'status': 'INTEGRITY_FAILED', 'code': 'ANCHORED_HISTORY_TRUNCATED_OR_REWRITTEN', 'anchored': previous, 'current': head}
    body = {'seq': head['seq'], 'hash': head['hash'], 'journal_at': head['at'], 'records': verified['records'],
            'anchored_at': datetime.now(timezone.utc).isoformat(), 'database': store.dialect,
            'previous': {'seq': previous['seq'], 'hash': previous['hash']} if previous else None}
    if previous and previous['seq'] == head['seq']:
        emit('audit.anchor', status='unchanged', seq=head['seq'])
        return 0, {'status': 'VERIFIED_UNCHANGED', **body}
    where.write('anchor-%012d.json' % head['seq'], body)
    emit('audit.anchor', status='anchored', seq=head['seq'], records=verified['records'])
    return 0, {'status': 'ANCHORED', **body}


def main():
    try:
        code, result = run()
    except Exception as error:
        emit('audit.anchor_failed', code=str(error)[:120] if str(error).isupper() else type(error).__name__)
        print(json.dumps({'status': 'FAILED_TO_RUN'}))
        sys.exit(1)
    print(json.dumps(result))
    sys.exit(code)


if __name__ == '__main__':
    main()
