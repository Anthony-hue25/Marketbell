import json,sqlite3,tempfile,threading,time,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from backend.store import Store,digest
from backend.engine import Engine
from backend.worker import Worker
from backend.impact import summary,evaluate,report,screen
from backend.universe import parse_csv,parse_rows,default_mandate,commit,current,consider,decide,archive
from backend.demo_history import seed
from test_backend import package,FakeAI,answer,SESSION


def session_after(days):
    """First XNYS session whose close is at least `days` calendar days from now (no fixed dates)."""
    from backend.vendor.calendar import calendar
    import pandas as pd
    cal=calendar();day=pd.Timestamp((datetime.now(timezone.utc)+timedelta(days=days)).date())
    while not cal.is_session(day):day+=pd.Timedelta(days=1)
    return str(day.date())

class ImpactUniverseTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'db.sqlite3');self.engine=Engine(self.store,FakeAI());self.engine.ensure('a')
  self.cal=patch('backend.engine.session_state',return_value={'latest':SESSION});self.cal.start()
 def tearDown(self):self.cal.stop();self.tmp.cleanup()
 def event(self,s='PSX',recording=None,mode='autonomous'):
  e=self.engine.ingest('a',package(s),mode,recording);return self.engine.investigate('a',e['id'])
 def mandate(self,**changes):
  m=default_mandate();m.update(changes);return {k:v for k,v in m.items() if k not in ('version','approved_at')}
 def onboard(self,automatic=None,rows=None):
  return commit(self.store,'a',rows or [{'ticker':'JPM','quantity':2,'tier':'portfolio','position_pct':3},{'ticker':'PSX','tier':'watchlist'}],self.mandate(automatic=automatic or {'promote':False,'deprioritize':False,'archive':False}),1,True)
 def test_unique_case_metrics_retry_and_screen_dedup(self):
  e=self.event();self.engine.investigate('a',e['id']);self.engine.ingest('a',package())
  with self.store.transaction() as db:
   for _ in range(3):screen(db,'monitor-public','PSX',{'id':'story','text':'test'})
  d=summary(self.store,'a');self.assertEqual(d['counts']['developments'],1);self.assertEqual(d['counts']['news_screened'],1);self.assertEqual(d['counts']['attention'],1);self.assertEqual(d['counts']['autonomous'],0)
 def test_user_approval_is_not_autonomous(self):
  e=self.event();self.engine.decide('a',e['id'],'approve',e['version']);d=summary(self.store,'a');self.assertEqual(d['counts']['autonomous'],0);self.assertEqual(d['counts']['user_involved'],1)
 def test_routine_is_autonomous_not_blocked(self):
  self.engine.ai=FakeAI(answer('no_action','Routine'),answer('no_action','Routine'));self.event();self.assertEqual(summary(self.store,'a')['counts']['autonomous'],1)
 def test_replay_genuine_and_tenant_separation(self):
  self.event(mode='verified');self.assertEqual(summary(self.store,'a')['counts']['developments'],0);self.assertEqual(summary(self.store,'a',origin_filter='replay')['counts']['developments'],1);self.assertEqual(summary(self.store,'b',origin_filter='replay')['counts']['developments'],0)
 def test_immutable_original_after_user_decision(self):
  e=self.event()
  with self.store.connect() as db:before=db.execute('SELECT hash FROM decision_snapshots').fetchone()[0]
  self.engine.decide('a',e['id'],'reject',e['version'])
  with self.store.connect() as db:
   self.assertEqual(before,db.execute('SELECT hash FROM decision_snapshots').fetchone()[0])
   with self.assertRaises(sqlite3.IntegrityError):db.execute("UPDATE decision_snapshots SET hash='bad'")
 def test_horizon_and_missing_later_evidence(self):
  e=self.event()
  with self.assertRaisesRegex(ValueError,'NOT_DUE'):evaluate(self.store,'a',e['id'],7)
  at=(datetime.now(timezone.utc)+timedelta(days=8)).isoformat();out=evaluate(self.store,'a',e['id'],7,at=at);self.assertEqual(out['outcome'],'not_evaluable')
  evaluate(self.store,'a',e['id'],7,at=at)
  with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM evaluations').fetchone()[0],1)
 def test_followup_rejects_lookahead_and_old_sources(self):
  e=self.event();at=(datetime.now(timezone.utc)+timedelta(days=8)).isoformat()
  later=[{'published_at':(datetime.now(timezone.utc)+timedelta(days=9)).isoformat(),'text':'future'},{'published_at':(datetime.now(timezone.utc)-timedelta(days=1)).isoformat(),'text':'old'}]
  self.assertEqual(evaluate(self.store,'a',e['id'],7,later,at=at)['outcome'],'not_evaluable')
 def test_contradicted_append_only_review(self):
  e=self.event();later=[{'id':'later','published_at':(datetime.now(timezone.utc)+timedelta(days=2)).isoformat(),'text':'Later conflicting evidence'}]
  out=evaluate(self.store,'a',e['id'],7,later,{'outcome':'contradicted','summary':'Test later contradiction'},at=(datetime.now(timezone.utc)+timedelta(days=8)).isoformat());self.assertEqual(out['outcome'],'contradicted')
  with self.store.connect() as db:
   with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM evaluations')
 def test_missed_report_dedup_is_attributed(self):
  for _ in range(2):report(self.store,'a','missed_risk','A test development was not observed.')
  d=summary(self.store,'a');self.assertEqual(len(d['reports']),1);self.assertIn('not independently',d['reports'][0]['attribution'])
 def test_csv_exchange_duplicate_and_quantity(self):
  self.assertEqual(parse_csv('ticker,exchange,tier,quantity\nAAPL,XNAS,portfolio,2')[0]['symbol'],'AAPL')
  for text in ('ticker,exchange\nAAPL,XNYS','ticker\nFAKE','ticker\nPSX\npsx','ticker,tier\nPSX,portfolio'):
   with self.assertRaises(ValueError):parse_csv(text)
 def test_csv_no_formula_numeric_nan(self):
  for q in ('=1+2','NaN','inf','-1'):
   with self.assertRaises(ValueError):parse_csv('ticker,tier,quantity\nPSX,portfolio,'+q)
 def test_explicit_mandate_confirmation(self):
  with self.assertRaisesRegex(ValueError,'CONFIRMATION'):commit(self.store,'a',[{'ticker':'PSX'}],self.mandate(),1)
 def test_onboarding_records_manual_ownership(self):
  u=self.onboard();h=next(e for e in u['entries'] if e['tier']=='portfolio');self.assertIn('not verified',h['ownership']);self.assertEqual(h['quantity'],2)
 def test_mandate_version_conflict(self):
  self.onboard()
  with self.assertRaisesRegex(ValueError,'STALE'):self.onboard()
 def test_automatic_promotion_never_adds_holding(self):
  self.onboard({'promote':True,'deprioritize':False,'archive':False});e=self.event('VLO');u=current(self.store,'a');self.assertEqual(next(x for x in u['entries'] if x['symbol']=='VLO')['tier'],'watchlist')
  with self.store.connect() as db:p=self.store.get(db,'a','portfolio','main')
  self.assertNotIn('VLO',[h['symbol'] for h in p['holdings']]);self.assertEqual(u['recommendations'][0]['status'],'applied');self.assertEqual(summary(self.store,'a')['universe']['automatically_organized'],1)
 def test_no_authority_proposes_promotion(self):
  self.onboard();self.event('VLO');u=current(self.store,'a');r=u['recommendations'][0];self.assertEqual(r['status'],'proposed');decide(self.store,'a',r['id'],'approve',r['version']);self.assertEqual(current(self.store,'a')['recommendations'][0]['ownership_changed'],False)
 def test_rejected_candidate_stays_pool(self):
  self.onboard();self.event('VLO');r=current(self.store,'a')['recommendations'][0];decide(self.store,'a',r['id'],'reject',1);self.assertEqual(next(e for e in current(self.store,'a')['entries'] if e['symbol']=='VLO')['tier'],'pool')
 def test_watchlist_limit_blocks_candidate(self):
  self.onboard({'promote':True,'deprioritize':False,'archive':False})
  with self.store.transaction() as db:m=self.store.get(db,'a','mandate','main');m['max_watchlist']=1;self.store.put(db,'a','mandate','main',m)
  self.event('VLO');self.assertEqual(current(self.store,'a')['recommendations'][0]['status'],'blocked')
 def test_exclusion_overrides_automatic_permission(self):
  self.onboard({'promote':True,'deprioritize':True,'archive':True})
  with self.store.transaction() as db:m=self.store.get(db,'a','mandate','main');m['excluded_symbols']=['VLO'];self.store.put(db,'a','mandate','main',m)
  self.event('VLO');self.assertEqual(current(self.store,'a')['recommendations'][0]['status'],'blocked')
 def test_pinned_watchlist_cannot_archive(self):
  self.onboard(rows=[{'ticker':'PSX','tier':'watchlist','pinned':True}])
  with self.assertRaisesRegex(ValueError,'PROTECTED'):archive(self.store,'a','PSX',2,True)
 def test_stale_recommendation_after_mandate_edit(self):
  self.onboard();self.event('VLO');r=current(self.store,'a')['recommendations'][0]
  with self.store.transaction() as db:m=self.store.get(db,'a','mandate','main');m['version']+=1;self.store.put(db,'a','mandate','main',m)
  with self.assertRaisesRegex(ValueError,'STALE'):decide(self.store,'a',r['id'],'approve',1)
 def test_universe_cross_tenant_decision_denied(self):
  self.onboard();self.event('VLO');r=current(self.store,'a')['recommendations'][0]
  with self.assertRaisesRegex(ValueError,'STALE'):decide(self.store,'b',r['id'],'approve',1)
 def test_history_cannot_be_rewritten(self):
  self.onboard()
  with self.store.connect() as db:
   with self.assertRaises(sqlite3.IntegrityError):db.execute("UPDATE documents SET body='{}' WHERE kind='universe_history'")
 def test_budget_deferred_source_is_durable(self):
  w=Worker(self.store,self.engine);item={'id':'durable','type':'headline_only','text':'Test controlled new development','source':'test','published_at':datetime.now(timezone.utc).isoformat()}
  with patch.dict('os.environ',{'MARKETBELL_DAILY_CALLS':'0'}):w.scan_watch(lambda s:[item] if s=='PSX' else [])
  r=w.scan_watch(lambda s:[]);self.assertEqual(r['newInvestigations'],1)
 def test_empty_poll_is_success_not_full_coverage(self):
  r=Worker(self.store,self.engine).scan_watch(lambda s:[]);self.assertTrue(r['active']);self.assertIn('Empty polls',r['coverageLimitations'])
 def test_outage_retains_last_success(self):
  w=Worker(self.store,self.engine);before=w.scan_watch(lambda s:[])
  def fail(s):raise ValueError('NETWORK_TEST')
  after=w.scan_watch(fail);self.assertFalse(after['active']);self.assertEqual(before['lastSuccessAt'],after['lastSuccessAt'])
 def test_spend_and_event_caps(self):
  with patch.dict('os.environ',{'MARKETBELL_DAILY_USD':'0.001'}):
   with self.assertRaisesRegex(ValueError,'SPEND'):self.store.reserve_model('e','RESEARCH',.002)
  with patch.dict('os.environ',{'MARKETBELL_EVENT_CALLS':'1'}):
   self.store.reserve_model('e','RESEARCH',.002)
   with self.assertRaisesRegex(ValueError,'EVENT'):self.store.reserve_model('e','CHALLENGE',.002)
 def test_worker_survives_database_outage(self):
  w=Worker(self.store,self.engine);calls=[]
  def beat(name):
   calls.append(name)
   if len(calls)==1:raise sqlite3.OperationalError('locked')
   w.stop.set()
  with patch.object(w,'heartbeat',side_effect=beat),patch.object(w,'once',return_value=False):
   t=threading.Thread(target=w.run);t.start();t.join(4)
  self.assertFalse(t.is_alive());self.assertGreaterEqual(len(calls),2)
 def test_timezone_and_calendar_period_metadata(self):
  self.event();d=summary(self.store,'a',1);self.assertEqual(d['timezone'],'America/Port_of_Spain');self.assertIn('+00:00',d['since'])
 def test_demo_history_is_separate_and_reproducible(self):
  result=seed(self.store,'illustrative-a');self.assertEqual(result['events'],120);seed(self.store,'illustrative-a')
  self.assertEqual(summary(self.store,'a')['counts']['developments'],0)
  self.assertEqual(summary(self.store,'illustrative-a',90,'illustrative')['counts']['developments'],120)
  self.assertEqual(summary(self.store,'illustrative-a',90,'genuine')['counts']['developments'],0)
  self.assertEqual(len(Worker(self.store,self.engine).observed_symbols()),14)  # 6 holdings + 4 watchlist + 4 rotating discovery
  self.assertEqual(self.store.diagnostics()['materialCases'],0)
  self.assertEqual(next(e for e in current(self.store,'illustrative-a')['entries'] if e['symbol']=='GE')['tier'],'archived')
  self.store.verify()

class AdditionalAuthorityTests(ImpactUniverseTests):
 def test_excluded_unowned_discovery_does_not_interrupt_user(self):
  self.onboard()
  with self.store.transaction() as db:m=self.store.get(db,'a','mandate','main');m['excluded_sectors']=['Technology'];self.store.put(db,'a','mandate','main',m)
  self.engine.ai=FakeAI(answer('hold','Risk'),answer('hold','Risk'));source=self.engine.ingest('monitor-public',package('NVDA'));source=self.engine.investigate('monitor-public',source['id']);Worker(self.store,self.engine).personalize(source)
  self.assertFalse(any(e['symbol']=='NVDA' for e in self.store.snapshot('a')['events']))
 def test_list_learning_uses_worker_and_proposes_without_authority(self):
  from backend.ai import AI
  self.onboard();self.event('VLO');rec=current(self.store,'a')['recommendations'][0]
  output={'interpretable':True,'reason':'Test-only scoped interpretation.','rule':{'scope':'list_management','operation':'promote','sector':'Energy','max_watchlist':12,'effect':'allow','expires_days':30}}
  class Provider:
   model='TEST ONLY'
   def complete(self,*args):return {'content':json.dumps(output)}
  self.engine.ai=AI(self.store,Provider());result=Worker(self.store,self.engine).execute('a','list-correction',{'recommendation':rec['id'],'text':'Allow Energy watchlist promotions within 12 companies for 30 days.'})
  self.assertFalse(result['candidate']['active']);self.assertEqual(current(self.store,'a')['list_rules'],[])
 def test_disabled_inference_cannot_be_bypassed_at_model_boundary(self):
  from backend.ai import AI
  class Provider:
   def complete(self,*args):raise AssertionError('Provider must not be invoked')
  with patch.dict('os.environ',{'MARKETBELL_ALLOW_LIVE':'0'}):
   with self.assertRaisesRegex(ValueError,'LIVE_INFERENCE_DISABLED'):AI(self.store,Provider()).preference('Do not call the provider.',{'symbol':'PSX','kind':'Opportunity','sector':'Energy'})
  worker=Worker(self.store,self.engine);id=worker.enqueue('a','live',{'recording':'psx','request_id':'paused'})
  with self.store.transaction() as db:db.execute("UPDATE jobs SET status='failed',attempts=1,error='LIVE_INFERENCE_DISABLED' WHERE id=?",(id,))
  with patch.dict('os.environ',{'MARKETBELL_ALLOW_LIVE':'1'}):worker.recover()
  with self.store.connect() as db:self.assertEqual(db.execute('SELECT status FROM jobs WHERE id=?',(id,)).fetchone()[0],'queued')
 def test_explicit_fresh_runs_have_distinct_durable_identities(self):
  from backend.recordings import load
  rec=load('psx');self.engine.ai=FakeAI(rec['traces']['research']['output'],rec['traces']['challenge']['output']);w=Worker(self.store,self.engine)
  one=w.execute('a','live',{'recording':'psx','request_id':'first'});two=w.execute('a','live',{'recording':'psx','request_id':'second'})
  self.assertNotEqual(one['id'],two['id']);self.assertEqual(self.engine.ai.calls,['RESEARCH','CHALLENGE','RESEARCH','CHALLENGE'])
 def test_private_preference_prompt_is_not_persisted_in_trace(self):
  from backend.ai import AI
  class Provider:
   model='TEST ONLY'
   def complete(self,messages,tools):return {'content':json.dumps({'interpretable':False,'reason':'Clarification required.','rule':None})}
  trace=AI(self.store,Provider()).preference('Private test-only preference, without authority.',{'symbol':'PSX','kind':'Opportunity','sector':'Energy'})
  self.assertIsNone(trace['request']);self.assertIsNone(trace['response']);self.assertTrue(trace['request_hash'])
 def test_late_review_cannot_import_post_horizon_news(self):
  e=self.event();later=[{'id':'too-late','published_at':(datetime.now(timezone.utc)+timedelta(days=8)).isoformat(),'text':'Evidence first published after the seven-day horizon.'}]
  out=evaluate(self.store,'a',e['id'],7,later,at=(datetime.now(timezone.utc)+timedelta(days=10)).isoformat());self.assertEqual(out['outcome'],'not_evaluable');self.assertEqual(out['later_evidence'],[])
 def test_mandate_revocation_pauses_learned_list_authority(self):
  self.onboard();self.list_rule();commit(self.store,'a',[{'ticker':'JPM','tier':'portfolio','quantity':2,'position_pct':3},{'ticker':'PSX','tier':'watchlist'}],self.mandate(),2,True)
  self.event('VLO');u=current(self.store,'a');self.assertFalse(u['list_rules'][0]['active']);self.assertEqual(u['recommendations'][0]['status'],'proposed')
 def test_mandate_exclusion_blocks_existing_note_permission(self):
  self.onboard()
  with self.store.transaction() as db:m=self.store.get(db,'a','mandate','main');m['excluded_sectors']=['Energy'];self.store.put(db,'a','mandate','main',m)
  e=self.event('VLO');self.assertEqual(e['status'],'blocked');self.assertFalse(e['policy']['mandate_eligible'])
 def test_positive_price_change_does_not_prove_thesis(self):
  import json
  p=package();p['evidence'].append({'id':'daily','type':'daily_market_data','source':'CONTROLLED DAILY TEST','text':json.dumps({'adjustment':'split','bars':[{'close':100,'session_date':SESSION}]})})
  e=self.engine.ingest('a',p);e=self.engine.investigate('a',e['id'])
  out=evaluate(self.store,'a',e['id'],7,at=(datetime.now(timezone.utc)+timedelta(days=8)).isoformat(),market_observation={'session':session_after(3),'close':110,'adjustment':'split','source':'CONTROLLED DAILY TEST'})
  self.assertEqual(out['market_performance']['status'],'observed');self.assertEqual(out['market_performance']['close_change_pct'],10);self.assertEqual(out['outcome'],'not_evaluable')
 def test_future_price_cannot_enter_earlier_horizon(self):
  p=package();p['evidence'].append({'id':'daily','type':'daily_market_data','source':'CONTROLLED DAILY TEST','text':json.dumps({'bars':[{'close':100,'session_date':SESSION}]})})
  e=self.engine.ingest('a',p);e=self.engine.investigate('a',e['id'])
  out=evaluate(self.store,'a',e['id'],7,at=(datetime.now(timezone.utc)+timedelta(days=10)).isoformat(),market_observation={'session':session_after(12),'close':110,'adjustment':'split','source':'CONTROLLED DAILY TEST'})
  self.assertEqual(out['market_performance']['status'],'not_evaluated')
 def test_current_position_overrides_narrow_note_rule(self):
  e=self.engine.ingest('a',package('PSX'))
  r={'id':'narrow','version':1,'active':True,'approved_at':(datetime.now(timezone.utc)-timedelta(days=1)).isoformat(),'scope':'opportunity_note','sector':'Energy','max_position_pct':3,'max_sector_pct':30,'effect':'allow','expires':(datetime.now(timezone.utc)+timedelta(days=30)).isoformat(),'uses':0}
  with self.store.transaction() as db:
   self.store.put(db,'a','rule',r['id'],r);p=self.store.get(db,'a','portfolio','main');p['holdings'].append({'symbol':'PSX','position_pct':4,'sector':'Energy'});self.store.put(db,'a','portfolio','main',p)
  out=self.engine.investigate('a',e['id']);self.assertEqual(out['status'],'needs');self.assertFalse(out.get('action'))
  with self.store.connect() as db:
   self.assertEqual(db.execute("SELECT count(*) FROM documents WHERE kind='rule_evaluation'").fetchone()[0],1)
   with self.assertRaises(sqlite3.IntegrityError):db.execute("UPDATE documents SET body='{}' WHERE kind='rule_evaluation'")
 def test_default_watchlist_can_be_archived_with_confirmation(self):
  out=archive(self.store,'a','PSX',1,True);self.assertEqual(out['tier'],'archived')
 def test_offline_workspace_receives_shared_investigation_without_login(self):
  self.onboard();source=self.engine.ingest('monitor-public',package('PSX'));source=self.engine.investigate('monitor-public',source['id']);Worker(self.store,self.engine).personalize(source)
  self.assertTrue(any(e.get('shared_investigation') for e in self.store.snapshot('a')['events']))
 def test_entity_mismatch_screened_but_not_investigated(self):
  item={'id':'wrong-entity','entity_match':False,'text':'An unrelated story returned by a broad search','source':'test','type':'headline_only','published_at':datetime.now(timezone.utc).isoformat()}
  result=Worker(self.store,self.engine).scan_watch(lambda s:[item] if s=='PSX' else []);self.assertEqual(result['newInvestigations'],0);self.assertEqual(summary(self.store,'monitor-public')['counts']['news_screened'],1)
 def test_repeated_watchlist_clear_does_not_count_as_new_promotion(self):
  self.onboard({'promote':True,'deprioritize':False,'archive':False});self.event('VLO');self.event('VLO',recording='second-development');u=current(self.store,'a');self.assertEqual(len([r for r in u['recommendations'] if r['action']=='promote' and r['status']=='applied']),1)
 def test_watchlist_rule_edit_is_explicit_and_versioned(self):
  from backend.universe import list_rule_action
  self.onboard();r=self.list_rule();out=list_rule_action(self.store,'a',r['id'],'edit',1,{'max_watchlist':5,'effect':'ask'});self.assertEqual(out['version'],2);self.assertEqual(out['effect'],'ask')
 # Inherit fixtures only; test discovery below avoids rerunning the base tests.
 def list_rule(self,effect='allow'):
  r={'id':'list-'+effect,'scope':'list_management','operation':'promote','sector':'Energy','max_watchlist':12,'effect':effect,'expires_days':30,'active':True,'approved_at':datetime.now(timezone.utc).isoformat(),'expires':(datetime.now(timezone.utc)+timedelta(days=30)).isoformat(),'version':1,'uses':0}
  with self.store.transaction() as db:self.store.put(db,'a','list_rule',r['id'],r)
  return r
 def test_list_rule_applies_without_ownership_change(self):
  self.onboard();self.list_rule();self.event('VLO');r=current(self.store,'a')['recommendations'][0];self.assertEqual(r['status'],'applied');self.assertEqual(r['ruleId'],'list-allow');self.assertFalse(r['ownership_changed'])
 def test_list_rule_conflict_asks_even_with_mandate(self):
  self.onboard({'promote':True,'deprioritize':False,'archive':False});self.list_rule();self.list_rule('ask');self.event('VLO');self.assertEqual(current(self.store,'a')['recommendations'][0]['status'],'proposed')
 def test_list_ask_rule_vetoes_mandate(self):
  self.onboard({'promote':True,'deprioritize':False,'archive':False});self.list_rule('ask');self.event('VLO');self.assertEqual(current(self.store,'a')['recommendations'][0]['status'],'proposed')
 def test_list_revoke_is_immediate(self):
  from backend.universe import list_rule_action
  self.onboard();r=self.list_rule();list_rule_action(self.store,'a',r['id'],'revoke',1);self.event('VLO');self.assertEqual(current(self.store,'a')['recommendations'][0]['status'],'proposed')
 def test_invalid_list_scope_cannot_trade(self):
  from backend.ai import validate_list_rule
  r=self.list_rule();r={k:r[k] for k in ('scope','operation','sector','max_watchlist','effect','expires_days')};r['operation']='buy'
  with self.assertRaises(ValueError):validate_list_rule({'interpretable':True,'reason':'bad','rule':r})
 def test_risk_alert_not_hidden_by_position_block(self):
  from backend.engine import default_portfolio
  with self.store.transaction() as db:
   p=default_portfolio();next(h for h in p['holdings'] if h['symbol']=='NVDA')['position_pct']=7;self.store.put(db,'a','portfolio','main',p)
  e=self.engine.ingest('a',package('NVDA'));self.engine.ai=FakeAI(answer('hold','Risk'),answer('hold','Risk'));e=self.engine.investigate('a',e['id']);self.assertEqual(e['status'],'needs');self.assertFalse(e['policy']['position_limit'])
  with self.assertRaisesRegex(ValueError,'POLICY'):self.engine.decide('a',e['id'],'approve',e['version'])
  out=self.engine.decide('a',e['id'],'acknowledge',e['version']);self.assertFalse(out.get('action'));self.assertTrue(out['user_involved'])
 def test_confirmed_portfolio_proposal_does_not_change_holdings(self):
  from backend.universe import propose_portfolio_change
  self.onboard();e=self.event('VLO');r=propose_portfolio_change(self.store,'a',e['id'],'add_candidate');before=current(self.store,'a')['entries'];decide(self.store,'a',r['id'],'approve',1);self.assertEqual(before,current(self.store,'a')['entries'])
 def test_replacement_requires_comparative_research(self):
  from backend.universe import propose_portfolio_change
  self.onboard();e=self.event('VLO')
  with self.assertRaisesRegex(ValueError,'COMPARATIVE'):propose_portfolio_change(self.store,'a',e['id'],'replace_holding','JPM')
 def test_public_response_omits_private_model_requests(self):
  from backend.app import Application
  e=self.event();data=Application.public_snapshot({'events':[{'research_trace':{'request':'private','response':'raw','model':'test'}}],'rules':[{'trace':'private'}],'candidate':{'trace':'private'}})
  self.assertNotIn('request',data['events'][0]['research_trace']);self.assertNotIn('trace',data['rules'][0]);self.assertNotIn('trace',data['candidate'])

# Rebind just fixture/helper methods to avoid inheriting and duplicating test cases.
for name in list(ImpactUniverseTests.__dict__):
 if name.startswith('test_') and name not in AdditionalAuthorityTests.__dict__:setattr(AdditionalAuthorityTests,name,None)

if __name__=='__main__':unittest.main()
