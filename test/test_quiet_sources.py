"""Multi-source news, publisher tiers, the quiet log, plain-language blocks, token savings and Ask."""
import hashlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from backend import sources
from backend.app import Application
from backend.ask import validate, ADVICE
from backend.engine import Engine, default_portfolio, plain_policy, substantive
from backend.store import Store
from backend.worker import Worker
from test_backend import package, answer, FakeAI, SESSION


def iso(minutes=0):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def headline(id, text='Nvidia reports record data-center revenue', site='https://www.reuters.com', publisher='Reuters', minutes=1, **extra):
    return sources.classify({'id': id, 'text': text, 'publisher': publisher, 'publisher_site': site, 'published_at': iso(minutes),
        'type': 'headline_only', 'entity_match': True, 'entity_basis': 'name_or_ticker', 'source': 'https://news.google.com/x', **extra})


def submissions(symbol='NVDA', forms=(('8-K', '2.02,9.01', 60), ('4', '', 30), ('10-Q', '', 60 * 24 * 30))):
    recent = {'form': [], 'accessionNumber': [], 'acceptanceDateTime': [], 'primaryDocument': [], 'items': [], 'primaryDocDescription': []}
    for i, (form, items, minutes) in enumerate(forms):
        recent['form'].append(form); recent['accessionNumber'].append(f'0001045810-26-00000{i}')
        recent['acceptanceDateTime'].append((datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M:%S.000Z'))
        recent['primaryDocument'].append(f'doc{i}.htm'); recent['items'].append(items); recent['primaryDocDescription'].append(form)
    return {'tickers': [symbol], 'filings': {'recent': recent}}


class Base(unittest.TestCase):
    def setUp(self):
        sources.health.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'db.sqlite3')
        self.ai = FakeAI()
        self.engine = Engine(self.store, self.ai)
        self.engine.ensure('a')
        self.engine.ensure('monitor-public')
        self.calendar = patch('backend.engine.session_state', return_value={'latest': SESSION})
        self.calendar.start()

    def tearDown(self):
        self.calendar.stop()
        self.tmp.cleanup()


class Tiers(Base):
    def test_tier_comes_from_publisher_domain_not_display_name(self):
        self.assertEqual(headline('a', site='https://www.businesswire.com')['source_tier'], 1)
        self.assertEqual(headline('b', site='https://www.reuters.com')['source_tier'], 2)
        self.assertEqual(headline('c', site='https://247wallst.com', publisher='24/7 Wall St.')['source_tier'], 3)
        self.assertEqual(headline('d', site='https://reuters.com.evil.example', publisher='Reuters')['source_tier'], 3)
        self.assertEqual(headline('e', site=None)['source_tier'], 3)

    def test_strong_headline_counts_as_substantive_commentary_does_not(self):
        def event(tier_site):
            item = headline('e1', site=tier_site)
            review = {'claims': [{'evidence_id': 'e1', 'quote': 'record'}]}
            return {'evidence': [item], 'research': review, 'challenge': review}
        self.assertTrue(substantive(event('https://www.reuters.com')))
        self.assertTrue(substantive(event('https://www.prnewswire.com')))
        self.assertFalse(substantive(event('https://www.fool.com')))
        price = {'evidence': [{'id': 'p', 'type': 'daily_market_data', 'source_tier': 1}], 'research': {'claims': [{'evidence_id': 'p'}]}, 'challenge': {'claims': [{'evidence_id': 'p'}]}}
        self.assertFalse(substantive(price))


class Edgar(Base):
    def test_recent_filings_are_tier_one_and_routine_forms_are_flagged(self):
        items = sources.edgar('NVDA', fetch=lambda url: submissions())
        self.assertEqual([i['form'] for i in items], ['8-K', '4'])  # 30-day-old 10-Q outside the 7-day window
        eightk = items[0]
        self.assertEqual(eightk['source_tier'], 1)
        self.assertEqual(eightk['type'], 'regulatory_filing')
        self.assertIn('Item 2.02 (results of operations and financial condition)', eightk['text'])
        self.assertTrue(eightk['source'].startswith('https://www.sec.gov/Archives/edgar/data/1045810/'))
        self.assertFalse(eightk['routine'])
        self.assertTrue(items[1]['routine'])

    def test_identity_mismatch_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'SEC_IDENTITY_MISMATCH'):
            sources.edgar('NVDA', fetch=lambda url: submissions('AMD'))

    def test_user_agent_must_be_a_contact(self):
        with patch.dict(os.environ, {'SEC_USER_AGENT': 'MarketBell'}):
            self.assertIsNone(sources.sec_agent())
        with patch.dict(os.environ, {'SEC_USER_AGENT': 'MarketBell research ops@example.com'}):
            self.assertTrue(sources.sec_agent())
        with patch.dict(os.environ, {'SEC_USER_AGENT': ''}):
            with self.assertRaisesRegex(ValueError, 'SEC_USER_AGENT_NOT_SET'):
                sources.sec_get('https://data.sec.gov/x')


class Gather(Base):
    def test_one_channel_failing_never_hides_the_others(self):
        def news(symbol, query=None):
            if query:
                raise ValueError('NEWS_SOURCE_UNAVAILABLE')
            return [headline('g1')]
        items = sources.gather('NVDA', followed=True, news_fetch=news, sec_fetch=lambda url: submissions())
        self.assertEqual({i['discovered_via'] if 'discovered_via' in i else 'news' for i in items} >= {'SEC_EDGAR'}, True)
        self.assertFalse(sources.health['press_wires']['ok'])
        self.assertTrue(sources.health['sec_edgar']['ok'])

    def test_all_channels_failing_is_an_outage_not_silence(self):
        def down(symbol, query=None):
            raise ValueError('NEWS_SOURCE_UNAVAILABLE')
        with self.assertRaisesRegex(ValueError, 'ALL_SOURCES_UNAVAILABLE'):
            sources.gather('NVDA', news_fetch=down)

    def test_same_story_kept_once_as_the_strongest_source(self):
        def news(symbol, query=None):
            if query:
                return [headline('same', site='https://www.businesswire.com', publisher='Business Wire')]
            return [headline('same', site='https://www.fool.com', publisher='The Motley Fool')]
        items = sources.gather('NVDA', followed=True, news_fetch=news)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['publisher'], 'Business Wire')
        self.assertEqual(items[0]['also_reported_by'], ['The Motley Fool'])

    def test_discovery_pool_stocks_get_general_news_only(self):
        queries = []
        def news(symbol, query=None):
            queries.append(query); return []
        sources.gather('CAT', followed=False, news_fetch=news, sec_fetch=lambda url: self.fail('SEC called for unfollowed stock'))
        self.assertEqual(queries, [None])


class QuietLog(Base):
    def scan(self, worker, items_for):
        return worker.scan_watch(lambda s: items_for.get(s, []))

    def quiet(self):
        with self.store.connect() as db:
            return {q['reason']: q for q in self.store.all(db, 'monitor-public', 'quiet')}

    def jobs(self):
        with self.store.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM jobs WHERE kind='watch-event'")]

    def test_triage_sets_aside_commentary_and_explains_it_in_plain_language(self):
        w = Worker(self.store, self.engine)
        self.scan(w, {})  # baseline cursors for every observed symbol
        commentary = headline('c1', 'Is Nvidia stock a buy before earnings?', site='https://247wallst.com', publisher='24/7 Wall St.')
        news = headline('n1', 'Nvidia wins large sovereign AI contract')
        wrong = headline('w1', 'Lilly pads bloom early in the city pond', site='https://www.reuters.com')
        wrong['entity_match'] = False; wrong['entity_basis'] = 'AMBIGUOUS_COMMON_WORD'
        with patch.dict(os.environ, {'MARKETBELL_SCAN_MAX_NEW': '5'}):
            status = self.scan(w, {'NVDA': [commentary, news], 'LLY': [wrong]})
        quiet = self.quiet()
        self.assertIn('COMMENTARY_ONLY', quiet)
        self.assertIn('24/7 Wall St.', quiet['COMMENTARY_ONLY']['plain'])
        self.assertIn('AMBIGUOUS_NAME', quiet)
        self.assertEqual([j['notice']['id'] for j in self.jobs()], ['n1'])  # only the newsroom story costs tokens
        self.assertEqual(status['newInvestigations'], 1)
        with self.store.connect() as db:
            from backend.config import market_day
            day = self.store.get(db, 'monitor-public', 'brief_day', market_day())
        self.assertEqual((day['read'], day['quiet'], day['investigated']), (3, 2, 1))
        self.scan(w, {'NVDA': [commentary, news]})  # re-seen items are not logged twice
        self.assertEqual(len(self.quiet()), 2)

    def test_news_before_monitoring_and_routine_filings_are_logged_not_investigated(self):
        w = Worker(self.store, self.engine)
        old = headline('o1', minutes=120)
        self.scan(w, {'NVDA': [old]})
        self.assertIn('BEFORE_MONITORING', self.quiet())
        filing = sources.edgar('NVDA', fetch=lambda url: submissions(forms=(('4', '', 1),)))[0]
        self.scan(w, {'NVDA': [filing]})
        self.assertIn('ROUTINE_FILING', self.quiet())
        self.assertEqual(self.jobs(), [])

    def test_material_filing_is_investigated(self):
        w = Worker(self.store, self.engine)
        self.scan(w, {})
        filing = sources.edgar('NVDA', fetch=lambda url: submissions(forms=(('8-K', '2.02', 1),)))[0]
        self.scan(w, {'NVDA': [filing]})
        self.assertEqual([j['notice']['type'] for j in self.jobs()], ['regulatory_filing'])

    def test_trusted_publisher_is_investigated_next_time(self):
        w = Worker(self.store, self.engine)
        self.scan(w, {})
        with self.store.transaction() as db:
            from backend.store import digest
            self.store.put(db, 'monitor-public', 'trusted_publisher', digest({'symbol': 'NVDA', 'publisher': '24/7 Wall St.'})[:24], {'symbol': 'NVDA', 'publisher': '24/7 Wall St.'})
        self.scan(w, {'NVDA': [headline('c2', site='https://247wallst.com', publisher='24/7 Wall St.')]})
        self.assertEqual([j['notice']['id'] for j in self.jobs()], ['c2'])


class App(Base):
    def session(self, app):
        out = {}
        env = {'REQUEST_METHOD': 'GET', 'PATH_INFO': '/api/state', 'HTTP_HOST': 'testserver', 'REMOTE_ADDR': 'q', 'wsgi.input': io.BytesIO()}
        state = json.loads(app(env, lambda s, h: out.update(h=dict(h)))[0])
        cookie = out['h']['Set-Cookie'].split(';')[0]
        return {'HTTP_COOKIE': cookie, 'HTTP_ORIGIN': 'http://testserver', 'HTTP_X_CSRF_TOKEN': state['csrf']}, state

    def state(self, app, headers):
        env = {'REQUEST_METHOD': 'GET', 'PATH_INFO': '/api/state', 'HTTP_HOST': 'testserver', 'REMOTE_ADDR': 'q', 'wsgi.input': io.BytesIO(), **headers}
        return json.loads(app(env, lambda s, h: None)[0])

    def post(self, app, headers, body):
        raw = json.dumps(body).encode(); out = {}
        env = {'REQUEST_METHOD': 'POST', 'PATH_INFO': '/api/action', 'HTTP_HOST': 'testserver', 'REMOTE_ADDR': 'q',
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw), **headers}
        result = json.loads(app(env, lambda s, h: out.update(s=s))[0])
        return out['s'], result

    def seed_quiet(self):
        w = Worker(self.store, self.engine)
        w.scan_watch(lambda s: [])
        w.scan_watch(lambda s: [headline('c1', 'Is Nvidia stock a buy?', site='https://247wallst.com', publisher='24/7 Wall St.')] if s == 'NVDA' else [])
        with self.store.connect() as db:
            return self.store.all(db, 'monitor-public', 'quiet')[0]

    def test_state_has_morning_brief_and_quiet_log(self):
        q = self.seed_quiet()
        with patch.dict(os.environ, {'MARKETBELL_ALLOW_LIVE': '0'}):
            app = Application(self.store, start_worker=False)
            headers, state = self.session(app)
        self.assertIn('Today I read 1 new item, set aside 1, investigated 0', state['brief']['sentence'])
        self.assertEqual(state['quiet'][0]['id'], q['id'])
        self.assertTrue(state['quiet'][0]['followed'])
        self.assertNotIn('item', state['quiet'][0])

    def test_overturn_in_public_demo_stays_in_the_visitors_workspace(self):
        q = self.seed_quiet()
        with patch.dict(os.environ, {'MARKETBELL_ALLOW_LIVE': '0', 'MARKETBELL_PUBLIC_DEMO': '1'}):
            app = Application(self.store, start_worker=False)
            headers, _ = self.session(app)
            status, result = self.post(app, headers, {'action': 'quiet-overturn', 'id': q['id']})
            state = self.state(app, headers)
        self.assertTrue(status.startswith('200'), result)
        self.assertTrue(state['quiet'][0]['overturned'])
        with self.store.connect() as db:
            self.assertEqual(self.store.all(db, 'monitor-public', 'trusted_publisher'), [])

    def test_overturn_in_personal_use_teaches_triage(self):
        q = self.seed_quiet()
        with patch.dict(os.environ, {'MARKETBELL_ALLOW_LIVE': '0', 'MARKETBELL_PUBLIC_DEMO': '0'}):
            app = Application(self.store, start_worker=False)
            headers, _ = self.session(app)
            status, result = self.post(app, headers, {'action': 'quiet-overturn', 'id': q['id']})
        self.assertIn('24/7 Wall St.', result['learned'])
        with self.store.connect() as db:
            self.assertEqual(len(self.store.all(db, 'monitor-public', 'trusted_publisher')), 1)

    def test_overturn_unknown_item_rejected(self):
        app = Application(self.store, start_worker=False)
        headers, _ = self.session(app)
        status, result = self.post(app, headers, {'action': 'quiet-overturn', 'id': 'nope'})
        self.assertTrue(status.startswith('4'))

    def test_ask_from_records_without_a_model(self):
        self.seed_quiet()
        with patch.dict(os.environ, {'MARKETBELL_ALLOW_LIVE': '0'}):
            app = Application(self.store, start_worker=False)
            headers, _ = self.session(app)
            status, result = self.post(app, headers, {'action': 'ask', 'question': 'Why were you quiet about NVDA today?'})
            state = self.state(app, headers)
        self.assertEqual(result['answer']['mode'], 'records')
        self.assertIn('commentary only', result['answer']['answer'])
        self.assertEqual(state['answers'][0]['question'], 'Why were you quiet about NVDA today?')


class PlainAndSavings(Base):
    def test_blocked_reason_is_one_actionable_sentence(self):
        p = default_portfolio()
        text = plain_policy({'symbol': 'NVDA', 'position': 7, 'sector': 'Technology'}, p, {'position_limit': False, 'completed_session_fresh': False})
        self.assertIn('NVDA would be 7% of your portfolio, above your 5% per-stock limit', text)
        self.assertIn('no verified price baseline', text)
        self.assertTrue(text.startswith("I didn't prepare a note on NVDA"))

    def test_routine_discovery_stock_skips_the_second_review(self):
        ai = FakeAI(research=answer('no_action', 'Routine'), challenge=answer('clear'))
        engine = Engine(self.store, ai)
        e = engine.investigate('a', engine.ingest('a', package('CAT'))['id'])
        self.assertEqual(ai.calls, ['RESEARCH'])
        self.assertTrue(e['challenge']['skipped'])
        self.assertNotEqual(e['status'], 'needs')
        self.assertFalse(e.get('action'))
        self.store.verify()

    def test_held_or_watched_stocks_always_get_both_reviews(self):
        for symbol in ('NVDA', 'PSX'):
            ai = FakeAI(research=answer('no_action', 'Routine'), challenge=answer('no_action', 'Routine'))
            engine = Engine(self.store, ai)
            engine.investigate('a', engine.ingest('a', package(symbol))['id'])
            self.assertEqual(ai.calls, ['RESEARCH', 'CHALLENGE'], symbol)

    def test_skip_can_be_turned_off(self):
        ai = FakeAI(research=answer('no_action', 'Routine'), challenge=answer('no_action', 'Routine'))
        engine = Engine(self.store, ai)
        with patch.dict(os.environ, {'MARKETBELL_SKIP_ROUTINE_CHALLENGE': '0'}):
            engine.investigate('a', engine.ingest('a', package('CAT'))['id'])
        self.assertEqual(ai.calls, ['RESEARCH', 'CHALLENGE'])


class AskValidation(unittest.TestCase):
    items = [{'id': 'quiet-1', 'text': 'NVDA set aside (COMMENTARY_ONLY): "Is Nvidia a buy?" — Commentary.'}]

    def test_advice_is_refused(self):
        for text in ('You should consider buying more NVDA now.', 'I recommend selling NVDA.', 'This is a strong buy signal.'):
            self.assertTrue(ADVICE.search(text), text)
            with self.assertRaisesRegex(ValueError, 'ASK_ADVICE_REFUSED'):
                validate({'answer': text, 'uncertainty': '', 'claims': [{'evidence_id': 'quiet-1', 'quote': 'COMMENTARY_ONLY'}]}, self.items)

    def test_answers_must_quote_records(self):
        with self.assertRaisesRegex(ValueError, 'ASK_CITATION_NOT_GROUNDED'):
            validate({'answer': 'I set aside a commentary piece.', 'uncertainty': '', 'claims': [{'evidence_id': 'quiet-1', 'quote': 'invented words here'}]}, self.items)
        with self.assertRaisesRegex(ValueError, 'ASK_CITATIONS_REQUIRED'):
            validate({'answer': 'I set aside a commentary piece.', 'uncertainty': '', 'claims': []}, self.items)
        ok = validate({'answer': 'I set aside a commentary piece about NVDA.', 'uncertainty': 'Article not read.', 'claims': [{'evidence_id': 'quiet-1', 'quote': 'COMMENTARY_ONLY'}]}, self.items)
        self.assertEqual(ok['claims'][0]['evidence_id'], 'quiet-1')


if __name__ == '__main__':
    unittest.main()
