"""Regressions for the build deficiencies found when MarketBell was audited against the Adaptive Universe
(Quant + Nemotron) run-time audit of 2026-10-21. Each test names the lesson it guards."""
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from backend import market, sources
from backend.engine import Engine, RECORDING_PORTFOLIO, default_portfolio
from backend.recordings import load
from backend.store import Store, digest
from backend.worker import Worker
from test_backend import package, answer, FakeAI, SESSION
from test_quiet_sources import headline


def iso(minutes=0):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


class Base(unittest.TestCase):
    def setUp(self):
        sources.health.clear()
        sources._ttl.clear()
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


class DecisionTimeContext(Base):
    """Quant lesson: decision-time context is immutable. Recorded cases must reproduce their recorded
    outcome even after the demo portfolio changed (the "Policy prevents preparation" case had drifted
    to "held" when NVDA moved from 7 % to 5 %)."""

    def test_every_recording_reproduces_its_recorded_outcome(self):
        for name in ('psx', 'vlo', 'risk', 'routine', 'challenge'):
            rec = load(name)
            e = self.engine.ingest('a', rec['package'], 'verified', name)
            result = self.engine.investigate('a', e['id'], rec['traces'], rec['package']['session'])
            original = rec['original_outcome']
            self.assertEqual(result['status'], original['status'] if isinstance(original, dict) else original, name)
            self.assertIn('as it was when recorded', result['portfolio_basis'])

    def test_live_cases_use_the_current_portfolio(self):
        e = self.engine.investigate('a', self.engine.ingest('a', package('NVDA'))['id'])
        self.assertNotIn('portfolio_basis', e)
        self.assertTrue(e['policy']['position_limit'])  # NVDA 5 % in today's portfolio

    def test_untouched_legacy_portfolio_is_upgraded_and_edited_one_is_not(self):
        legacy = {**RECORDING_PORTFOLIO, 'name': 'Isolated demonstration portfolio'}
        edited = {**legacy, 'cash_pct': 30}
        with self.store.transaction() as db:
            self.store.put(db, 'old', 'portfolio', 'main', legacy)
            self.store.put(db, 'mine', 'portfolio', 'main', edited)
        self.engine.ensure('old'); self.engine.ensure('mine')
        with self.store.connect() as db:
            old = self.store.get(db, 'old', 'portfolio', 'main'); mine = self.store.get(db, 'mine', 'portfolio', 'main')
            audit = [r[0] for r in db.execute("SELECT type FROM audit WHERE tenant='old'")]
        self.assertEqual([h['symbol'] for h in old['holdings']], [h['symbol'] for h in default_portfolio()['holdings']])
        self.assertEqual(old['version'], 2)
        self.assertIn('Demo portfolio upgraded', audit)
        self.assertEqual(mine, edited)


class EvidenceHygiene(Base):
    """Quant lessons: identity before research; no ticker-specific code paths."""

    def test_wrong_company_and_routine_items_never_reach_case_evidence(self):
        good = headline('g', 'Nvidia signs supply deal', site='https://www.reuters.com')
        blog = headline('b', 'Is Nvidia a buy?', site='https://247wallst.com', publisher='24/7 Wall St.')
        wrong = dict(headline('w', 'Apple orchard record'), entity_match=False)
        filing = dict(headline('f', 'Form 4'), type='regulatory_filing', routine=True)
        kept = market.evidence_items([blog, wrong, filing, good])
        self.assertEqual([i['id'] for i in kept], ['g', 'b'])  # strongest source first

    def test_acquire_has_no_per_ticker_pages(self):
        self.assertFalse(hasattr(market, 'issuer_events'))
        bars = {'source': 'T', 'retrieved_at': iso(), 'session': SESSION, 'adjustment': 'split',
                'bars': [{'session_date': '2026-10-09', 'close': 100}, {'session_date': SESSION, 'close': 101}], 'page_hashes': []}
        with patch.object(market, 'market_bars', return_value=bars), patch.object(market, 'news', return_value=[headline('g', 'Phillips 66 sets results date')]):
            psx = market.acquire('PSX', SESSION)
        self.assertEqual([e['type'] for e in psx['evidence']], ['daily_market_data', 'headline_only'])

    def test_initial_kind_is_not_hard_coded_per_ticker(self):
        self.assertEqual(self.engine.ingest('a', package('NVDA'))['kind'], 'Pending')


class VisibleConfiguration(Base):
    """Quant lesson: a missing SEC_USER_AGENT silently starved the candidate list for two days."""

    def test_sec_channel_off_is_visible_with_its_reason(self):
        with patch.dict(os.environ, {'SEC_USER_AGENT': ''}):
            sources.gather('NVDA', followed=True, news_fetch=lambda s, query=None: [])
        self.assertEqual(sources.health['sec_edgar']['error'], 'SEC_USER_AGENT_NOT_SET')
        self.assertTrue(sources.health['sec_edgar']['disabled'])
        with patch.dict(os.environ, {'SEC_USER_AGENT': 'MarketBell'}):
            sources.gather('NVDA', followed=True, news_fetch=lambda s, query=None: [])
        self.assertEqual(sources.health['sec_edgar']['error'], 'SEC_USER_AGENT_INVALID')

    def test_missing_price_source_is_a_channel_state(self):
        with patch.object(market, 'market_bars', side_effect=ValueError('MARKET_DATA_CREDENTIALS_UNAVAILABLE')):
            with self.assertRaises(ValueError):
                market.acquire('NVDA', SESSION)
        self.assertEqual(sources.health['price_baseline']['error'], 'MARKET_DATA_CREDENTIALS_UNAVAILABLE')

    def test_one_symbol_failing_is_not_hidden_by_a_later_success(self):
        def news(symbol, query=None):
            if symbol == 'NVDA':
                raise ValueError('NEWS_SOURCE_UNAVAILABLE')
            return []
        with self.assertRaises(ValueError):
            sources.gather('NVDA', news_fetch=news)
        sources.gather('MSFT', news_fetch=news)
        self.assertFalse(sources.health['google_news']['ok'])
        self.assertEqual(sources.health['google_news']['failing'], ['NVDA'])


class Efficiency(Base):
    """Quant lessons T1/T3/T8: verify once, parse once, do not refetch what cannot have changed."""

    def test_slow_channels_are_cached_and_returned_as_copies(self):
        calls = []
        def run():
            calls.append(1); return [{'id': 'x'}]
        first = sources.cached('sec_edgar', 'NVDA', run)
        first[0]['also_reported_by'] = ['mutated']
        second = sources.cached('sec_edgar', 'NVDA', run)
        self.assertEqual(len(calls), 1)
        self.assertNotIn('also_reported_by', second[0])

    def test_preserved_download_is_parsed_once(self):
        folder = Path(self.tmp.name) / 'iex' / 'd1'
        folder.mkdir(parents=True)
        (folder / 'bars.csv').write_text('ticker,date,open,high,low,close,volume\nNVDA,2026-10-09,1,1,1,1,1\nNVDA,2026-10-12,1,1,1,1,1\n')
        calls = []
        original = market._group_bars
        with patch.object(market, '_group_bars', side_effect=lambda p: calls.append(p) or original(p)):
            market._bars_by_symbol(folder / 'bars.csv'); market._bars_by_symbol(folder / 'bars.csv')
        self.assertEqual(len(calls), 1)

    def test_journal_verification_still_catches_a_tampered_trace(self):
        e = self.engine.investigate('a', self.engine.ingest('a', package())['id'])
        with self.store.transaction() as db:
            e['research_trace']['provider'] = 'SOMEONE ELSE'
            self.store.put(db, 'a', 'event', e['id'], e)
        with self.assertRaisesRegex(ValueError, 'MODEL_RECORD_TAMPERED'):
            self.store.verify()

    def test_per_scan_cap_follows_the_cadence(self):
        w = Worker(self.store, self.engine)
        with patch.dict(os.environ, {'MARKETBELL_WATCH_SECONDS': '3600', 'MARKETBELL_SCAN_MAX_NEW': ''}):
            w.scan_watch(lambda s: [])
            items = {s: [headline('h' + s, s + ' wins contract', minutes=1)] for s in ('NVDA', 'MSFT', 'JPM', 'XOM', 'COST', 'LLY')}
            status = w.scan_watch(lambda s: items.get(s, []))
        self.assertEqual(status['newInvestigations'], 6)  # an hourly scan was capped at 1 before

    def test_unreached_notices_expire_visibly(self):
        w = Worker(self.store, self.engine)
        w.scan_watch(lambda s: [])
        with patch.dict(os.environ, {'MARKETBELL_DAILY_CALLS': '0'}):
            w.scan_watch(lambda s: [headline('n1', minutes=1)] if s == 'NVDA' else [])
        with self.store.transaction() as db:
            key = next(r[0] for r in db.execute("SELECT id FROM documents WHERE kind='pending_notice'"))
            n = self.store.get(db, 'monitor-public', 'pending_notice', key)
            n['item']['published_at'] = iso(60 * 24 * 8)
            self.store.put(db, 'monitor-public', 'pending_notice', key, n)
        w.scan_watch(lambda s: [])
        with self.store.connect() as db:
            reasons = {q['reason'] for q in self.store.all(db, 'monitor-public', 'quiet')}
            state = self.store.get(db, 'monitor-public', 'pending_notice', key)['state']
        self.assertIn('NOT_REACHED', reasons)
        self.assertEqual(state, 'expired')

    def test_old_items_are_read_once_not_every_scan(self):
        w = Worker(self.store, self.engine)
        w.scan_watch(lambda s: [])
        old = headline('old', minutes=60 * 24 * 12)
        for _ in range(3):
            w.scan_watch(lambda s: [old] if s == 'NVDA' else [])
        from backend.config import market_day
        with self.store.connect() as db:
            day = self.store.get(db, 'monitor-public', 'brief_day', market_day())
            reasons = [q['reason'] for q in self.store.all(db, 'monitor-public', 'quiet')]
        self.assertEqual(day['read'], 1)
        self.assertEqual(reasons, ['OLD_NEWS'])

    def test_cursor_keeps_the_most_recent_ids_in_order(self):
        w = Worker(self.store, self.engine)
        w.scan_watch(lambda s: [])
        for batch in range(4):
            items = [headline(f'b{batch}-{i}', minutes=1) for i in range(90)]
            w.scan_watch(lambda s, items=items: items if s == 'NVDA' else [])
        with self.store.connect() as db:
            seen = self.store.get(db, 'monitor-public', 'watch_cursor', 'NVDA')['seen']
        self.assertEqual(len(seen), 300)
        self.assertEqual(seen[-1], 'b3-89')
        self.assertNotIn('b0-0', seen)


class FreshDeployment(unittest.TestCase):
    def test_first_scan_follows_the_demo_holdings(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'db.sqlite3')
            w = Worker(store, Engine(store, FakeAI()))
            calls = []
            with patch('backend.sources.gather', side_effect=lambda s, followed=False: calls.append((s, followed)) or []):
                w.scan_watch()
            self.assertTrue(dict(calls)['NVDA'])  # holdings get press releases and filings from the first scan


class Workflow(Base):
    """Quant lessons T4/T5: do not research what cannot change the outcome; stop early."""

    def test_held_research_on_a_discovery_stock_stops_early(self):
        ai = FakeAI(research=answer('hold', 'Opportunity'), challenge=answer('clear'))
        engine = Engine(self.store, ai)
        e = engine.investigate('a', engine.ingest('a', package('CAT'))['id'])
        self.assertEqual(ai.calls, ['RESEARCH'])
        self.assertEqual(e['status'], 'held')
        self.store.verify()

    def test_holdings_and_watchlist_always_get_both_reviews_even_after_a_hold(self):
        for symbol in ('NVDA', 'AMD'):
            ai = FakeAI(research=answer('hold', 'Risk'), challenge=answer('hold', 'Risk'))
            engine = Engine(self.store, ai)
            engine.investigate('a', engine.ingest('a', package(symbol))['id'])
            self.assertEqual(ai.calls, ['RESEARCH', 'CHALLENGE'], symbol)

    def test_discovery_rotation_skips_mandate_exclusions(self):
        with self.store.transaction() as db:
            self.store.put(db, 'monitor-public', 'mandate', 'main', {'excluded_symbols': ['CAT'], 'excluded_sectors': ['Utilities']})
        w = Worker(self.store, self.engine)
        seen = set()
        for _ in range(8):
            seen |= set(w.observed_symbols())
        self.assertNotIn('CAT', seen)
        self.assertFalse({'NEE', 'DUK'} & seen)

    def test_daily_review_covers_followed_stocks_and_spends_ai_only_where_it_matters(self):
        w = Worker(self.store, self.engine)
        def acquire(symbol, session=None):
            move = 3.0 if symbol == 'XOM' else 0.4
            return {'symbol': symbol, 'session': SESSION, 'change': move, 'priority': abs(move), 'trigger': 't', 'source': 'T',
                    'evidence': [{'id': 'p' + symbol, 'type': 'daily_market_data', 'text': 'x', 'published_at': iso(600)}] +
                                ([headline('n' + symbol, symbol + ' contract', minutes=30)] if symbol == 'AMD' else [])}
        with patch('backend.worker.acquire', side_effect=acquire):
            result = w.execute('monitor-public', 'autonomous', {'session': SESSION})
        with self.store.connect() as db:
            investigated = sorted({e['symbol'] for e in self.store.all(db, 'monitor-public', 'event')})
        self.assertEqual(investigated, ['AMD', 'XOM'])  # material move; fresh information
        self.assertIn('MSFT', result['reviewed_by_rule'])
        self.assertNotIn('NVDA', investigated)


if __name__ == '__main__':
    unittest.main()
