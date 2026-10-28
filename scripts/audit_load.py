"""Bounded offline load/latency audit. Fake model responses are explicitly labelled."""
import json,tempfile,time,statistics,sys,os
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from backend.store import Store,digest
from backend.engine import Engine
from backend.worker import Worker
from backend.impact import summary
sys.path.insert(0,str(Path('test').resolve()))
from test_backend import FakeAI,package,SESSION

def distribution(values):
 values=sorted(values);return {'n':len(values),'p50_ms':statistics.median(values),'p95_ms':values[min(len(values)-1,int(.95*len(values)))],'min_ms':values[0],'max_ms':values[-1]}

def main():
 result={'at':datetime.now(timezone.utc).isoformat(),'model':'MOCKED — no credentials or paid load calls','bursts':[]}
 with tempfile.TemporaryDirectory() as tmp:
  for n in (1,10,100):
   store=Store(Path(tmp)/f'{n}.sqlite3');ai=FakeAI();engine=Engine(store,ai);worker=Worker(store,engine);times=[];start=time.perf_counter()
   notices=[{'symbol':('PSX','VLO','NVDA','JPM')[i%4],'notice':{'id':digest(i),'type':'headline_only','source':'CONTROLLED OFFLINE LOAD','text':'CONTROLLED offline news headline for bounded load test '+str(i),'published_at':datetime.now(timezone.utc).isoformat()}} for i in range(n)]
   def enqueue(p):
    a=time.perf_counter();id=worker.enqueue('monitor-public','watch-event',p,key=p['notice']['id']);times.append((time.perf_counter()-a)*1000);return id
   with ThreadPoolExecutor(max_workers=4) as pool:ids=list(pool.map(enqueue,notices*2))
   resolution=[]
   with patch('backend.worker.acquire',side_effect=lambda symbol,session=None:package(symbol)),patch('backend.worker.session_state',return_value={'latest':SESSION}),patch('backend.engine.session_state',return_value={'latest':SESSION}):
    while True:
     a=time.perf_counter();worked=worker.once()
     if not worked:break
     resolution.append((time.perf_counter()-a)*1000)
   metrics=[]
   for _ in range(40):
    a=time.perf_counter();summary(store,'monitor-public');metrics.append((time.perf_counter()-a)*1000)
   with store.connect() as db:
    jobs={r[0]:r[1] for r in db.execute('SELECT status,count(*) FROM jobs GROUP BY status')}
    plans=[list(r) for r in db.execute("EXPLAIN QUERY PLAN SELECT body FROM impact_cases WHERE tenant='monitor-public' AND origin='genuine' AND created>='2026-01-01' ORDER BY created DESC LIMIT 20")]
   with store.connect() as db:
    waits=[r[0] for r in db.execute("SELECT (julianday(started_at)-julianday(created))*86400000 FROM jobs WHERE started_at IS NOT NULL")]
    policy=[json.loads(r[0]).get('policy_duration_ms',0) for r in db.execute("SELECT body FROM documents WHERE kind='event'")]
   result['bursts'].append({'events':n,'securities':['PSX','VLO','NVDA','JPM'][:min(n,4)],'queue_wait':distribution(waits),'policy_evaluation':distribution(policy),'duplicate_arrivals':n,'unique_jobs':len(set(ids)),'jobs':jobs,'enqueue':distribution(times),'resolution_mocked':distribution(resolution),'impact_query':distribution(metrics),'wall_seconds':round(time.perf_counter()-start,3),'model_calls':len(ai.calls),'query_plan':plans})
  result['database_integrity']=store.verify();backup=Path(tmp)/'restore.sqlite3';store.backup(backup);result['backup_restore']=Store(backup).verify()
 try:
  import psutil
  process=psutil.Process();cpu=process.cpu_times();result['resources']={'rss_bytes':process.memory_info().rss,'cpu_user_seconds':cpu.user,'cpu_system_seconds':cpu.system,'scope':'Audit harness process, not a hosted-service capacity claim'}
 except ImportError:result['resources']={'status':'psutil unavailable'}
 Path('docs/AUDIT_LOAD_RESULTS.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if __name__=='__main__':main()
