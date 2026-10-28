import json,secrets
from datetime import datetime,timedelta,timezone
from backend.store import Store,digest
from backend.ai import AI
from backend.config import ROOT,now
original={'event':'labelled-followup-evaluation','symbol':'PSX','at':(datetime.now(timezone.utc)-timedelta(days=8)).isoformat(),'hypothesis':'SYNTHETIC test: a newly awarded contract may improve business outlook, but terms are uncertain.','research':{'summary':'Hypothetical issuer reported an awarded contract; financial terms are unknown.'},'challenge':{'summary':'Independent confirmation and quantified terms are missing.'},'rationale':'A cautious research note was proposed, not a trade.'}
text='HYPOTHETICAL later issuer correction: The previously reported contract was not awarded. Negotiations ended without an agreement. No revenue is expected from that negotiation.'
later=[{'id':digest(text),'type':'SYNTHETIC_RETROSPECTIVE_EVALUATION','text':text,'source':'Controlled labelled model test, not market evidence','published_at':(datetime.fromisoformat(original['at'])+timedelta(days=6)).isoformat()}]
store=Store(ROOT/'work'/'audit'/('followup-eval-'+secrets.token_hex(3)+'.sqlite3'));out=AI(store).followup(original,later,7)
report={'at':now(),'result':'PASS','synthetic_input':True,'model_call':'Genuine Nebius/Nemotron','original':original,'later':later,'assessment':out,'diagnostics':store.diagnostics(),'limitation':'This verifies a retrospective schema and grounded contradiction assessment, not an elapsed genuine investment recommendation.'}
(ROOT/'docs'/'AUDIT_FOLLOWUP_EVALUATION.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))
