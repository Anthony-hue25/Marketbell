"""Defect reproduction harness: run unchanged against the pre-assurance snapshot and the current code.

    python -B -m scripts.assurance_repro            (from the repository root)

Uses test doubles only (no model, news or market calls). Prints one JSON object of observations.
"""
import io,json,os,tempfile,time
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
import sys; sys.path.insert(0,'test')
from backend.store import Store,digest
from backend.engine import Engine
from backend.app import Application
from test_backend import package,FakeAI,SESSION
NEW=hasattr(Store,'acquire_lease')
results_code='current' if NEW else 'pre-assurance'
results={}
cal=patch('backend.engine.session_state',return_value={'latest':SESSION});cal.start()
t=tempfile.mkdtemp();store=Store(Path(t)/'db.sqlite3');engine=Engine(store,FakeAI());engine.ensure('a')
# 1 evidence overwrite
first=package();e=engine.investigate('a',engine.ingest('a',first)['id'])
rev={**first['evidence'][0],'text':first['evidence'][0]['text']+' CORRECTION'}
engine.ingest('a',{**package(),'session':'2026-10-13','evidence':[rev]})
with store.connect() as db:kept=store.get(db,'a','evidence',first['evidence'][0]['id'])
results['evidence_revision_overwrites_first_version']=kept['text'].endswith('CORRECTION')
# 2 winter close timestamp
from backend import market
bars={'source':'T','retrieved_at':'x','session':'2026-12-15','bars':[{'session_date':'2026-12-14','close':1},{'session_date':'2026-12-15','close':1}],'page_hashes':[]}
with patch.object(market,'market_bars',return_value=bars),patch.object(market,'news',return_value=[]):
    results['winter_daily_data_published_before_close']=market.acquire('NVDA','2026-12-15')['evidence'][0]['published_at']
# 3 entity alias
from backend import universe
xml=b'<rss><channel><item><title>Bank of England raises rates - FT</title><link>https://news.google.com/a</link><pubDate>%s</pubDate></item></channel></rss>'%datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT').encode()
with patch.object(market.requests,'get',return_value=type('R',(),{'status_code':200,'content':xml})()):
    results['bank_of_england_matches_bank_of_america']=market.news('BAC')[0]['entity_match']
# 4 retroactive rule
f=Engine(store,FakeAI(fail='RESEARCH'));ev=f.ingest('a',package('VLO'))
try:f.investigate('a',ev['id'])
except ValueError:pass
r={'id':'r1','version':1,'active':True,'revoked':False,'scope':'opportunity_note','sector':'Energy','max_position_pct':3,'max_sector_pct':20,'effect':'allow','expires_days':30,'uses':0,'approved_at':datetime.now(timezone.utc).isoformat(),'expires':(datetime.now(timezone.utc)+timedelta(days=30)).isoformat()}
with store.transaction() as db:store.put(db,'a','rule','r1',r)
results['rule_approved_after_detection_authorizes_case']=engine.investigate('a',ev['id'])['status']=='handled'
# 5 headline-only under rule
p=package('PSX');p['session']='2026-10-10';p['evidence'][0]['type']='headline_only'
with patch('backend.engine.session_state',return_value={'latest':'2026-10-10'}):
    out=engine.investigate('a',engine.ingest('a',p)['id'])
results['headline_only_case_handled_automatically_under_rule']=out['status']=='handled'
# 6 shared new-session limit behind proxy
os.environ['MARKETBELL_TRUSTED_PROXY_HOPS']='1'  # Azure Container Apps ingress: one trusted forwarder
app=Application(store,start_worker=False);codes=[]
for i in range(25):
    o={};app({'REQUEST_METHOD':'GET','PATH_INFO':'/api/state','HTTP_HOST':'127.0.0.1:4318','REMOTE_ADDR':'100.100.0.1','HTTP_X_FORWARDED_FOR':'198.51.100.%d'%i,'wsgi.input':io.BytesIO()},lambda s,h:o.update(s=s));codes.append(o['s'][:3])
results['distinct_visitors_blocked_after_20_sessions_behind_proxy']=codes.count('429')
# 7 stale monitor
with store.transaction() as db:store.put(db,'monitor-public','monitor','continuous',{'active':True,'status':'Watching for new information','intervalSeconds':300,'checkedAt':(datetime.now(timezone.utc)-timedelta(hours=5)).isoformat()})
o={};body=app({'REQUEST_METHOD':'GET','PATH_INFO':'/api/state','HTTP_HOST':'127.0.0.1:4318','REMOTE_ADDR':'10.9.9.9','wsgi.input':io.BytesIO()},lambda s,h:o.update(s=s))
w=json.loads(body[0])['watch'];results['five_hour_stale_monitor_reported']={'active':w['active'],'status':w['status']}
# 8 healthz on db failure
with patch.object(store,'connect',side_effect=Exception('down')):
    o={};app({'REQUEST_METHOD':'GET','PATH_INFO':'/healthz','HTTP_HOST':'x','wsgi.input':io.BytesIO()},lambda s,h:o.update(s=s))
results['healthz_status_when_database_down']=o['s']
# 9 recover preempts running job of a live worker
from backend.worker import Worker
wk=Worker(store,engine);jid=wk.enqueue('a','test',{})
with store.transaction() as db:
    if NEW:wk.claim(db,jid)  # a live worker's claim carries a lease
    else:db.execute("UPDATE jobs SET status='running' WHERE id=?",(jid,))
Worker(store,engine).recover()
with store.connect() as db:results['second_worker_requeues_live_running_job']=db.execute('SELECT status FROM jobs WHERE id=?',(jid,)).fetchone()[0]=='queued'
# 10 interactive spend can exhaust monitoring budget
os.environ['MARKETBELL_INTERACTIVE_DAILY_CALLS']='1'
for i in range(4):
    try:store.reserve_call(4,'demo-%d'%i) if NEW else store.reserve_call(4)
    except ValueError:pass
try:(store.reserve_call(4,'monitor-public') if NEW else store.reserve_call(4));results['monitor_blocked_after_visitor_calls']=False
except ValueError as er:results['monitor_blocked_after_visitor_calls']=str(er)
results['code']=results_code
results['database']=getattr(store,'dialect','sqlite')
print(json.dumps(results,indent=1))
