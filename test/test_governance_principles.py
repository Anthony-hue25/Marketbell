"""Focused checks for the MarketBell operating principles (docs: MARKETBELL_OPERATING_PRINCIPLES.md).

Each class maps to one validation requirement of the governance update. They use the existing fakes; no
network, no model calls, no broker code.
"""
import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from backend.store import Store
from backend.engine import Engine, approved_before, checks, default_portfolio
from backend.ai import AI, DISCIPLINE, validate_analysis
from backend.explain import explain, operations, decision_right, stage, STALE_DAYS
from backend.app import Application
from test_backend import package, answer, FakeAI, SESSION


def iso(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


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

    def rule(self, sector='Energy', **extra):
        r = {'id': 'r1', 'version': 1, 'active': True, 'revoked': False, 'scope': 'opportunity_note', 'sector': sector,
             'max_position_pct': 3, 'max_sector_pct': 20, 'effect': 'allow', 'expires_days': 30, 'uses': 0,
             'approved_at': iso(days=-1), 'expires': iso(days=30), 'text': 'test rule', **extra}
        with self.store.transaction() as db:
            self.store.put(db, 'a', 'rule', r['id'], r)
        return r

    def run_case(self, p=None, engine=None):
        engine = engine or self.engine
        return engine.investigate('a', engine.ingest('a', p or package('VLO'))['id'])

    def note(self, id):
        with self.store.connect() as db:
            return self.store.get(db, 'a', 'note', id)


class ResearchCannotBypassPolicy(Base):
    def test_clear_reviews_and_matching_rule_still_blocked_by_hard_policy(self):
        self.rule()
        with self.store.transaction() as db:
            p = default_portfolio(); p['cash_pct'] = 0
            self.store.put(db, 'a', 'portfolio', 'main', p)
        out = self.run_case()
        self.assertEqual(out['status'], 'blocked')
        self.assertIsNone(self.note(out['id']))
        self.assertEqual(explain(out)['decision_right'], 'BLOCKED')

    def test_model_cannot_add_an_action_field(self):
        bad = {**answer(), 'action': 'order executed'}
        with self.assertRaisesRegex(ValueError, 'MODEL_SCHEMA_INVALID'):
            validate_analysis(bad, package()['evidence'])


class EvidenceLabelling(Base):
    def stale_package(self):
        p = package('VLO')
        p['evidence'][0]['published_at'] = iso(days=-(STALE_DAYS + 20))
        return p

    def test_stale_evidence_never_carries_standing_rule_authority(self):
        self.rule()
        out = self.run_case(self.stale_package())
        self.assertEqual(out['status'], 'needs')
        self.assertEqual(out['rule_withheld'], ['r1'])
        self.assertIn('days old', out['rule_withheld_reason'])
        self.assertIsNone(self.note(out['id']))
        x = explain(out)
        self.assertEqual(x['evidence']['cited_freshness'], 'stale')
        self.assertTrue(any('days old' in c for c in x['cautions']))
        with self.store.connect() as db:
            ev = self.store.get(db, 'a', 'rule_evaluation', 'r1-' + out['id'] + '-1')
        self.assertFalse(ev['evidence_current'])

    def test_fresh_evidence_still_uses_rule(self):
        self.rule()
        out = self.run_case()
        self.assertEqual(out['status'], 'handled')
        self.assertEqual(explain(out)['evidence']['cited_freshness'], 'fresh')

    def test_headline_only_labelled_insufficient(self):
        self.rule()
        p = package('VLO'); p['evidence'][0]['type'] = 'headline_only'
        out = self.run_case(p)
        self.assertEqual(out['evidence_sufficiency'], 'headline or price data only')
        self.assertIn('too thin', explain(out)['why_authority'])
        self.assertTrue(any('not demonstrated profitability' in c for c in explain(out)['cautions']))

    def test_failed_investigation_is_not_presented_as_research(self):
        engine = Engine(self.store, FakeAI(fail='RESEARCH'))
        e = engine.ingest('a', package())
        with self.assertRaises(ValueError):
            engine.investigate('a', e['id'])
        with self.store.connect() as db:
            failed = self.store.get(db, 'a', 'event', e['id'])
        x = explain(failed)
        self.assertEqual(x['decision_right'], 'NOT_VERIFIED')
        self.assertEqual(x['stage'], 'not_verified')
        self.assertIsNone(failed['research'])


class RecommendationIsNotAction(Base):
    def test_cleared_recommendation_is_only_proposed(self):
        claim = {**answer(), 'summary': 'Order executed and position bought; note prepared for the owner.'}
        out = self.run_case(engine=Engine(self.store, FakeAI(research=claim, challenge=claim)))
        self.assertEqual(out['status'], 'needs')
        self.assertFalse(out.get('action'))
        self.assertIsNone(self.note(out['id']))
        x = explain(out)
        self.assertEqual(x['stage'], 'proposed')
        self.assertIn('Not executed', x['stage_label'])
        self.assertEqual(x['why_it_matters']['kind'], 'model_interpretation')

    def test_completed_only_with_verified_readback(self):
        e = {'status': 'handled', 'kind': 'Opportunity', 'action': {'type': 'prepare_note', 'verified': False}, 'evidence': []}
        self.assertNotEqual(stage(e)[0], 'completed_verified')
        e['action']['verified'] = True
        self.assertEqual(stage(e)[0], 'completed_verified')

    def test_owner_approval_recorded_as_owner_decision(self):
        out = self.run_case()
        done = self.engine.decide('a', out['id'], 'approve', out['version'])
        x = explain(done)
        self.assertEqual(x['decision_right'], 'OWNER_REVIEW')
        self.assertIn('Approved by you', x['stage_label'])


class RulesNeverWidenImplicitly(Base):
    def test_one_off_approval_does_not_cover_next_case(self):
        first = self.run_case(package('PSX'))
        self.engine.decide('a', first['id'], 'approve', first['version'])
        second = self.run_case(package('VLO'))
        self.assertEqual(second['status'], 'needs')
        with self.store.connect() as db:
            self.assertEqual(self.store.all(db, 'a', 'rule'), [])

    def test_widening_edit_does_not_reach_back_to_earlier_case(self):
        self.rule(sector='Technology')
        failing = Engine(self.store, FakeAI(fail='RESEARCH'))
        e = failing.ingest('a', package('VLO'))
        with self.assertRaises(ValueError):
            failing.investigate('a', e['id'])
        time.sleep(0.02)
        self.engine.rule_action('a', 'r1', 'edit', 1, {'sector': 'Any'})
        retried = self.engine.investigate('a', e['id'])
        self.assertEqual(retried['status'], 'needs')
        self.assertFalse(retried.get('action'))
        later = self.run_case(package('PSX'))
        self.assertEqual(later['status'], 'handled')      # explicit edit applies going forward

    def test_resume_does_not_authorize_case_detected_while_paused(self):
        self.rule()
        self.engine.rule_action('a', 'r1', 'pause', 1)
        failing = Engine(self.store, FakeAI(fail='RESEARCH'))
        e = failing.ingest('a', package('VLO'))
        with self.assertRaises(ValueError):
            failing.investigate('a', e['id'])
        time.sleep(0.02)
        self.engine.rule_action('a', 'r1', 'resume', 2)
        self.assertEqual(self.engine.investigate('a', e['id'])['status'], 'needs')

    def test_effective_from_governs_timing(self):
        r = {'approved_at': iso(days=-5), 'effective_from': iso(days=-1)}
        self.assertFalse(approved_before(r, {'created': iso(days=-2)}))
        self.assertTrue(approved_before(r, {'created': iso(hours=-1)}))
        self.assertFalse(approved_before({'effective_from': iso(days=-3)}, {'created': iso()}))  # never approved


class CorrectionsTraceable(Base):
    def test_correction_to_rule_to_application_chain(self):
        source = self.run_case(package('PSX'))
        trace = {'output': {'interpretable': True, 'reason': 'Scoped to Energy notes', 'rule': {'scope': 'opportunity_note', 'sector': 'Energy',
                 'max_position_pct': 3, 'max_sector_pct': 20, 'effect': 'allow', 'expires_days': 30}}}
        text = 'Energy research notes are fine up to 3%.'
        proposal = self.engine.propose('a', source['id'], text, trace)
        rule = self.engine.approve_rule('a', proposal['candidate']['id'])
        self.assertEqual((rule['source'], rule['correction'], rule['source_version']), (source['id'], text, source['version']))
        self.assertTrue(rule['approved_at'] and rule['effective_from'])
        with self.store.connect() as db:
            pref = self.store.get(db, 'a', 'preference', source['id'])
            types = [r[0] for r in db.execute("SELECT type FROM audit WHERE tenant='a' ORDER BY seq")]
        self.assertEqual((pref['text'], pref['grants_authority']), (text, False))
        self.assertLess(types.index('Correction interpreted'), types.index('Standing rule approved'))
        time.sleep(0.02)
        applied = self.run_case(package('VLO'))
        self.assertEqual(applied['ruleId'], rule['id'])
        lines = explain(applied)['rules']
        self.assertTrue(lines[0].startswith('Applied: standing rule ' + rule['id'][:8]))
        self.assertIn('Energy', lines[0])


class ProviderAndPolicyReplaceable(Base):
    class Provider:
        model = 'test-nemotron'
        def __init__(self):
            self.messages = []
        def complete(self, messages, tools):
            self.messages.append(messages)
            return {'content': json.dumps(answer()), 'usage': {'prompt_tokens': 1, 'completion_tokens': 1}}

    def test_research_and_challenge_flow_through_a_replacement_provider(self):
        provider = self.Provider()
        engine = Engine(self.store, AI(self.store, provider=provider))
        with patch.dict('os.environ', {'MARKETBELL_ALLOW_LIVE': '1'}):
            out = self.run_case(package('PSX'), engine)
        self.assertEqual(out['status'], 'needs')
        self.assertEqual(len(provider.messages), 2)
        research, challenge = provider.messages
        self.assertIn(DISCIPLINE, research[0]['content'])
        self.assertIn(DISCIPLINE, challenge[0]['content'])
        self.assertIn('You have no Research conclusion', challenge[0]['content'])
        self.assertNotIn('"research"', challenge[1]['content'])   # blind challenge: no Research verdict supplied

    def test_policy_module_replaceable_without_touching_research(self):
        strict = lambda event, portfolio, expected, mandate=None: {**checks(event, portfolio, expected, mandate), 'owner_policy': False}
        engine = Engine(self.store, self.ai, policy=strict)
        self.rule()
        out = self.run_case(engine=engine)
        self.assertEqual(out['status'], 'blocked')
        self.assertIn('owner_policy', out['blocked_checks'])
        self.assertEqual(self.ai.calls, ['RESEARCH', 'CHALLENGE'])
        self.assertEqual(self.run_case(package('PSX'))['status'], 'handled')   # default engine unaffected


class ExplanationAndVisibility(Base):
    def test_routine_case_gets_brief_explanation(self):
        quiet = answer('no_action', 'Routine')
        out = self.run_case(engine=Engine(self.store, FakeAI(research=quiet, challenge=quiet)))
        x = explain(out)
        self.assertEqual((x['level'], x['decision_right']), ('brief', 'AUTO_HANDLE'))
        self.assertNotIn('cautions', x)

    def test_full_explanation_fields_and_no_confidence_score(self):
        out = self.run_case()
        x = explain(out)
        for k in ('what_happened', 'why_it_matters', 'evidence', 'uncertainty', 'downside', 'cautions', 'rules',
                  'proposed_action', 'decision_right', 'why_authority', 'outcome_to_check', 'note'):
            self.assertIn(k, x)
        self.assertNotIn('confidence', json.dumps(x).lower())
        self.assertIn('not evidence that the research is right', x['note'])

    def test_concentration_and_sector_overlap_cautions(self):
        with self.store.transaction() as db:
            p = default_portfolio(); p['watchlist'].append('XOM'); p['sector_exposure']['Energy'] = 18
            self.store.put(db, 'a', 'portfolio', 'main', p)
        out = self.run_case(package('XOM'))
        cautions = ' '.join(explain(out)['cautions'])
        self.assertIn('already hold XOM', cautions)
        self.assertIn('both held and on your watchlist', cautions)
        self.assertIn('Energy is already 18%', cautions)

    def test_operation_counts_come_from_records(self):
        events = [{'status': 'handled', 'ruleId': 'r', 'action': {'verified': True}}, {'status': 'handled'},
                  {'status': 'needs'}, {'status': 'rejected', 'user_involved': True}, {'status': 'blocked'},
                  {'status': 'held'}, {'status': 'queued'}, {'status': 'failed'}]
        o = operations(events, deferred=2, verified_outcomes=1)
        self.assertEqual(o, {'handled_without_you': 2, 'under_standing_rule': 1, 'needs_you': 1, 'decided_by_you': 1, 'rejected': 1,
                             'blocked': 1, 'monitoring': 1, 'awaiting_evidence': 3, 'not_verified': 1, 'verified_outcomes': 1})

    def test_state_api_exposes_explanations_and_counts(self):
        monitor = Engine(self.store, FakeAI())
        monitor.investigate('monitor-public', monitor.ingest('monitor-public', package('PSX'))['id'])
        app = Application(self.store, False)
        env = {'PATH_INFO': '/api/state', 'REQUEST_METHOD': 'GET', 'HTTP_HOST': 'testserver', 'REMOTE_ADDR': 'test'}
        body = json.loads(b''.join(app(env, lambda s, h: None)))
        self.assertIn('operations', body)
        self.assertFalse(body['executionEnabled'])
        events = body['monitored']['events']
        self.assertTrue(events and all('explanation' in e for e in events))
        self.assertEqual(body['operations']['needs_you'], sum(1 for e in events if e['status'] == 'needs'))


if __name__ == '__main__':
    unittest.main()
