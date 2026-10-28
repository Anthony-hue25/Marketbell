"""Migrations, tenant-scoped documents, transactional state + hash-linked journal.

SQLite is the local/development engine. PostgreSQL (MARKETBELL_DATABASE_URL) is the Azure
production engine; see backend/db.py for the dialect boundary.
"""
import hashlib
import re
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from .config import DATA, now
from . import db as dbapi

def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)

HEX64 = re.compile(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])')

def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()

class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()

# Version 9: worker/job leases, scoped model budgets, content-addressed evidence history.
SQLITE_V9 = '''
CREATE TABLE IF NOT EXISTS leases(name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL, acquired TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS call_budget(day TEXT NOT NULL, scope TEXT NOT NULL, calls INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(day,scope));
CREATE TABLE IF NOT EXISTS evidence_versions(tenant TEXT NOT NULL, evidence_id TEXT NOT NULL, content_hash TEXT NOT NULL,
    first_seen TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(tenant,evidence_id,content_hash));
CREATE INDEX IF NOT EXISTS evidence_versions_seen ON evidence_versions(tenant,evidence_id,first_seen);
CREATE TRIGGER IF NOT EXISTS evidence_versions_no_update BEFORE UPDATE ON evidence_versions BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER IF NOT EXISTS evidence_versions_no_delete BEFORE DELETE ON evidence_versions BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE INDEX IF NOT EXISTS jobs_lease ON jobs(status,lease_expires);
'''

PG_SCHEMA = '''
CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents(tenant TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(tenant,kind,id));
CREATE TABLE IF NOT EXISTS audit(seq BIGSERIAL PRIMARY KEY, tenant TEXT NOT NULL, at TEXT NOT NULL, type TEXT NOT NULL,
    detail TEXT NOT NULL, parent TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, tenant TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, tenant TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL, updated TEXT NOT NULL, error TEXT, priority INTEGER NOT NULL DEFAULT 5,
    started_at TEXT, lease_owner TEXT, lease_expires DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS costs(day TEXT PRIMARY KEY, calls INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS model_calls(id BIGSERIAL PRIMARY KEY, at TEXT NOT NULL, context TEXT NOT NULL, stage TEXT NOT NULL,
    reserved DOUBLE PRECISION NOT NULL, duration_ms INTEGER, status TEXT NOT NULL, usage TEXT, error TEXT);
CREATE INDEX IF NOT EXISTS calls_at ON model_calls(at);
CREATE INDEX IF NOT EXISTS calls_context ON model_calls(context);
CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(status,priority,created);
CREATE INDEX IF NOT EXISTS jobs_lease ON jobs(status,lease_expires);
CREATE INDEX IF NOT EXISTS audit_tenant_seq ON audit(tenant,seq);
CREATE TABLE IF NOT EXISTS leases(name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL, acquired TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS call_budget(day TEXT NOT NULL, scope TEXT NOT NULL, calls INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(day,scope));
CREATE TABLE IF NOT EXISTS evidence_versions(tenant TEXT NOT NULL, evidence_id TEXT NOT NULL, content_hash TEXT NOT NULL,
    first_seen TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(tenant,evidence_id,content_hash));
CREATE INDEX IF NOT EXISTS evidence_versions_seen ON evidence_versions(tenant,evidence_id,first_seen);
CREATE OR REPLACE TRIGGER audit_immutable BEFORE UPDATE OR DELETE ON audit FOR EACH ROW EXECUTE FUNCTION mb_immutable('IMMUTABLE_AUDIT');
CREATE OR REPLACE TRIGGER audit_no_truncate BEFORE TRUNCATE ON audit FOR EACH STATEMENT EXECUTE FUNCTION mb_immutable('IMMUTABLE_AUDIT');
CREATE OR REPLACE TRIGGER evidence_versions_immutable BEFORE UPDATE OR DELETE ON evidence_versions FOR EACH ROW EXECUTE FUNCTION mb_immutable('IMMUTABLE_EVIDENCE');
CREATE OR REPLACE TRIGGER evidence_versions_no_truncate BEFORE TRUNCATE ON evidence_versions FOR EACH STATEMENT EXECUTE FUNCTION mb_immutable('IMMUTABLE_EVIDENCE')
'''

SYSTEM_TENANTS = ('monitor-public',)

class Store:
    def __init__(self, path=None, url=None, schema=None):
        test_url = os.environ.get('MARKETBELL_TEST_DATABASE_URL', '').strip()
        if url is None and path is None:
            url = dbapi.database_url() or None
        elif url is None and str(path).startswith('postgres:'):
            # Reopen a PostgreSQL store from its own .path descriptor (restart semantics in tests/tools).
            url = test_url or dbapi.database_url()
            schema = schema or (str(path).split('#', 1)[1] if '#' in str(path) else None)
        elif url is None and test_url:
            # Test isolation: one schema per Store path on a shared PostgreSQL test server.
            url = test_url
            schema = schema or 'mbt_' + hashlib.sha256(str(path).encode()).hexdigest()[:20]
        self.url = url
        self.dialect = 'postgres' if url else 'sqlite'
        if self.dialect == 'postgres':
            self.schema = schema or os.environ.get('MARKETBELL_DB_SCHEMA') or None
            if self.schema and not self.schema.replace('_', '').isalnum():
                raise ValueError('DATABASE_SCHEMA_INVALID')
            self.path = 'postgres:' + dbapi.redact_url(url) + ('#' + self.schema if self.schema else '')
            self._pool = dbapi.pool(url, self.schema)
            self._init_postgres()
        else:
            self.path = str(path or DATA / 'marketbell.sqlite3')
            from pathlib import Path
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self._init_sqlite()
        from .impact import initialize
        initialize(self)
        self.verify()

    def _init_sqlite(self):
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
                INSERT OR IGNORE INTO migrations VALUES(1, CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS documents(tenant TEXT, kind TEXT, id TEXT, body TEXT NOT NULL,
                    PRIMARY KEY(tenant,kind,id));
                CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY AUTOINCREMENT, tenant TEXT NOT NULL,
                    at TEXT NOT NULL, type TEXT NOT NULL, detail TEXT NOT NULL, parent TEXT NOT NULL, hash TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'IMMUTABLE_AUDIT'); END;
                CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'IMMUTABLE_AUDIT'); END;
                CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, tenant TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, tenant TEXT NOT NULL, kind TEXT NOT NULL,
                    payload TEXT NOT NULL,status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    created TEXT NOT NULL,updated TEXT NOT NULL,error TEXT);
                CREATE TABLE IF NOT EXISTS costs(day TEXT PRIMARY KEY,calls INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
            if 'priority' not in {r[1] for r in db.execute('PRAGMA table_info(jobs)')}:
                db.execute('ALTER TABLE jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 5')
            db.execute('INSERT OR IGNORE INTO migrations VALUES(2, CURRENT_TIMESTAMP)')
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS model_calls(id INTEGER PRIMARY KEY, at TEXT NOT NULL, context TEXT NOT NULL,
                    stage TEXT NOT NULL, reserved REAL NOT NULL, duration_ms INTEGER, status TEXT NOT NULL,
                    usage TEXT, error TEXT);
                CREATE INDEX IF NOT EXISTS calls_at ON model_calls(at);
                CREATE INDEX IF NOT EXISTS calls_context ON model_calls(context);
                CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(status,priority,created);
                CREATE INDEX IF NOT EXISTS audit_tenant_seq ON audit(tenant,seq);
                INSERT OR IGNORE INTO migrations VALUES(3,CURRENT_TIMESTAMP);
            """)
        with self.connect() as db:
            columns = {r[1] for r in db.execute('PRAGMA table_info(jobs)')}
            if 'started_at' not in columns:
                db.execute('ALTER TABLE jobs ADD COLUMN started_at TEXT')
            db.execute('INSERT OR IGNORE INTO migrations VALUES(5,CURRENT_TIMESTAMP)')
        with self.connect() as db:
            columns = {r[1] for r in db.execute('PRAGMA table_info(jobs)')}
            for name, kind in (('lease_owner', 'TEXT'), ('lease_expires', 'REAL')):
                if name not in columns:
                    db.execute('ALTER TABLE jobs ADD COLUMN ' + name + ' ' + kind)
            db.executescript(SQLITE_V9)
            db.execute('INSERT OR IGNORE INTO migrations VALUES(9,CURRENT_TIMESTAMP)')

    def _init_postgres(self):
        with self.connect() as db:
            db.execute('BEGIN READ COMMITTED')  # Not a snapshot: DDL must see the other starter's commit.
            # Serialize concurrent first starts (API and worker containers) during DDL.
            db._raw.execute('SELECT pg_advisory_xact_lock(724100002)')
            if self.schema:
                db._raw.execute('CREATE SCHEMA IF NOT EXISTS ' + self.schema)
            db._raw.execute(dbapi.PG_FUNCTIONS)
            db._raw.execute(PG_SCHEMA)
            for version in (1, 2, 3, 5, 9):
                db.execute('INSERT OR IGNORE INTO migrations VALUES(?,?)', (version, now()))
            db.commit()

    def connect(self):
        if self.dialect == 'postgres':
            return self._pool.connection()
        db = sqlite3.connect(self.path, timeout=15, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=15000')
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, db, tenant, kind, id):
        row = db.execute('SELECT body FROM documents WHERE tenant=? AND kind=? AND id=?', (tenant, kind, id)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, db, tenant, kind, id, value):
        db.execute('INSERT INTO documents VALUES(?,?,?,?) ON CONFLICT(tenant,kind,id) DO UPDATE SET body=excluded.body', (tenant, kind, id, encoded(value)))
        if kind=='event':
            from .impact import record
            record(self,db,tenant,value)

    def put_evidence(self, db, tenant, item):
        """Evidence history is append-only and content-addressed.

        The mutable 'evidence' document keeps the FIRST version seen under an id, so a later revision
        of the same source can never silently rewrite what an earlier decision cited. Every distinct
        version is retained in evidence_versions. Returns True when this content is new.
        """
        content = digest(item)
        inserted = db.execute('INSERT OR IGNORE INTO evidence_versions VALUES(?,?,?,?,?)',
            (tenant, item['id'], content, now(), encoded(item))).rowcount
        if not self.get(db, tenant, 'evidence', item['id']):
            db.execute('INSERT INTO documents VALUES(?,?,?,?) ON CONFLICT(tenant,kind,id) DO NOTHING', (tenant, 'evidence', item['id'], encoded(item)))
        elif inserted:
            self.append(db, tenant, 'Evidence revision observed', item['id'] + ': new content ' + content + ' retained separately; earlier version unchanged.')
        return bool(inserted)

    def all(self, db, tenant, kind):
        return [json.loads(r[0]) for r in db.execute('SELECT body FROM documents WHERE tenant=? AND kind=? ORDER BY id', (tenant, kind))]

    def append(self, db, tenant, type, detail):
        parent = db.execute('SELECT hash FROM audit ORDER BY seq DESC LIMIT 1').fetchone()
        item = dict(tenant=tenant, at=now(), type=type, detail=detail, parent=parent[0] if parent else '')
        item['hash'] = digest(item)
        db.execute('INSERT INTO audit(tenant,at,type,detail,parent,hash) VALUES(?,?,?,?,?,?)', tuple(item[k] for k in ('tenant','at','type','detail','parent','hash')))

    def verify(self):
        parent = ''
        with self.connect() as db:
            db.execute('BEGIN')  # One consistent journal/document view while writers continue.
            for row in db.execute('SELECT * FROM audit ORDER BY seq'):
                item = {k:row[k] for k in ('tenant','at','type','detail','parent')}
                if item['parent'] != parent or digest(item) != row['hash']:
                    raise ValueError('AUDIT_CHAIN_INVALID')
                parent = row['hash']
            # Verify persisted model records and prepared notes against their journal commitments.
            # Every commitment is a SHA-256 written into a journal entry. Collect them once as a set per
            # tenant: membership is O(1), where a substring search over the joined journal made start-up
            # verification grow with the square of history (2,000 cases: 0.6 s; a year: tens of seconds).
            commitments={}
            for row in db.execute('SELECT tenant,detail FROM audit'):
                commitments.setdefault(row['tenant'],set()).update(HEX64.findall(row['detail']))
            for row in db.execute("SELECT tenant,body FROM documents WHERE kind='event'"):
                event=json.loads(row['body'])
                for key in ('research_trace','challenge_trace'):
                    trace=event.get(key)
                    if trace and digest(trace) not in commitments.get(row['tenant'],()):
                        raise ValueError('MODEL_RECORD_TAMPERED')
                if event.get('action'):
                    note=self.get(db,row['tenant'],'note',event['action']['id'])
                    if not note or digest(note)!=event['action']['hash'] or digest(note) not in commitments.get(row['tenant'],()):
                        raise ValueError('ACTION_RECORD_TAMPERED')
            for table in ('decision_snapshots','evaluations'):
                for row in db.execute('SELECT tenant,body,hash FROM '+table):  # nosec B608 - table from a fixed tuple
                    if digest(json.loads(row['body']))!=row['hash'] or row['hash'] not in commitments.get(row['tenant'],()):
                        raise ValueError('IMPACT_RECORD_TAMPERED')
            for row in db.execute('SELECT content_hash,body FROM evidence_versions'):
                if digest(json.loads(row['body']))!=row['content_hash']:
                    raise ValueError('EVIDENCE_RECORD_TAMPERED')
            return {'records':db.execute('SELECT count(*) FROM audit').fetchone()[0], 'head':parent}

    def head(self):
        """Current audit head for external anchoring (cheap; no full-chain verification)."""
        with self.connect() as db:
            row = db.execute('SELECT seq,hash,at FROM audit ORDER BY seq DESC LIMIT 1').fetchone()
            return {'seq': row['seq'], 'hash': row['hash'], 'at': row['at']} if row else {'seq': 0, 'hash': '', 'at': None}

    def snapshot(self, tenant, db=None):
        if db is None:
            with self.connect() as connection:return self.snapshot(tenant,connection)
        events = [json.loads(r[0]) for r in db.execute("SELECT d.body FROM impact_cases i JOIN documents d ON d.tenant=i.tenant AND d.kind='event' AND d.id=i.event WHERE i.tenant=? ORDER BY i.attention DESC,i.created DESC LIMIT 40",(tenant,))]
        return {'events':sorted(events, key=lambda e:e['created'], reverse=True), 'rules':self.all(db, tenant, 'rule'),
            'portfolio':self.get(db, tenant, 'portfolio', 'main'),
            'ruleStats':[dict(r) for r in db.execute("SELECT json_extract(body,'$.rule') AS rule,count(*) AS evaluated,sum(json_extract(body,'$.matched')=0) AS nonmatches,sum(json_extract(body,'$.policy_passed')=0 OR json_extract(body,'$.reviews_clear')=0) AS exceptions FROM documents WHERE tenant=? AND kind='rule_evaluation' GROUP BY json_extract(body,'$.rule')",(tenant,))],
            'candidate':self.get(db, tenant, 'candidate', 'current'),
            'notes':[json.loads(r[0]) for r in db.execute("SELECT body FROM documents WHERE tenant=? AND kind='note' ORDER BY json_extract(body,'$.created') DESC LIMIT 40",(tenant,))],
            'audit':[dict(r) for r in db.execute('SELECT seq AS id,at,type,detail,hash FROM audit WHERE tenant=? ORDER BY seq DESC LIMIT 300',(tenant,))],
            'jobs':[dict(r) for r in db.execute('SELECT id,kind,status,attempts,created,updated,error FROM jobs WHERE tenant=? ORDER BY created DESC LIMIT 10',(tenant,))]}

    @staticmethod
    def budget_scope(tenant):
        return 'system' if tenant in SYSTEM_TENANTS else 'interactive'

    def reserve_call(self, limit, tenant=None):
        """Daily call budget. Interactive (visitor/demo) calls have their own cap so public use can
        never consume the allowance that always-on monitoring needs."""
        day = now()[:10]
        scope = self.budget_scope(tenant) if tenant else None
        with self.transaction() as db:
            db.execute('INSERT OR IGNORE INTO costs(day,calls) VALUES(?,0)', (day,))
            n = db.execute('SELECT calls FROM costs WHERE day=?', (day,)).fetchone()[0]
            if n >= limit:
                raise ValueError('DAILY_MODEL_BUDGET_EXHAUSTED')
            if scope == 'interactive':
                cap = int(os.environ.get('MARKETBELL_INTERACTIVE_DAILY_CALLS', str(max(1, limit // 3))))
                db.execute('INSERT OR IGNORE INTO call_budget(day,scope,calls) VALUES(?,?,0)', (day, scope))
                used = db.execute('SELECT calls FROM call_budget WHERE day=? AND scope=?', (day, scope)).fetchone()[0]
                if used >= cap:
                    raise ValueError('INTERACTIVE_MODEL_BUDGET_EXHAUSTED')
            if scope:
                db.execute('INSERT OR IGNORE INTO call_budget(day,scope,calls) VALUES(?,?,0)', (day, scope))
                db.execute('UPDATE call_budget SET calls=calls+1 WHERE day=? AND scope=?', (day, scope))
            db.execute('UPDATE costs SET calls=calls+1 WHERE day=?',(day,))

    def reserve_model(self, context, stage, reserved):
        at=now()
        with self.transaction() as db:
            day=db.execute("SELECT COALESCE(sum(reserved),0) FROM model_calls WHERE at>=?",(at[:10],)).fetchone()[0]
            month=db.execute("SELECT COALESCE(sum(reserved),0) FROM model_calls WHERE at>=?",(at[:7],)).fetchone()[0]
            if day+reserved>float(os.environ.get('MARKETBELL_DAILY_USD','0.50')) or month+reserved>float(os.environ.get('MARKETBELL_MONTHLY_USD','10')):
                raise ValueError('SPEND_BUDGET_EXHAUSTED')
            if db.execute('SELECT count(*) FROM model_calls WHERE context=?',(context,)).fetchone()[0]>=int(os.environ.get('MARKETBELL_EVENT_CALLS','12')):
                raise ValueError('EVENT_MODEL_BUDGET_EXHAUSTED')
            return db.execute('INSERT INTO model_calls(at,context,stage,reserved,status) VALUES(?,?,?,?,?) RETURNING id',
                (at,context,stage,reserved,'reserved')).fetchone()[0]

    def finish_model(self, id, duration, usage=None, error=None):
        with self.transaction() as db:
            db.execute('UPDATE model_calls SET duration_ms=?,status=?,usage=?,error=? WHERE id=?',
                (duration,'failed' if error else 'success',encoded(usage) if usage else None,error,id))

    # ---- Leases: one active worker / monitor across processes, containers and revisions. ----

    def acquire_lease(self, name, owner, ttl):
        at = time.time()
        with self.transaction() as db:
            row = db.execute('SELECT owner,expires FROM leases WHERE name=?', (name,)).fetchone()
            if row and row['owner'] != owner and row['expires'] > at:
                return False
            if row and row['owner'] == owner:
                db.execute('UPDATE leases SET expires=? WHERE name=?', (at + ttl, name))
            elif row:
                db.execute('UPDATE leases SET owner=?,expires=?,acquired=? WHERE name=?', (owner, at + ttl, now(), name))
            else:
                db.execute('INSERT INTO leases VALUES(?,?,?,?)', (name, owner, at + ttl, now()))
            return True

    def release_lease(self, name, owner):
        with self.transaction() as db:
            db.execute('UPDATE leases SET expires=0 WHERE name=? AND owner=?', (name, owner))

    def lease(self, name):
        with self.connect() as db:
            row = db.execute('SELECT owner,expires,acquired FROM leases WHERE name=?', (name,)).fetchone()
            return dict(row) if row else None

    def diagnostics(self):
        with self.connect() as db:
            jobs={r[0]:r[1] for r in db.execute('SELECT status,count(*) FROM jobs GROUP BY status')}
            oldest=db.execute("SELECT min(created) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            calls=dict(db.execute("SELECT count(*) AS attempts,COALESCE(sum(reserved),0) AS reserved_usd,COALESCE(avg(duration_ms),0) AS avg_model_ms FROM model_calls").fetchone())
            failures=db.execute("SELECT count(*) FROM model_calls WHERE status='failed'").fetchone()[0]
            beats={k:self.get(db,'monitor-public','heartbeat',k) for k in ('worker','monitor')}
            budgets={r['scope']:r['calls'] for r in db.execute('SELECT scope,calls FROM call_budget WHERE day=?',(now()[:10],))}
            from .telemetry import resource_usage
            return {'resources':resource_usage(),'database':self.dialect,'newsUnique':db.execute("SELECT count(*) FROM news_identity WHERE tenant='monitor-public'").fetchone()[0],'materialCases':db.execute("SELECT count(*) FROM impact_cases WHERE material=1 AND origin='genuine'").fetchone()[0],'decisionBacklog':db.execute("SELECT count(*) FROM impact_cases WHERE attention=1 AND origin='genuine'").fetchone()[0],'rulesApplied':db.execute("SELECT count(*) FROM impact_cases WHERE rule_id IS NOT NULL AND autonomous=1 AND origin='genuine'").fetchone()[0],'recoveryAttempts':db.execute('SELECT COALESCE(sum(max(attempts-1,0)),0) FROM jobs').fetchone()[0],'averageCompletedJobMs':db.execute("SELECT COALESCE(avg((julianday(updated)-julianday(started_at))*86400000),0) FROM jobs WHERE status='completed' AND started_at IS NOT NULL").fetchone()[0],'sourceHealth':self.get(db,'monitor-public','monitor','continuous'),'jobs':jobs,'oldestPending':oldest,'model':{**calls,'failures':failures},'heartbeat':beats,'callBudgets':budgets,
                'leases':{r['name']:{'owner':r['owner'],'expires':r['expires']} for r in db.execute('SELECT name,owner,expires FROM leases')},
                'deferredHoldingNotices':db.execute("SELECT count(*) FROM documents WHERE kind='deferred_notice' AND json_extract(body,'$.state')='deferred'").fetchone()[0]}

    def backup(self, target):
        if self.dialect != 'sqlite':
            raise ValueError('USE_MANAGED_POSTGRES_BACKUP')
        with self.connect() as source, sqlite3.connect(str(target),factory=ClosingConnection) as dest:
            source.backup(dest)
