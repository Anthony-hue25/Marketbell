"""Isolated HTTP + idle worker benchmark; no model calls, no public deployment."""
import os,json,secrets,subprocess,sys,tempfile,time,statistics,requests
from pathlib import Path
from backend.config import ROOT,now
from scripts.audit_autonomy import CHILD
from scripts.process_utils import audit_python,audit_env

def stat(values):
 s=sorted(values);return {'samples':len(s),'p50_ms':statistics.median(s),'p95_ms':s[min(len(s)-1,int(len(s)*.95))],'max_ms':s[-1]}

def main():
 with tempfile.TemporaryDirectory() as folder:
  token=secrets.token_urlsafe(32);env={**os.environ,'MARKETBELL_DATA_DIR':folder,'MARKETBELL_WATCH':'0','MARKETBELL_SCHEDULER':'0','MARKETBELL_ALLOW_LIVE':'0','MARKETBELL_ADMIN_TOKEN':token,'PYTHONUTF8':'1','AUDIT_INSTANCE':secrets.token_hex(12)}
  start=time.perf_counter();child=subprocess.Popen([audit_python(),'-B','-c',CHILD.replace('4320','4321')],cwd=ROOT,env=audit_env(env),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  try:
   for _ in range(300):
    try:
     identity=requests.get('http://127.0.0.1:4321/audit-instance',timeout=1)
     if identity.ok and identity.json().get('instance')==env['AUDIT_INSTANCE']:
      assert identity.json()['pid']==child.pid;break
    except requests.RequestException:pass
    if child.poll() is not None:raise RuntimeError('BENCHMARK_SERVER_START_FAILED')
    time.sleep(.1)
   result={'at':now(),'startup_ms':(time.perf_counter()-start)*1000,'scope':'Isolated local server, mocked-free idle service, no model calls','endpoints':{}}
   client=requests.Session()
   for path,n in (('/',30),('/healthz',60),('/api/state',40),('/api/impact?days=30&dataset=workspace',40),('/api/universe',40)):
    values=[];sizes=[]
    for _ in range(n):
     a=time.perf_counter();r=client.get('http://127.0.0.1:4321'+path,timeout=20);r.raise_for_status();values.append((time.perf_counter()-a)*1000);sizes.append(len(r.content))
    result['endpoints'][path]={**stat(values),'response_bytes':max(sizes)}
   h={'Authorization':'Bearer '+token}
   first=client.get('http://127.0.0.1:4321/api/diagnostics',headers=h,timeout=20).json()
   begin=time.monotonic()
   while time.monotonic()-begin<15:time.sleep(1)
   last=client.get('http://127.0.0.1:4321/api/diagnostics',headers=h,timeout=20).json()
   result.update(idle_window_seconds=time.monotonic()-begin,first_diagnostics=first,last_diagnostics=last,
    idle_cpu_percent_one_core=(last['resources']['cpu_seconds']-first['resources']['cpu_seconds'])/(time.monotonic()-begin)*100)
   # Reproduce teach-once via the public API; preserved genuine inference, no paid calls.
   state=client.get('http://127.0.0.1:4321/api/state',timeout=20).json()
   headers={'Origin':'http://127.0.0.1:4321','Content-Type':'application/json','X-CSRF-Token':state['csrf']}
   def action(value):
    response=client.post('http://127.0.0.1:4321/api/action',json=value,headers=headers,timeout=30);response.raise_for_status();return response.json()
   a=time.perf_counter();action({'action':'start','mode':'verified'});start_ms=(time.perf_counter()-a)*1000
   from backend.recordings import load
   state=client.get('http://127.0.0.1:4321/api/state',timeout=20).json();event=next(e for e in state['events'] if e.get('recording')=='psx')
   candidate=action({'action':'correct','id':event['id'],'text':load('preference')['text']})['candidate']
   action({'action':'save-rule','id':candidate['id']})
   a=time.perf_counter();action({'action':'approve','id':event['id'],'version':event['version']});decision_ms=(time.perf_counter()-a)*1000
   action({'action':'next','mode':'verified'})
   impact=client.get('http://127.0.0.1:4321/api/impact?dataset=replay&days=30',timeout=20).json()
   assert impact['counts']['rule_reused']==1 and impact['counts']['autonomous']==1
   result['teach_once_api']={'mode':'Verified authentic replay; explicit approvals issued by the test harness in an isolated workspace','start_ms':start_ms,'approval_ms':decision_ms,'counts':impact['counts'],'result':'PASS'}
   Path('docs/AUDIT_RUNTIME_BENCHMARK.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
  finally:child.terminate();child.wait(10)
if __name__=='__main__':main()
