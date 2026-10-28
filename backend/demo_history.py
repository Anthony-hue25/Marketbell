"""Explicit isolated illustrative history, generated through the normal governed engine.
No model calls. Synthetic outputs and later reviews are conspicuously labelled.
"""
import threading
from datetime import datetime,timedelta,timezone
from .engine import Engine
from .store import digest
from .config import now
from .market import session_state
from .impact import screen,evaluate
from .universe import default_mandate,consider

class SimulationAI:
 def analyze(self,stage,event):
  n=event['simulation_index'];decision='hold' if n%11==0 and stage=='CHALLENGE' else 'no_action' if n%4==0 else 'clear'
  kind='Routine' if n%4==0 else 'Risk' if n%9==0 or n==7 else 'Opportunity'
  if n%17==0:raise ValueError('ILLUSTRATIVE_PROVIDER_FAILURE')
  return {'stage':stage,'provider':'DETERMINISTIC SIMULATION — no AI invocation','model':'none','at':event['created'],
   'output':{'decision':decision,'classification':kind,'material':kind!='Routine',
   'summary':'ILLUSTRATIVE: '+('Insufficient corroboration; retain uncertainty.' if decision=='hold' else 'Review the synthetic company development without treating it as a trade.'),
   'claims':[{'evidence_id':event['evidence'][0]['id'],'quote':event['evidence'][0]['text'][:80]}]}}

_build_lock=threading.Lock()

def seed(store,tenant):
 with _build_lock:return build(store,tenant)

def build(store,tenant):
 with store.connect() as db:
  existing=store.get(db,tenant,'demo','seed')
 if existing:return existing
 # Historical processing uses an injected clock confined to this Engine, never global monkeypatching.
 base=datetime.now(timezone.utc)-timedelta(days=60);clock=[base]
 engine=Engine(store,SimulationAI(),clock=lambda:clock[0].isoformat());engine.ensure(tenant)
 mandate=default_mandate();mandate.update(approved_at=base.isoformat(),version=2,automatic={'promote':True,'deprioritize':True,'archive':True})
 with store.transaction() as db:
  store.put(db,tenant,'mandate','main',mandate)
  store.append(db,tenant,'Illustrative mandate approved','SIMULATED host approval of non-trading list management, not live user authority.')
 symbols=['PSX','VLO','NVDA','JPM','XOM','CVX','MSFT','AAPL']
 events=[]
 for i in range(120):
  clock[0]=base+timedelta(hours=i*12)
  s='GE' if i==7 else symbols[i%len(symbols)];session=session_state(clock[0])['latest']
  text='ILLUSTRATIVE DEMO: '+s+' announces a synthetic guidance update with uncertain business implications. Case '+str(i)+'.'
  ev={'id':digest(text),'text':text,'type':'illustrative_only','source':'Isolated deterministic demonstration','published_at':clock[0].isoformat(),'retrieved_at':clock[0].isoformat()}
  p={'symbol':s,'session':session,'trigger':text,'change':0,'priority':1,'source':'ILLUSTRATIVE DEMO','evidence':[ev],'simulation':True,'simulation_index':i}
  with store.transaction() as db:screen(db,tenant,s,ev,at=clock[0].isoformat())
  e=engine.ingest(tenant,p,'verified','illustrative-'+str(i))
  try:e=engine.investigate(tenant,e['id'],expected=session)
  except ValueError:continue
  events.append(e)
  if e['status']=='needs' and i in (1,3,6):
   output={'interpretable':True,'reason':'DETERMINISTIC SIMULATION: scoped preference, not a live Nemotron interpretation.',
    'rule':{'scope':'opportunity_note','sector':e['sector'],'max_position_pct':3,'max_sector_pct':30,'effect':'allow','expires_days':90}}
   proposed=engine.propose(tenant,e['id'],'ILLUSTRATIVE user permits bounded research notes.',trace={'output':output,'provider':'DETERMINISTIC SIMULATION'})
   engine.approve_rule(tenant,proposed['candidate']['id'])
  if e['status']=='needs' and i%5==0:engine.decide(tenant,e['id'],'reject',e['version'])
  if i<100 and i%3==0:
   later={**ev,'id':digest('later'+str(i)),'text':'ILLUSTRATIVE later corroboration or contradiction of the synthetic thesis.','published_at':(clock[0]+timedelta(days=6)).isoformat()}
   outcome=('supported','contradicted','mixed')[i//3%3]
   evaluate(store,tenant,e['id'],7,[later],{'outcome':outcome,'summary':'ILLUSTRATIVE deterministic retrospective; not measured AI quality.'},at=(clock[0]+timedelta(days=8)).isoformat())
 from .universe import maintain
 maintain(store,tenant)
 result={'seed':'illustrative-v3','events':120,'label':'ILLUSTRATIVE DEMO DATA — simulated chronology, deterministic interpretation, no live AI or investment results','created':now()}
 with store.transaction() as db:store.put(db,tenant,'demo','seed',result)
 return result
