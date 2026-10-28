import json,secrets
from pathlib import Path
from backend.ai import AI
from backend.store import Store,digest
from backend.config import ROOT,now
p=ROOT/'docs'/'AUDIT_AI_EVALUATION.json';report=json.loads(p.read_text(encoding='utf-8'));store=Store(ROOT/'work'/'audit'/('risk-conditional-'+secrets.token_hex(3)+'.sqlite3'));ai=AI(store)
text='HYPOTHETICAL evaluation only: Issuer states that one refinery unit is temporarily shut after an equipment incident. No injuries are reported. Duration, repair costs, insurance and earnings effects remain undetermined.'
event={'id':'conditional-risk','symbol':'PSX','trigger':'Evaluate risk conditional on this hypothetical issuer statement being true. Synthetic provenance means no real-world action, but must not replace assessment of the described business risk. Do not invent numerical impacts.', 'evidence':[{'id':digest(text),'type':'LABELLED_HYPOTHETICAL','source':'Local model evaluation','retrieved_at':now(),'text':text}]}
ops=[]
for stage in ('RESEARCH','CHALLENGE'):
 t=ai.analyze(stage,event);ops.append({k:t.get(k) for k in ('stage','model','at','duration_ms','usage','api_request_id','output')})
report['conditional_risk_retest']={'reason':'Original test confounded scenario materiality with synthetic provenance. Original results are retained above. This clarifies hypothetical evaluation only; production prompts were not tuned to expected answers.','operations':ops,'diagnostics':store.diagnostics()}
p.write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(ops,indent=2))
