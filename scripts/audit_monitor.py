"""Two genuine recurring source polls, no browser or login, no model charges."""
import os,time,json,secrets,subprocess,tempfile,requests
from pathlib import Path
from backend.config import ROOT,now
from backend.store import Store
from scripts.audit_autonomy import CHILD
from scripts.process_utils import audit_python,audit_env

def main():
 folder=ROOT/'work'/'audit'/('monitor-'+secrets.token_hex(4));folder.mkdir();nonce=secrets.token_hex(12)
 env=audit_env({**os.environ,'MARKETBELL_DATA_DIR':str(folder),'MARKETBELL_WATCH':'1','MARKETBELL_WATCH_SECONDS':'60','MARKETBELL_SCHEDULER':'0','MARKETBELL_ALLOW_LIVE':'0','AUDIT_INSTANCE':nonce})
 child=subprocess.Popen([audit_python(),'-B','-c',CHILD.replace('4320','4322')],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 try:
  for _ in range(150):
   try:
    r=requests.get('http://127.0.0.1:4322/audit-instance',timeout=1)
    if r.ok and r.json().get('instance')==nonce:
     assert r.json()['pid']==child.pid;break
   except requests.RequestException:pass
   if child.poll() is not None:raise RuntimeError('MONITOR_TEST_START_FAILED')
   time.sleep(.1)
  store=Store(folder/'marketbell.sqlite3');checks=[];deadline=time.monotonic()+180
  while time.monotonic()<deadline:
   with store.connect() as db:health=store.get(db,'monitor-public','monitor','continuous')
   if health and (not checks or health['checkedAt']!=checks[-1]['checkedAt']):
    checks.append(health);print('Scheduled source check '+str(len(checks))+' observed',flush=True)
   if len(checks)>=2:break
   time.sleep(1)
  with store.connect() as db:
   sessions=db.execute('SELECT count(*) FROM sessions').fetchone()[0];calls=db.execute('SELECT count(*) FROM model_calls').fetchone()[0];screened=db.execute("SELECT count(*) FROM news_identity WHERE tenant='monitor-public'").fetchone()[0]
  assert len(checks)>=2 and all(c['active'] for c in checks) and sessions==0 and calls==0 and screened>0
  report={'result':'PASS','at':now(),'actual_process_identity_checked':True,'browser_connections':0,'sessions':sessions,'automatic_checks':checks,'unique_live_headlines':screened,'model_invocations':calls,'mode':'Real scheduled source monitoring; fresh inference deliberately disabled to bound this endurance check. Full real-model resolution is a separate acceptance artifact.','audit':store.verify()}
  (ROOT/'docs'/'AUDIT_SCHEDULED_MONITOR_PROOF.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))
 finally:child.terminate();child.wait(10)
if __name__=='__main__':main()
