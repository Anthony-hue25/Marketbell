import json
from pathlib import Path
from backend.store import Store,digest
from backend.config import now
store=Store()
with store.connect() as db:
 rows=db.execute("SELECT tenant,body FROM documents WHERE kind='event' AND json_extract(body,'$.symbol')='CVX' AND json_extract(body,'$.mode')='autonomous' AND json_extract(body,'$.ruleId') IS NOT NULL AND json_extract(body,'$.shared_investigation')=1").fetchall()
 assert rows,'NO_GENUINE_RULE_REUSE'
 row=rows[-1];event=json.loads(row['body']);rule=store.get(db,row['tenant'],'rule',event['ruleId']);note=store.get(db,row['tenant'],'note',event['action']['id'])
 assert event['status']=='handled' and event.get('simulation') is not True and event['action']['hash']==digest(note) and rule['approved_at']<event['resolved_at']
 for stage in ('research','challenge'):
  trace=event[stage+'_trace'];assert trace['provider']=='Nebius Token Factory' and 'nemotron' in trace['model'].lower() and trace.get('api_request_id') and (trace.get('usage') or {}).get('prompt_tokens',0)>0
 proof={'at':now(),'result':'PASS','scope':'Real background research and verified non-trading note under explicitly approved authority in an isolated user-entered demo portfolio. No brokerage ownership or trade is claimed.','security':event['symbol'],'event':event['id'],'trigger':event['trigger'],'created':event['created'],'resolved_at':event['resolved_at'],'manual_event_button':False,'rule_approved_before_event':True,
  'approved_rule':{k:rule.get(k) for k in ('id','scope','sector','max_position_pct','max_sector_pct','effect','approved_at','expires','version')},'research':event['research'],'challenge':event['challenge'],'policy':event['policy'],'shared_public_analysis':True,'note_hash':digest(note),'readback_verified':True,
  'traces':[{k:event[stage+'_trace'].get(k) for k in ('stage','model','provider','at','requested_at','duration_ms','api_request_id','usage')} for stage in ('research','challenge')],
  'evidence':[{'id':e['id'],'source':e['source'],'type':e['type'],'published_at':e.get('published_at'),'retrieved_at':e.get('retrieved_at')} for e in event['evidence']]}
Path('docs/AUDIT_GENUINE_RULE_REUSE.json').write_text(json.dumps(proof,indent=2),encoding='utf-8');print('Fresh CVX background investigation prepared and verified a note under a previously approved Energy rule. No model calls made by this readback.')
