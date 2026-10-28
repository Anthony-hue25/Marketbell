"""Small genuine-model, browser-independent recovery and live-source verification.
Separate isolated DB/server. No user session exists until final display verification.
"""
import os,sys,json,time,secrets,subprocess,requests
from pathlib import Path
from datetime import datetime,timezone,timedelta
from backend.config import ROOT,now
from backend.store import Store,digest
from backend.market import news,session_state
from backend.recordings import load
from backend.engine import Engine
from backend.worker import Worker
from backend.vendor.calendar import calendar
import pandas as pd
from scripts.process_utils import audit_python,audit_env

CHILD='''import os
from wsgiref.simple_server import make_server,WSGIRequestHandler
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer
from backend.app import Application
class Server(ThreadingMixIn,WSGIServer):daemon_threads=True
class Quiet(WSGIRequestHandler):
 def log_message(self,*args):pass
import json
application=Application()
def wrapped(environ,start_response):
 if environ.get('PATH_INFO')=='/audit-instance':
  raw=json.dumps({'instance':os.environ['AUDIT_INSTANCE'],'pid':os.getpid()}).encode();start_response('200 OK',[('Content-Type','application/json')]);return [raw]
 return application(environ,start_response)
make_server('127.0.0.1',4320,wrapped,server_class=Server,handler_class=Quiet).serve_forever()
'''

def main():
 folder=ROOT/'work'/'audit'/('live-'+secrets.token_hex(4));folder.mkdir(parents=True)
 env={**os.environ,'MARKETBELL_DATA_DIR':str(folder),'MARKETBELL_ALLOW_LIVE':'1','MARKETBELL_WATCH':'0','MARKETBELL_SCHEDULER':'0','MARKETBELL_DAILY_CALLS':'20','MARKETBELL_DAILY_USD':'0.50','MARKETBELL_WEBHOOK_TOKEN':secrets.token_urlsafe(32),'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1'}
 token=env['MARKETBELL_WEBHOOK_TOKEN'];server=None;report={'started':now(),'browser_required':False,'environment':'Isolated local real background-worker acceptance','results':[]}
 def start():
  env['AUDIT_INSTANCE']=secrets.token_hex(12)
  p=subprocess.Popen([audit_python(),'-B','-c',CHILD],cwd=ROOT,env=audit_env(env),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  for _ in range(100):
   try:
    if p.poll() is not None:raise RuntimeError('AUDIT_SERVER_START_FAILED')
    identity=requests.get('http://127.0.0.1:4320/audit-instance',timeout=1)
    if identity.ok and identity.json().get('instance')==env['AUDIT_INSTANCE']:
     if identity.json()['pid']!=p.pid:raise RuntimeError('AUDIT_PROCESS_ID_MISMATCH')
     return p
   except requests.RequestException:pass
   if p.poll() is not None:raise RuntimeError('AUDIT_SERVER_START_FAILED')
   time.sleep(.1)
  raise RuntimeError('AUDIT_SERVER_TIMEOUT')
 def wait_job(store,job):
  deadline=time.monotonic()+300
  while time.monotonic()<deadline:
   with store.connect() as db:r=db.execute('SELECT * FROM jobs WHERE id=?',(job,)).fetchone()
   if r and r['status'] in ('completed','failed'):return dict(r)
   time.sleep(.2)
  raise RuntimeError('AUDIT_JOB_TIMEOUT')
 try:
  server=start();store=Store(folder/'marketbell.sqlite3')
  source=next(e for e in load('psx')['package']['evidence'] if e['type']=='issuer_event_announcement')
  payload={'symbol':'PSX','event_id':'audit-controlled-'+now(),'published_at':'2026-09-16T00:00:00+00:00',
   'source':source['source'],'text':'CONTROLLED ACCEPTANCE DELIVERY of preserved real issuer announcement; not newly published news. '+source['text'][:1200]}
  response=requests.post('http://127.0.0.1:4320/api/webhook',json=payload,headers={'Authorization':'Bearer '+token},timeout=10);response.raise_for_status();job=response.json()['job']
  deadline=time.monotonic()+240;research=None
  while time.monotonic()<deadline:
   events=store.snapshot('monitor-public')['events']
   research=next((e for e in events if e.get('research_trace')),None)
   if research:break
   time.sleep(.1)
  if not research:raise RuntimeError('RESEARCH_NOT_PERSISTED')
  research_hash=digest(research['research_trace']);first_pid=server.pid;server.terminate();server.wait(10);server=None
  server=start();assert server.pid!=first_pid;out=wait_job(store,job)
  events=store.snapshot('monitor-public')['events'];event=next(e for e in events if e['id']==research['id'])
  assert out['status']=='completed' and digest(event['research_trace'])==research_hash
  duplicate=requests.post('http://127.0.0.1:4320/api/webhook',json=payload,headers={'Authorization':'Bearer '+token},timeout=10);assert duplicate.json()['job']==job
  with store.connect() as db:
   sessions=db.execute('SELECT count(*) FROM sessions').fetchone()[0]
   stages=[r[0] for r in db.execute('SELECT stage FROM lifecycle WHERE tenant=? AND event=?',('monitor-public',event['id']))]
  assert sessions==0 and {'DETECTED','TRIAGED','INVESTIGATED','CHALLENGED','POLICY CHECKED','RESOLVED'}<=set(stages)
  report['results'].append({'test':'Controlled event and mid-processing process restart','result':'PASS','source_label':'Controlled delivery of preserved issuer announcement','event':event['id'],'status':event['status'],'job_attempts':out['attempts'],'sessions_during_processing':sessions,'research_preserved':True,'process_before':first_pid,'process_after':server.pid,'actual_process_identity_checked':True,'outcomes':1,'stages':stages,
    'traces':[{k:e.get(k) for k in ('stage','model','provider','at','duration_ms','usage','api_request_id')} for e in (event['research_trace'],event['challenge_trace'])]})
  print('Controlled real-model processing and restart verified.',flush=True)
  live=[item for item in news('PSX') if item.get('entity_match',True)]
  if not live:raise RuntimeError('LIVE_SOURCE_EMPTY')
  # Feed the actual unmodified returned item through the normal recurring detector.
  with store.transaction() as db:store.put(db,'monitor-public','watch_cursor','PSX',{'seen':[],'checked_at':(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()})
  worker=Worker(store,Engine(store));status=worker.scan_watch(lambda s:live[:1] if s=='PSX' else [])
  with store.connect() as db:row=db.execute("SELECT id FROM jobs WHERE kind='watch-event' AND id!=? ORDER BY created DESC LIMIT 1",(job,)).fetchone()
  assert row;live_job=wait_job(store,row[0]);assert live_job['status']=='completed'
  live_event=next(e for e in store.snapshot('monitor-public')['events'] if e['id']!=event['id'])
  report['results'].append({'test':'Authentic fetched RSS item through recurring detector','result':'PASS','source':live[0],'provider_connectivity':'HTTP retrieval succeeded; redistribution license is not established by connectivity','event':live_event['id'],'status':live_event['status'],
   'traces':[{k:e.get(k) for k in ('stage','model','provider','at','duration_ms','usage','api_request_id')} for e in (live_event['research_trace'],live_event['challenge_trace'])]})
  display=requests.get('http://127.0.0.1:4320/api/state',timeout=15);assert display.ok and any(e['id']==live_event['id'] for e in display.json()['monitored']['events'])
  report.update(result='PASS',displayed_via_normal_state_api=True,completed=now(),market_open=bool(calendar().is_open_on_minute(pd.Timestamp(datetime.now(timezone.utc)).floor('min'))),new_market_close_required=False,diagnostics=store.diagnostics(),audit=Store(store.path).verify())
 except Exception as error:
  report.update(result='FAIL',error=str(error) if str(error).isupper() else type(error).__name__,completed=now())
 finally:
  if server:server.terminate();server.wait(10)
  (ROOT/'docs'/'AUDIT_AUTONOMY_PROOF.json').write_text(json.dumps(report,indent=2))
 print(json.dumps(report,indent=2),flush=True)
 if report['result']!='PASS':raise SystemExit(1)
if __name__=='__main__':main()
