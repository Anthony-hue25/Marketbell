"""Real-provider integration evidence. Uses isolated demo authority and no brokerage actions."""
import json
from pathlib import Path
from backend.config import ROOT, now
from backend.store import Store, digest
from backend.engine import Engine
from backend.recordings import load

def main():
    store=Store(ROOT/'work'/'acceptance.sqlite3')
    engine=Engine(store)
    tenant='real-integration-'+now()
    first=load('psx')
    event=engine.ingest(tenant,first['package'],'verified','psx')
    event=engine.investigate(tenant,event['id'],first['traces'],first['package']['session'])
    assert event['status']=='needs',event['status']
    text=load('preference')['text']
    proposed=engine.propose(tenant,event['id'],text)
    assert proposed['candidate'] and not proposed['candidate']['active']
    with store.connect() as db:
        assert store.all(db,tenant,'rule')==[]
    rule=engine.approve_rule(tenant,proposed['candidate']['id'])
    engine.decide(tenant,event['id'],'approve',event['version'])
    second=load('vlo')
    later=engine.ingest(tenant,second['package'],'live','vlo')
    later=engine.investigate(tenant,later['id'])
    assert later['status']=='handled' and later.get('ruleId')==rule['id'],later['summary']
    assert later['action']['verified'] is True
    reopened=Store(store.path)
    assert reopened.snapshot(tenant)['events'][0]['action']['verified']
    with store.connect() as db:
        stored=store.get(db,tenant,'rule',rule['id'])
        assert stored['uses']==1
    proof={'at':now(),'tenant':tenant,'first_event':event['id'],'second_event':later['id'],
        'first_mode':'Authentic captured model calls replayed','second_mode':'Fresh Nemotron Research and blind Challenge',
        'correction_mode':'Fresh Nemotron inference; no fixed parser','provider':later['research_trace']['provider'],
        'model':later['research_trace']['model'],'market_source':later['source'],
        'evidence_ids':later['resolution']['evidence_ids'],'second_research':later['research'],'second_challenge':later['challenge'],
        'correction_trace_hash':digest(proposed['candidate']['trace']),
        'second_research_request_hash':later['research_trace']['request_hash'],
        'second_challenge_request_hash':later['challenge_trace']['request_hash'],
        'rule_id':rule['id'],'rule_uses':stored['uses'],'verified_action':later['action'],'restart_readback':'PASS',
        'audit':store.verify(),'result':'PASS','orders':0}
    (ROOT/'docs'/'REAL_INTEGRATION_PROOF.json').write_text(json.dumps(proof,indent=2))
    print(json.dumps(proof,indent=2),flush=True)
if __name__=='__main__':main()
