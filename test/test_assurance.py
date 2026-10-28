"""Independent assurance regressions (Azure readiness + Adaptive Universe lessons).

Each test names the defect it guards. Tests run on SQLite by default and on PostgreSQL when
MARKETBELL_TEST_DATABASE_URL is set (one isolated schema per Store).
"""
import io
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from backend.store import Store, digest
from backend.engine import Engine
from backend.worker import Worker
from backend.app import Application
from backend import db as dbapi
from test_backend import package, answer, FakeAI, SESSION


def wait_until(predicate, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'db.sqlite3')
        self.ai = FakeAI()
        self.engine = Engine(self.store, self.ai)
        self.engine.ensure('a')
        self.calendar = patch('backend.engine.session_state', return_value={'latest': SESSION})
        self.calendar.start()

    def tearDown(self):
        self.calendar.stop()
        self.tmp.cleanup()

    def rule(self, approved_at=None, sector='Energy'):
        r = {'id': 'r1', 'version': 1, 'active': True, 'revoked': False, 'scope': 'opportunity_note', 'sector': sector,
             'max_position_pct': 3, 'max_sector_pct': 20, 'effect': 'allow', 'expires_days': 30, 'uses': 0,
             'approved_at': approved_at or (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
             'expires': (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()}
        with self.store.transaction() as db:
            self.store.put(db, 'a', 'rule', r['id'], r)
        return r


class EvidenceIntegrity(Base):
    """AU lesson: decision-time evidence is immutable; later revisions are separate evidence."""

    def test_revised_source_never_rewrites_decision_time_evidence(self):
        first = package()
        e = self.engine.ingest('a', first)
        e = self.engine.investigate('a', e['id'])
        original = first['evidence'][0]
        revised = {**original, 'text': original['text'] + ' CORRECTION: margins were not improving.'}
        later = {**package(), 'session': '2026-10-13', 'evidence': [revised]}
        self.engine.ingest('a', later)
        with self.store.connect() as db:
            kept = self.store.get(db, 'a', 'evidence', original['id'])
            versions = db.execute('SELECT count(*) FROM evidence_versions WHERE tenant=? AND evidence_id=?', ('a', original['id'])).fetchone()[0]
            snap = json.loads(db.execute('SELECT body FROM decision_snapshots WHERE tenant=? AND event=?', ('a', e['id'])).fetchone()[0])
            audit = [r[0] for r in db.execute("SELECT type FROM audit WHERE tenant='a'")]
        self.assertEqual(kept['text'], original['text'])  # first version kept (was: last write won)
        self.assertEqual(versions, 2)
        self.assertIn('Evidence revision observed', audit)
        self.assertEqual(snap['evidence_sealed'][0]['sha256'], digest(original))
        self.assertEqual(snap['evidence_package_sha256'], digest(first['evidence']))

    def test_evidence_history_is_append_only(self):
        e = self.engine.ingest('a', package())
        with self.store.transaction() as db:
            with self.assertRaises(dbapi.IntegrityError):
                db.execute("UPDATE evidence_versions SET body='{}'")
        with self.store.transaction() as db:
            with self.assertRaises(dbapi.IntegrityError):
                db.execute('DELETE FROM evidence_versions')

    def test_tampered_evidence_version_detected(self):
        if self.store.dialect != 'sqlite':
            self.skipTest('Trigger bypass simulation uses SQLite PRAGMA')
        self.engine.ingest('a', package())
        import sqlite3
        raw = sqlite3.connect(self.store.path)
        raw.execute('DROP TRIGGER evidence_versions_no_update')
        raw.execute("UPDATE evidence_versions SET body=replace(body,'improving','collapsing')")
        raw.commit(); raw.close()
        with self.assertRaisesRegex(ValueError, 'EVIDENCE_RECORD_TAMPERED'):
            self.store.verify()


class TemporalCorrectness(unittest.TestCase):
    """AU lesson: XNYS actual close (EST/EDT, early closes), never a fixed UTC hour."""

    def acquire_for(self, session):
        from backend import market
        bars = {'source': 'TEST', 'retrieved_at': '2026-12-31T00:00:00+00:00', 'session': session, 'adjustment': 'split',
                'bars': [{'session_date': '2026-01-01', 'close': 100}, {'session_date': session, 'close': 101}], 'page_hashes': []}
        with patch.object(market, 'market_bars', return_value=bars), patch.object(market, 'news', return_value=[]):
            return market.acquire('NVDA', session)

    def test_winter_close_is_2100_utc_not_2000(self):
        ev = self.acquire_for('2026-12-15')['evidence'][0]
        self.assertEqual(ev['published_at'], '2026-12-15T21:00:00+00:00')

    def test_summer_close_and_early_close(self):
        self.assertEqual(self.acquire_for('2026-07-15')['evidence'][0]['published_at'], '2026-07-15T20:00:00+00:00')
        self.assertEqual(self.acquire_for('2026-11-27')['evidence'][0]['published_at'], '2026-11-27T18:00:00+00:00')


class Provenance(unittest.TestCase):
    """AU lesson: publisher versus discovery channel; security identity ambiguity fails closed."""

    RSS = b'''<?xml version="1.0"?><rss><channel>
      <item><title>Valero beats refining estimates - Reuters</title><link>https://news.google.com/rss/articles/abc</link>
      <pubDate>%s</pubDate><source url="https://www.reuters.com">Reuters</source></item>
    </channel></rss>'''

    def test_publisher_is_recorded_separately_from_aggregator(self):
        from backend import market
        stamp = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT').encode()
        response = type('R', (), {'status_code': 200, 'content': self.RSS % stamp})()
        with patch.object(market.requests, 'get', return_value=response):
            item = market.news('VLO')[0]
        self.assertEqual(item['publisher'], 'Reuters')
        self.assertEqual(item['publisher_site'], 'https://www.reuters.com')
        self.assertEqual(item['discovered_via'], 'GOOGLE_NEWS_RSS')
        self.assertTrue(item['discovery_url'].startswith('https://news.google.com/'))
        self.assertTrue(item['entity_match'])

    def test_generic_first_words_are_not_identity(self):
        from backend import universe  # noqa: F401  (registers the catalog)
        from backend.market import entity
        # Previously the first word of the name was an alias: "Bank" matched Bank of America.
        self.assertFalse(entity('BAC', 'Bank of England raises rates')['match'])
        self.assertFalse(entity('AMD', 'Advanced Auto Parts shares fall')['match'])
        self.assertFalse(entity('DUK', 'Duke wins in overtime')['match'])
        self.assertFalse(entity('AAPL', 'Apple orchards face drought')['match'])
        self.assertEqual(entity('AAPL', 'Apple orchards face drought')['basis'], 'AMBIGUOUS_COMMON_WORD')
        self.assertTrue(entity('AAPL', 'Apple shares climb after results')['match'])
        self.assertTrue(entity('BAC', 'Bank of America raises dividend')['match'])
        self.assertTrue(entity('JPM', 'Banks to watch: $JPM')['match'])


class Authority(Base):
    def test_rule_approved_after_detection_does_not_authorize_that_case(self):
        failing = Engine(self.store, FakeAI(fail='RESEARCH'))
        e = failing.ingest('a', package())
        with self.assertRaises(ValueError):
            failing.investigate('a', e['id'])
        self.rule(approved_at=(datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat())
        time.sleep(1.1)
        retried = self.engine.investigate('a', e['id'])
        self.assertEqual(retried['status'], 'needs')
        self.assertFalse(retried.get('action'))
        later = self.engine.investigate('a', self.engine.ingest('a', package('VLO'))['id'])
        self.assertEqual(later['status'], 'handled')
        self.assertEqual(later['ruleId'], 'r1')

    def test_headline_only_support_never_uses_standing_rule(self):
        self.rule()
        p = package('VLO')
        p['evidence'][0]['type'] = 'headline_only'
        out = self.engine.investigate('a', self.engine.ingest('a', p)['id'])
        self.assertEqual(out['status'], 'needs')
        self.assertEqual(out['rule_withheld'], ['r1'])
        self.assertFalse(out.get('action'))

    def test_rule_revoked_during_investigation_stops_applying(self):
        r = self.rule()
        engine = self.engine
        class RevokingAI(FakeAI):
            def analyze(inner, stage, event):
                if stage == 'CHALLENGE':
                    engine.rule_action('a', r['id'], 'revoke', 1)
                return FakeAI.analyze(inner, stage, event)
        self.engine.ai = RevokingAI()
        out = self.engine.investigate('a', self.engine.ingest('a', package('VLO'))['id'])
        self.assertEqual(out['status'], 'needs')
        self.assertFalse(out.get('action'))

    def test_correction_creates_no_authority_until_explicit_approval(self):
        e = self.engine.investigate('a', self.engine.ingest('a', package())['id'])
        trace = {'output': {'interpretable': True, 'reason': 'Scoped', 'rule': {'scope': 'opportunity_note', 'sector': 'Energy',
                 'max_position_pct': 3, 'max_sector_pct': 20, 'effect': 'allow', 'expires_days': 30}}}
        proposal = self.engine.propose('a', e['id'], 'Energy notes are fine up to 3%.', trace)
        with self.store.connect() as db:
            self.assertEqual(self.store.all(db, 'a', 'rule'), [])
        self.assertFalse(proposal['candidate']['active'])
        out = self.engine.investigate('a', self.engine.ingest('a', package('VLO'))['id'])
        self.assertEqual(out['status'], 'needs')


class Outcomes(Base):
    def snapshot_event(self, adjustment='split'):
        p = package()
        p['evidence'].append({'id': 'daily', 'type': 'daily_market_data', 'source': 'TEST',
                              'text': json.dumps({'adjustment': adjustment, 'bars': [{'close': 100, 'session_date': SESSION}]})})
        return self.engine.investigate('a', self.engine.ingest('a', p)['id'])

    def later_session(self, days):
        from test_impact_universe import session_after
        return session_after(days)

    def test_adjustment_basis_mismatch_is_not_compared(self):
        from backend.impact import evaluate
        e = self.snapshot_event('unknown')
        out = evaluate(self.store, 'a', e['id'], 7, at=(datetime.now(timezone.utc) + timedelta(days=8)).isoformat(),
                       market_observation={'session': self.later_session(3), 'close': 110, 'adjustment': 'split'})
        self.assertEqual(out['market_performance']['status'], 'not_evaluated')

    def test_split_after_decision_uses_same_retrieval_reference(self):
        from backend.impact import evaluate
        e = self.snapshot_event('split')
        # A 2:1 split after the decision: the provider now reports the reference session at 50.
        out = evaluate(self.store, 'a', e['id'], 7, at=(datetime.now(timezone.utc) + timedelta(days=8)).isoformat(),
                       market_observation={'session': self.later_session(3), 'close': 55, 'adjustment': 'split', 'reference_close_same_retrieval': 50})
        perf = out['market_performance']
        self.assertEqual(perf['status'], 'observed')
        self.assertEqual(perf['close_change_pct'], 10.0)  # not -45%
        self.assertEqual(perf['provider_revision']['decision_time_close'], 100)
        with self.store.connect() as db:
            snap = json.loads(db.execute('SELECT body FROM decision_snapshots WHERE event=?', (e['id'],)).fetchone()[0])
        self.assertEqual(snap['price_reference']['close'], 100)  # original never rewritten

    def test_observation_before_decision_is_rejected(self):
        from backend.impact import evaluate
        e = self.snapshot_event('split')
        out = evaluate(self.store, 'a', e['id'], 7, at=(datetime.now(timezone.utc) + timedelta(days=8)).isoformat(),
                       market_observation={'session': '2026-10-15', 'close': 110, 'adjustment': 'split'})
        self.assertEqual(out['market_performance']['status'], 'not_evaluated')


class Leasing(Base):
    def test_only_one_worker_holds_the_role(self):
        a, b = Worker(self.store, self.engine, owner='A'), Worker(self.store, self.engine, owner='B')
        a.renew(); b.renew()
        self.assertTrue(a.roles['worker']); self.assertFalse(b.roles['worker'])
        self.store.release_lease('worker', 'A'); b.renew()
        self.assertTrue(b.roles['worker'])

    def test_role_lease_expiry_allows_takeover(self):
        self.assertTrue(self.store.acquire_lease('worker', 'A', 0.2))
        self.assertFalse(self.store.acquire_lease('worker', 'B', 30))
        time.sleep(0.3)
        self.assertTrue(self.store.acquire_lease('worker', 'B', 30))

    def test_concurrent_claim_has_exactly_one_winner(self):
        worker = Worker(self.store, self.engine, owner='A')
        id = worker.enqueue('a', 'test', {})
        results = []
        def claim(owner):
            with self.store.transaction() as db:
                results.append(Worker(self.store, self.engine, owner=owner).claim(db, id))
        threads = [threading.Thread(target=claim, args=('W%d' % i,)) for i in range(8)]
        [t.start() for t in threads]; [t.join() for t in threads]
        self.assertEqual(results.count(True), 1)

    def test_recover_never_preempts_a_live_lease(self):
        worker = Worker(self.store, self.engine, owner='A')
        id = worker.enqueue('a', 'test', {})
        with self.store.transaction() as db:
            worker.claim(db, id)
        self.assertEqual(Worker(self.store, self.engine, owner='B').recover(), 0)
        with self.store.transaction() as db:
            db.execute('UPDATE jobs SET lease_expires=? WHERE id=?', (time.time() - 1, id))
        self.assertEqual(Worker(self.store, self.engine, owner='B').recover(), 1)

    def test_fenced_completion_cannot_overwrite_new_owner(self):
        worker = Worker(self.store, self.engine, owner='A')
        id = worker.enqueue('a', 'test', {})
        with self.store.transaction() as db:
            worker.claim(db, id)
            db.execute("UPDATE jobs SET lease_owner='B' WHERE id=?", (id,))  # taken over after expiry
            db.execute("UPDATE jobs SET status='completed',lease_expires=NULL WHERE id=? AND lease_owner=?", (id, 'A'))
            status = db.execute('SELECT status FROM jobs WHERE id=?', (id,)).fetchone()[0]
        self.assertEqual(status, 'running')

    def test_shutdown_requeues_current_job_without_consuming_retry(self):
        worker = Worker(self.store, self.engine, owner='A')
        id = worker.enqueue('a', 'test', {})
        with self.store.transaction() as db:
            worker.claim(db, id)
        worker.current_job = id
        worker.shutdown(0)
        with self.store.connect() as db:
            row = db.execute('SELECT status,attempts,lease_owner FROM jobs WHERE id=?', (id,)).fetchone()
        self.assertEqual((row['status'], row['attempts'], row['lease_owner']), ('queued', 0, None))

    def test_parallel_slots_process_each_job_once(self):
        calls, active, peak, lock = [], [0], [0], threading.Lock()
        class SlowAI(FakeAI):
            def analyze(inner, stage, event):
                with lock:
                    calls.append((event['id'], stage)); active[0] += 1; peak[0] = max(peak[0], active[0])
                try:
                    time.sleep(0.2)
                finally:
                    with lock:
                        active[0] -= 1
                return FakeAI.analyze(inner, stage, event)
        engine = Engine(self.store, SlowAI())
        with patch.dict(os.environ, {'MARKETBELL_WORKER_CONCURRENCY': '4'}):
            worker = Worker(self.store, engine, owner='P')
        with patch.object(Worker, 'execute', lambda w, tenant, kind, payload: engine.investigate(tenant, engine.ingest(tenant, package(payload['s']))['id'])):
            for s in ('PSX', 'VLO', 'NVDA', 'JPM'):
                worker.enqueue('a', 'x', {'s': s})
            worker.start()
            try:
                self.assertTrue(wait_until(lambda: self.done_count() == 4, 20))
            finally:
                worker.shutdown(5)
        self.assertEqual(len(calls), 8)  # 4 cases x (research, challenge), no duplicates
        self.assertEqual(len(set(calls)), 8)
        # Slots really run in parallel. Measured as overlapping model calls, not wall-clock time, which
        # depends on the machine (a busy 2-core CI runner exceeded a fixed time bound).
        self.assertGreaterEqual(peak[0], 2)

    def done_count(self):
        with self.store.connect() as db:
            return db.execute("SELECT count(*) FROM jobs WHERE status='completed'").fetchone()[0]

    def test_scheduled_run_once_drains_and_releases_roles(self):
        worker = Worker(self.store, self.engine, owner='J')
        with patch.object(Worker, 'execute', lambda w, tenant, kind, payload: {}), patch.dict(os.environ, {'MARKETBELL_WATCH': '0', 'MARKETBELL_SCHEDULER': '0'}):
            for i in range(3):
                worker.enqueue('a', 'x', {'i': i})
            result = worker.run_once(30)
        self.assertEqual((result['status'], result['processed']), ('ran', 3))
        self.assertTrue(self.store.acquire_lease('worker', 'next-run', 30))  # roles released for the next run
        busy = Worker(self.store, self.engine, owner='K')
        self.assertEqual(busy.run_once(5)['status'], 'standby')  # a concurrent run never double-processes

    def test_standby_worker_processes_nothing(self):
        active, standby = Worker(self.store, self.engine, owner='A'), Worker(self.store, self.engine, owner='B')
        active.renew(); standby.renew()
        standby.enqueue('a', 'test', {})
        standby.start()
        try:
            time.sleep(1.5)
            with self.store.connect() as db:
                self.assertEqual(db.execute("SELECT status FROM jobs").fetchone()[0], 'queued')
        finally:
            standby.shutdown(5)


class Budgets(Base):
    def test_interactive_use_cannot_exhaust_monitoring_budget(self):
        with patch.dict(os.environ, {'MARKETBELL_INTERACTIVE_DAILY_CALLS': '2'}):
            self.store.reserve_call(10, 'demo-x'); self.store.reserve_call(10, 'demo-y')
            with self.assertRaisesRegex(ValueError, 'INTERACTIVE_MODEL_BUDGET_EXHAUSTED'):
                self.store.reserve_call(10, 'demo-z')
            self.store.reserve_call(10, 'monitor-public')  # system monitoring still has allowance

    def test_budget_exhaustion_defers_holding_notice_visibly(self):
        notice = {'id': 'n1', 'text': 'NVIDIA discloses export restriction', 'published_at': datetime.now(timezone.utc).isoformat(),
                  'source': 'https://news.google.com/x', 'type': 'headline_only', 'entity_match': True}
        worker = Worker(self.store, self.engine, owner='A')
        with patch.dict(os.environ, {'MARKETBELL_DAILY_CALLS': '0'}):
            result = worker.scan_watch(lambda s: [notice] if s == 'NVDA' else [])
        self.assertTrue(result['budgetDeferred'])
        with self.store.connect() as db:
            deferred = self.store.all(db, 'monitor-public', 'deferred_notice')
        self.assertEqual([d['symbol'] for d in deferred], ['NVDA'])
        self.assertEqual(deferred[0]['state'], 'deferred')


class Api(Base):
    def app(self, **env):
        with patch.dict(os.environ, env):
            return Application(self.store, start_worker=False)

    def call(self, app, path='/api/state', method='GET', headers=None, env=None):
        out = {}
        environ = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'HTTP_HOST': '127.0.0.1:4318', 'REMOTE_ADDR': '10.0.0.5',
                   'wsgi.input': io.BytesIO(), **(headers or {})}
        with patch.dict(os.environ, env or {}):
            body = app(environ, lambda s, h: out.update(status=s, headers=h))
        return out['status'], json.loads(body[0])

    def test_client_address_behind_azure_ingress(self):
        environ = {'REMOTE_ADDR': '100.100.0.1', 'HTTP_X_FORWARDED_FOR': '6.6.6.6, 203.0.113.9'}
        with patch.dict(os.environ, {'MARKETBELL_TRUSTED_PROXY_HOPS': '1'}):
            self.assertEqual(Application.client_address(environ), '203.0.113.9')  # spoofed left entry ignored
        with patch.dict(os.environ, {'MARKETBELL_TRUSTED_PROXY_HOPS': '0'}):
            self.assertEqual(Application.client_address(environ), '100.100.0.1')
        with patch.dict(os.environ, {'MARKETBELL_TRUSTED_PROXY_HOPS': '1'}):
            self.assertEqual(Application.client_address({'REMOTE_ADDR': 'x', 'HTTP_X_FORWARDED_FOR': '<script>'}), 'x')

    def test_new_session_limit_is_per_visitor_not_per_proxy(self):
        app = self.app()
        env = {'MARKETBELL_TRUSTED_PROXY_HOPS': '1', 'MARKETBELL_NEW_SESSIONS_PER_HOUR': '2'}
        statuses = [self.call(app, headers={'HTTP_X_FORWARDED_FOR': '198.51.100.%d' % i}, env=env)[0] for i in range(5)]
        self.assertTrue(all(s.startswith('200') for s in statuses))
        same = [self.call(app, headers={'HTTP_X_FORWARDED_FOR': '198.51.100.77'}, env=env)[0] for i in range(3)]
        self.assertTrue(same[-1].startswith('429'))

    def test_stale_monitor_is_not_reported_as_watching(self):
        with self.store.transaction() as db:
            self.store.put(db, 'monitor-public', 'monitor', 'continuous', {'active': True, 'status': 'Watching for new information',
                'intervalSeconds': 300, 'checkedAt': (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()})
        status, state = self.call(self.app())
        self.assertFalse(state['watch']['active'])
        self.assertTrue(state['watch']['stale'])
        self.assertFalse(state['worker']['healthy'])

    def test_readiness_reports_database_outage_as_503(self):
        app = self.app()
        with patch.object(self.store, 'connect', side_effect=dbapi.OperationalError('DATABASE_UNAVAILABLE')):
            status, body = self.call(app, '/healthz')
        self.assertTrue(status.startswith('503'))
        self.assertEqual(body['error'], 'DATABASE_UNAVAILABLE')
        self.assertTrue(self.call(app, '/livez')[0].startswith('200'))

    def test_api_without_in_process_worker(self):
        app = self.app(MARKETBELL_WORKER='0')
        self.assertFalse(app.worker_enabled)
        status, body = self.call(app, '/healthz')
        self.assertFalse(body['worker_in_process'])

    def test_deferred_notice_visible_only_to_holders(self):
        with self.store.transaction() as db:
            self.store.put(db, 'monitor-public', 'deferred_notice', 'NVDA-n1', {'symbol': 'NVDA', 'state': 'deferred', 'message': 'm', 'at': 'x'})
            self.store.put(db, 'monitor-public', 'deferred_notice', 'CVX-n2', {'symbol': 'CVX', 'state': 'deferred', 'message': 'm', 'at': 'x'})
        status, state = self.call(self.app())
        self.assertEqual([d['symbol'] for d in state['deferredNotices']], ['NVDA'])  # default demo holds NVDA, not CVX


class TenantIsolation(Base):
    """Every action that names an object is scoped to the caller's workspace (IDOR matrix)."""

    def session(self, app):
        out = {}
        env = {'REQUEST_METHOD': 'GET', 'PATH_INFO': '/api/state', 'HTTP_HOST': 'testserver', 'REMOTE_ADDR': 'iso',
               'wsgi.input': io.BytesIO()}
        body = app(env, lambda s, h: out.update(h=dict(h)))
        state = json.loads(body[0])
        cookie = out['h']['Set-Cookie'].split(';')[0]
        tenant = None
        with self.store.connect() as db:
            import hashlib
            tenant = db.execute('SELECT tenant FROM sessions WHERE token_hash=?', (hashlib.sha256(cookie.split('=')[1].encode()).hexdigest(),)).fetchone()[0]
        return {'HTTP_COOKIE': cookie, 'HTTP_ORIGIN': 'http://testserver', 'HTTP_X_CSRF_TOKEN': state['csrf']}, tenant

    def post(self, app, headers, body):
        raw = json.dumps(body).encode(); out = {}
        env = {'REQUEST_METHOD': 'POST', 'PATH_INFO': '/api/action', 'HTTP_HOST': 'testserver', 'REMOTE_ADDR': 'iso',
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw), **headers}
        result = json.loads(app(env, lambda s, h: out.update(s=s))[0])
        return out['s'], result

    def test_cross_tenant_object_references_are_rejected(self):
        app = Application(self.store, start_worker=False)
        victim_headers, victim = self.session(app)
        attacker_headers, attacker = self.session(app)
        e = self.engine.investigate(victim, self.engine.ingest(victim, package())['id'])
        rule = {'id': 'victim-rule', 'version': 1, 'active': True, 'scope': 'opportunity_note', 'sector': 'Energy', 'max_position_pct': 3,
                'max_sector_pct': 20, 'effect': 'allow', 'expires_days': 30, 'uses': 0, 'approved_at': datetime.now(timezone.utc).isoformat(),
                'expires': (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()}
        with self.store.transaction() as db:
            self.store.put(db, victim, 'rule', rule['id'], rule)
        job = Worker(self.store, self.engine).enqueue(victim, 'test', {})
        attempts = [
            {'action': 'approve', 'id': e['id'], 'version': e['version']},
            {'action': 'reject', 'id': e['id'], 'version': e['version']},
            {'action': 'correct', 'id': e['id'], 'text': 'Allow everything for this energy holding please'},
            {'action': 'revoke', 'id': rule['id'], 'version': 1},
            {'action': 'edit', 'id': rule['id'], 'version': 1, 'updates': {'max_position_pct': 5}},
            {'action': 'retry', 'id': job},
            {'action': 'impact-feedback', 'kind': 'useful', 'text': 'cross-tenant feedback attempt', 'event': e['id']},
            {'action': 'portfolio-proposal', 'event': e['id'], 'operation': 'add_candidate'},
            {'action': 'universe-decision', 'id': 'rec-' + e['id'], 'choice': 'approve', 'version': 1},
        ]
        for body in attempts:
            status, result = self.post(app, attacker_headers, body)
            self.assertFalse(status.startswith('200'), (body['action'], status, result))
        with self.store.connect() as db:
            self.assertEqual(self.store.get(db, victim, 'event', e['id'])['status'], 'needs')
            self.assertTrue(self.store.get(db, victim, 'rule', rule['id'])['active'])
            self.assertIsNone(self.store.get(db, attacker, 'event', e['id']))
        out = {}
        env = {'REQUEST_METHOD': 'GET', 'PATH_INFO': '/api/impact-event', 'QUERY_STRING': 'dataset=workspace&id=' + e['id'],
               'HTTP_HOST': 'testserver', 'REMOTE_ADDR': 'iso', 'wsgi.input': io.BytesIO(), **attacker_headers}
        app(env, lambda s, h: out.update(s=s))
        self.assertTrue(out['s'].startswith('404'))

    def test_public_demo_discards_personal_holding_details(self):
        app = Application(self.store, start_worker=False)
        headers, tenant = self.session(app)
        with self.store.connect() as db:
            version = self.store.get(db, tenant, 'portfolio', 'main')['version']
        mandate = {'objective': 'Long-term research only', 'horizon': 'long_term', 'excluded_sectors': [], 'excluded_symbols': [],
                   'max_watchlist': 12, 'automatic': {'promote': False, 'deprioritize': False, 'archive': False},
                   'max_position_pct': 5, 'max_sector_pct': 30}
        rows = [{'ticker': 'JPM', 'tier': 'portfolio', 'quantity': 1234, 'cost_basis': 98765, 'position_pct': 3}]
        with patch.dict(os.environ, {'MARKETBELL_PUBLIC_DEMO': '1'}):
            status, result = self.post(app, headers, {'action': 'universe-commit', 'rows': rows, 'mandate': mandate, 'version': version, 'confirmed': True})
        self.assertTrue(status.startswith('200'), result)
        with self.store.connect() as db:
            raw = json.dumps([json.loads(r[0]) for r in db.execute('SELECT body FROM documents WHERE tenant=?', (tenant,))])
        self.assertNotIn('98765', raw)
        self.assertNotIn('1234', raw)


class Webhook(Base):
    def deliver(self, app, text, event_id='ev-1'):
        raw = json.dumps({'symbol': 'NVDA', 'event_id': event_id, 'text': text, 'published_at': datetime.now(timezone.utc).isoformat(),
                          'source': 'Issuer press office'}).encode()
        out = {}
        env = {'REQUEST_METHOD': 'POST', 'PATH_INFO': '/api/webhook', 'HTTP_HOST': 'testserver', 'REMOTE_ADDR': 'w',
               'HTTP_AUTHORIZATION': 'Bearer ' + 'w' * 32, 'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw)}
        with patch.dict(os.environ, {'MARKETBELL_WEBHOOK_TOKEN': 'w' * 32}):
            body = json.loads(app(env, lambda st, h: out.update(s=st))[0])
        return out['s'], body

    def test_duplicate_delivery_is_idempotent_and_revision_is_preserved_separately(self):
        app = Application(self.store, start_worker=False)
        s1, first = self.deliver(app, 'NVIDIA announces a new data-center supply agreement with a cloud provider.')
        s2, again = self.deliver(app, 'NVIDIA announces a new data-center supply agreement with a cloud provider.')
        s3, revised = self.deliver(app, 'NVIDIA announces a revised and smaller data-center supply agreement with a cloud provider.')
        self.assertEqual(first['job'], again['job']); self.assertEqual(again['status'], 'duplicate_ignored')
        self.assertNotEqual(revised['job'], first['job']); self.assertEqual(revised['status'], 'revision_accepted_for_investigation')
        with self.store.connect() as db:
            jobs = {r['id']: json.loads(r['payload']) for r in db.execute("SELECT id,payload FROM jobs WHERE kind='watch-event'")}
        self.assertEqual(len(jobs), 2)
        self.assertIn('smaller', jobs[revised['job']]['notice']['text'])
        self.assertNotIn('smaller', jobs[first['job']]['notice']['text'])  # original delivery unchanged
        self.assertEqual(jobs[revised['job']]['notice']['revision_of'], jobs[first['job']]['notice']['id'])


class AuditAnchor(Base):
    def test_anchor_detects_tail_truncation_that_the_chain_alone_cannot(self):
        from scripts.audit_anchor import run, LocalTarget
        if self.store.dialect != 'sqlite':
            self.skipTest('Trigger bypass simulation uses SQLite')
        self.engine.investigate('a', self.engine.ingest('a', package())['id'])
        where = LocalTarget(Path(self.tmp.name) / 'anchors')
        code, first = run(self.store, where)
        self.assertEqual((code, first['status']), (0, 'ANCHORED'))
        import sqlite3
        raw = sqlite3.connect(self.store.path)
        raw.execute('DROP TRIGGER audit_no_delete')
        raw.execute('DELETE FROM audit WHERE seq>(SELECT max(seq)-3 FROM audit)')  # owner truncates history
        raw.commit(); raw.close()
        self.store.verify()  # the remaining prefix is still a valid chain ...
        code, result = run(self.store, where)  # ... but the external anchor catches it
        self.assertEqual(code, 2)
        self.assertEqual(result['code'], 'ANCHORED_HISTORY_TRUNCATED_OR_REWRITTEN')

    def test_anchor_is_write_once_and_chains(self):
        from scripts.audit_anchor import run, LocalTarget
        where = LocalTarget(Path(self.tmp.name) / 'anchors')
        self.assertEqual(run(self.store, where)[0], 0)
        self.assertEqual(run(self.store, where)[1]['status'], 'VERIFIED_UNCHANGED')
        self.engine.ingest('a', package('VLO'))
        code, second = run(self.store, where)
        self.assertEqual(second['status'], 'ANCHORED')
        self.assertIsNotNone(second['previous'])


class Adversarial(Base):
    """Independent adversarial pass: altered timestamps, duplicates, stale rules, partial failures."""

    def test_webhook_rejects_future_and_naive_timestamps(self):
        app = Application(self.store, start_worker=False)
        for published in ((datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(), '2026-10-18T10:00:00',
                          (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()):
            raw = json.dumps({'symbol': 'NVDA', 'event_id': 'ts', 'text': 'NVIDIA timestamp manipulation attempt for testing.', 'published_at': published}).encode()
            out = {}
            env = {'REQUEST_METHOD': 'POST', 'PATH_INFO': '/api/webhook', 'HTTP_HOST': 'testserver', 'REMOTE_ADDR': 'w', 'HTTP_AUTHORIZATION': 'Bearer ' + 'w' * 32,
                   'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw)}
            with patch.dict(os.environ, {'MARKETBELL_WEBHOOK_TOKEN': 'w' * 32}):
                app(env, lambda st, h: out.update(s=st))
            self.assertFalse(out['s'].startswith('200'), published)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM jobs WHERE kind='watch-event'").fetchone()[0], 0)

    def test_expired_rule_never_applies(self):
        r = self.rule()
        with self.store.transaction() as db:
            r['expires'] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            self.store.put(db, 'a', 'rule', r['id'], r)
        out = self.engine.investigate('a', self.engine.ingest('a', package('VLO'))['id'])
        self.assertEqual(out['status'], 'needs')

    def test_rule_candidate_cannot_be_approved_after_source_changes(self):
        e = self.engine.investigate('a', self.engine.ingest('a', package())['id'])
        trace = {'output': {'interpretable': True, 'reason': 'Scoped', 'rule': {'scope': 'opportunity_note', 'sector': 'Energy',
                 'max_position_pct': 3, 'max_sector_pct': 20, 'effect': 'allow', 'expires_days': 30}}}
        c = self.engine.propose('a', e['id'], 'Energy notes are fine up to 3%.', trace)['candidate']
        self.engine.decide('a', e['id'], 'reject', e['version'])
        with self.assertRaisesRegex(ValueError, 'SOURCE_DECISION_CHANGED'):
            self.engine.approve_rule('a', c['id'])

    def test_stale_session_blocks_late_user_approval(self):
        e = self.engine.investigate('a', self.engine.ingest('a', package())['id'])
        with patch('backend.engine.session_state', return_value={'latest': '2026-10-20'}):
            with self.assertRaisesRegex(ValueError, 'POLICY_OR_REVIEW_BLOCKED'):
                self.engine.decide('a', e['id'], 'approve', e['version'])

    def test_partial_failure_resumes_without_repeating_completed_stage(self):
        class FlakyChallenge(FakeAI):
            failed = False
            def analyze(inner, stage, event):
                inner.calls.append(stage)
                if stage == 'CHALLENGE' and not FlakyChallenge.failed:
                    FlakyChallenge.failed = True
                    raise ValueError('NEBIUS_HTTP_503')
                return {'output': answer(), 'provider': 'TEST ONLY', 'stage': stage}
        ai = FlakyChallenge(); engine = Engine(self.store, ai)
        e = engine.ingest('a', package())
        with self.assertRaises(ValueError):
            engine.investigate('a', e['id'])
        out = engine.investigate('a', e['id'])  # restarted worker retries the job
        self.assertEqual(ai.calls, ['RESEARCH', 'CHALLENGE', 'CHALLENGE'])  # research not re-run
        self.assertEqual(out['status'], 'needs')

    def test_replayed_duplicate_resolution_creates_no_second_note(self):
        self.rule()
        out = self.engine.investigate('a', self.engine.ingest('a', package('VLO'))['id'])
        self.assertEqual(out['status'], 'handled')
        again = self.engine.investigate('a', out['id'])  # duplicate delivery after completion
        self.assertEqual(again['version'], out['version'])
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM documents WHERE tenant='a' AND kind='note'").fetchone()[0], 1)


class LiveCheckCeiling(Base):
    """The live check's $0.05 ceiling is enforced before any provider call, not just documented."""

    def test_script_forces_limits_over_shell_environment(self):
        import subprocess, sys
        env = {**os.environ, 'MARKETBELL_DAILY_USD': '99', 'MARKETBELL_DAILY_CALLS': '500', 'MARKETBELL_INPUT_USD_M': '0.01',
               'MARKETBELL_TEST_PROVIDER_URL': 'http://127.0.0.1:1/x', 'MARKETBELL_DATABASE_URL': 'postgresql://x@y/z'}
        code = ("import os,scripts.live_bounded_check as m;print(os.environ['MARKETBELL_DAILY_USD'],os.environ['MARKETBELL_MONTHLY_USD'],"
                "os.environ['MARKETBELL_DAILY_CALLS'],os.environ['MARKETBELL_INPUT_USD_M'],'MARKETBELL_TEST_PROVIDER_URL' in os.environ,'MARKETBELL_DATABASE_URL' in os.environ)")
        out = subprocess.run([sys.executable, '-B', '-c', code], env=env, capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[1]))
        self.assertEqual(out.stdout.split(), ['0.05', '0.05', '4', '0.3', 'False', 'False'], out.stderr[-500:])

    def test_reservation_refuses_the_call_before_it_is_sent(self):
        from backend.ai import AI, validate_analysis
        sent = []
        class Provider:
            model = 'nvidia/nemotron-test'
            def complete(self, messages, tools):
                sent.append(1)
                return {'content': json.dumps(answer()), 'usage': {'prompt_tokens': 10, 'completion_tokens': 10}}
        ai = AI(self.store, Provider())
        evidence = [{'id': 'e1', 'text': 'Issuer announced earnings call and improving operating margins. ' + 'x' * 60000}]
        limits = {'MARKETBELL_DAILY_USD': '0.05', 'MARKETBELL_MONTHLY_USD': '0.05', 'MARKETBELL_DAILY_CALLS': '4', 'MARKETBELL_EVENT_CALLS': '4',
                  'MARKETBELL_INPUT_USD_M': '0.30', 'MARKETBELL_OUTPUT_USD_M': '0.90', 'MARKETBELL_ALLOW_LIVE': '1'}
        with patch.dict(os.environ, limits):
            for i in range(4):
                try:
                    ai.call('RESEARCH', {'evidence': evidence, 'i': i}, lambda v: validate_analysis(v, evidence), context='c%d' % i)
                except ValueError as error:
                    self.assertEqual(str(error), 'SPEND_BUDGET_EXHAUSTED')
                    break
        with self.store.connect() as db:
            reserved = db.execute('SELECT COALESCE(sum(reserved),0) FROM model_calls').fetchone()[0]
        self.assertLess(len(sent), 4)          # refused before the provider was contacted
        self.assertLessEqual(reserved, 0.05)   # never reserves past the ceiling


class RealPaths(Base):
    """The corrected defects re-tested through the production code paths (worker jobs, news parser)."""

    def test_split_after_decision_through_followup_job(self):
        from backend.vendor.calendar import calendar
        import pandas as pd
        cal = calendar()
        decided = datetime.now(timezone.utc) - timedelta(days=12)
        ref = cal.date_to_session(pd.Timestamp(decided.date()), direction='previous')
        ref_s = str(ref.date())
        p = package()
        p['evidence'].append({'id': 'daily', 'type': 'daily_market_data', 'source': 'Alpaca IEX',
                              'text': json.dumps({'adjustment': 'split', 'bars': [{'close': 200.0, 'session_date': ref_s}]})})
        engine = Engine(self.store, FakeAI(), clock=lambda: decided.isoformat())
        e = engine.investigate('a', engine.ingest('a', p)['id'])
        def bars(symbol, session):
            # Provider history after a 2:1 split: every close halved, including the reference session.
            return {'source': 'Alpaca IEX', 'retrieved_at': datetime.now(timezone.utc).isoformat(), 'adjustment': 'split',
                    'bars': [{'session_date': session, 'close': 110.0 if session != ref_s else 100.0}]}
        worker = Worker(self.store, engine)
        with patch('backend.worker.news', return_value=[]), patch('backend.market.market_bars', side_effect=bars):
            out = worker.execute('a', 'followup', {'event': e['id'], 'horizon': 7})
        perf = out['market_performance']
        self.assertEqual(perf['status'], 'observed')
        self.assertEqual(perf['close_change_pct'], 10.0)  # 100 -> 110 on one basis, not 200 -> 110 (-45%)
        self.assertEqual(perf['provider_revision']['decision_time_close'], 200.0)

    def test_wrong_company_headline_is_screened_not_investigated_via_real_parser(self):
        from backend import market
        stamp = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT').encode()
        rss = (b'<rss><channel><item><title>Bank of England raises rates again - FT</title><link>https://news.google.com/rss/articles/x1</link>'
               b'<pubDate>' + stamp + b'</pubDate><source url="https://www.ft.com">FT</source></item></channel></rss>')
        response = type('R', (), {'status_code': 200, 'content': rss})()
        worker = Worker(self.store, self.engine)
        with patch.object(market.requests, 'get', return_value=response):
            worker.scan_watch(lambda s: market.news('BAC') if s == 'BAC' else [])  # baseline scan
            with patch.object(Worker, 'observed_symbols', return_value=['BAC']):
                worker.scan_watch(lambda s: market.news(s))
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM jobs WHERE kind='watch-event'").fetchone()[0], 0)

    def test_watch_event_records_publication_discovery_and_market_times(self):
        notice = {'id': 'n-times', 'text': 'NVIDIA discloses a new export restriction affecting data-center sales.', 'type': 'headline_only',
                  'published_at': (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(), 'retrieved_at': datetime.now(timezone.utc).isoformat(),
                  'source': 'https://news.google.com/rss/articles/y', 'discovered_via': 'GOOGLE_NEWS_RSS', 'publisher': 'Reuters'}
        worker = Worker(self.store, self.engine)
        with patch('backend.worker.acquire', side_effect=ValueError('MARKET_DATA_CREDENTIALS_UNAVAILABLE')), \
             patch('backend.worker.session_state', return_value={'latest': SESSION}), patch.object(Worker, 'personalize', lambda w, r: None):
            out = worker.execute('monitor-public', 'watch-event', {'symbol': 'NVDA', 'notice': notice})
        with self.store.connect() as db:
            snap = json.loads(db.execute('SELECT body FROM decision_snapshots WHERE event=?', (out['id'],)).fetchone()[0])
        sealed = snap['evidence_sealed'][0]
        self.assertEqual((sealed['published_at'], sealed['retrieved_at'], sealed['publisher']), (notice['published_at'], notice['retrieved_at'], 'Reuters'))
        self.assertFalse(out['policy']['completed_session_fresh'])  # no verified price baseline -> no preparation
        self.assertIn('not an intraday quote', out['trigger'])

    def test_two_started_workers_process_each_job_exactly_once(self):
        executed = []
        lock = threading.Lock()
        def execute(w, tenant, kind, payload):
            with lock:
                executed.append((payload['i'], w.owner))
            time.sleep(0.05)
            return {}
        with patch.object(Worker, 'execute', execute), patch.dict(os.environ, {'MARKETBELL_WORKER_CONCURRENCY': '3', 'MARKETBELL_WATCH': '0', 'MARKETBELL_SCHEDULER': '0'}):
            a, b = Worker(self.store, self.engine, owner='A'), Worker(self.store, self.engine, owner='B')
            for i in range(20):
                a.enqueue('a', 'x', {'i': i})
            if self.store.dialect == 'sqlite':
                a.start(); b.renew(); threading.Thread(target=b.run, daemon=True).start()  # file lock allows one start()
            else:
                a.start(); b.start()
            try:
                self.assertTrue(wait_until(lambda: len(executed) >= 20 and Leasing.done_count(self) == 20, 30))
            finally:
                a.shutdown(5); b.shutdown(5)
        self.assertEqual(sorted(i for i, _ in executed), list(range(20)))  # no job twice
        self.assertEqual({o for _, o in executed}, {'A'})  # the standby never processed


class Configuration(unittest.TestCase):
    def test_developer_paths_disabled_in_production(self):
        from backend import config
        with patch.object(config, 'PRODUCTION', True), patch.dict(os.environ, {}, clear=False):
            os.environ.pop('MARKETBELL_ALPACA_CACHE', None)
            self.assertIsNone(config.developer_path('MARKETBELL_ALPACA_CACHE', 'C:/Users/antho/x'))
        with patch.object(config, 'PRODUCTION', False):
            os.environ.pop('MARKETBELL_ALPACA_CACHE', None)
            self.assertEqual(str(config.developer_path('MARKETBELL_ALPACA_CACHE', 'C:/Users/antho/x')), 'C:/Users/antho/x')

    def test_provider_endpoint_cannot_be_redirected_in_production(self):
        from backend import config
        from backend.vendor import provider
        with patch.dict(os.environ, {'MARKETBELL_TEST_PROVIDER_URL': 'http://127.0.0.1:9/v1'}):
            with patch.object(config, 'PRODUCTION', True):
                self.assertEqual(provider.endpoint(), provider.DEFAULT_ENDPOINT)
            with patch.object(config, 'PRODUCTION', False):
                self.assertEqual(provider.endpoint(), 'http://127.0.0.1:9/v1')
        with patch.dict(os.environ, {'MARKETBELL_TEST_PROVIDER_URL': 'https://attacker.example/v1'}):
            with patch.object(config, 'PRODUCTION', False):
                self.assertEqual(provider.endpoint(), provider.DEFAULT_ENDPOINT)

    def test_structured_logs_drop_sensitive_fields(self):
        from backend import telemetry
        buffer = io.StringIO()
        with patch.dict(os.environ, {'MARKETBELL_STRUCTURED_LOGS': '1'}), patch('sys.stdout', buffer):
            telemetry.emit('x', api_key='secret-value', authorization='Bearer abc', text='private', holdings=[1], ok=1)
        line = json.loads(buffer.getvalue())
        self.assertEqual(line['ok'], 1)
        for k in ('api_key', 'authorization', 'text', 'holdings'):
            self.assertNotIn(k, line)
        self.assertNotIn('secret-value', buffer.getvalue())


class Dialect(unittest.TestCase):
    def test_translation_of_sqlite_constructs(self):
        t = dbapi.translate
        self.assertEqual(t("INSERT OR IGNORE INTO x VALUES(?,?)", True), 'INSERT INTO x VALUES(%s,%s) ON CONFLICT DO NOTHING')
        self.assertIn("json_extract(body,'$.matched')='0'", t("SELECT sum(json_extract(body,'$.matched')=0) FROM d", False))
        self.assertIn("COALESCE(json_extract(body,'$.m'),'1')='1'", t("WHERE COALESCE(json_extract(body,'$.m'),1)=1", False))
        self.assertIn("status='blocked'", t('SELECT sum(status="blocked") FROM t', False))
        self.assertIn("LIKE 'watch-%%'", t("WHERE a=? AND b LIKE 'watch-%'", True))
        self.assertIn('GREATEST(attempts-1,0)', t('SELECT sum(max(attempts-1,0)) FROM jobs', False))


if __name__ == '__main__':
    unittest.main()
