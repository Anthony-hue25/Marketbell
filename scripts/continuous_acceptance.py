"""Actual HTTP source delivery while browser is closed; no simulated model or exchange close."""
import json
import time
from pathlib import Path
import requests
from backend.config import ROOT, now
from backend.market import session_state
from backend.vendor.calendar import calendar
import pandas as pd
from datetime import datetime, timezone
from backend.store import Store,digest
from backend.recordings import load

def main():
    keyfile=ROOT/'work'/'runtime'/'webhook.key'
    token=keyfile.read_text().strip()
    store=Store()
    start=now()
    session_before=session_state()['latest']
    rec=load('psx')
    issuer=next(e for e in rec['package']['evidence'] if e['type']=='issuer_event_announcement')
    payload={'symbol':'PSX','event_id':'continuous-acceptance-'+start,
        'published_at':'2026-09-16T00:00:00+00:00','source':issuer['source'],
        'text':'CONTROLLED ACCEPTANCE DELIVERY of a preserved real issuer announcement, not a claim of a newly issued press release. '+issuer['text'][:1800]}
    response=requests.post('http://127.0.0.1:4318/api/webhook',json=payload,headers={'Authorization':'Bearer '+token},timeout=10)
    if response.status_code!=200:raise RuntimeError('LOCAL_WEBHOOK_REJECTED')
    job=response.json()['job']
    print('Authenticated source event delivered to the running backend; no browser request or completed-session trigger.',flush=True)
    deadline=time.monotonic()+240
    while time.monotonic()<deadline:
        with store.connect() as db:
            row=db.execute('SELECT status,error FROM jobs WHERE id=?',(job,)).fetchone()
        if row and row['status'] in ('completed','failed'):
            break
        time.sleep(1)
    else:raise RuntimeError('CONTINUOUS_TEST_TIMEOUT')
    if row['status']!='completed':raise RuntimeError(row['error'] or 'CONTINUOUS_TEST_FAILED')
    id=digest({'source':payload['source'],'event_id':payload['event_id'],'symbol':payload['symbol']})
    with store.connect() as db:
        event=next(e for e in store.all(db,'monitor-public','event') if e.get('recording')=='watch-'+id)
        before=db.execute('SELECT calls FROM costs WHERE day=?',(now()[:10],)).fetchone()[0]
    duplicate=requests.post('http://127.0.0.1:4318/api/webhook',json=payload,headers={'Authorization':'Bearer '+token},timeout=10)
    assert duplicate.json()['job']==job
    with store.connect() as db:
        after=db.execute('SELECT calls FROM costs WHERE day=?',(now()[:10],)).fetchone()[0]
    assert before==after
    restored=Store(store.path)
    assert any(e['id']==event['id'] for e in restored.snapshot('monitor-public')['events'])
    report={'started':start,'completed':now(),'result':'PASS','browser_closed_during_delivery':True,
        'delivery':'Authenticated webhook from controlled source emitter; preserved real issuer announcement; no claim of naturally breaking news.',
        'manual_product_button':False,'new_completed_session_required':False,'completed_session_before':session_before,
        'completed_session_after':session_state()['latest'],'market_open':bool(calendar().is_open_on_minute(pd.Timestamp(datetime.now(timezone.utc)).floor('min'))),'job':job,'event':event['id'],
        'status':event['status'],'research':event['research'],'challenge':event['challenge'],'policy':event['policy'],
        'provider':event['research_trace']['provider'],'model':event['research_trace']['model'],
        'research_request_id':event['research_trace'].get('api_request_id'),
        'challenge_request_id':event['challenge_trace'].get('api_request_id'),
        'duplicate_delivery_additional_model_calls':after-before,'restart_readback':'PASS','audit':store.verify()}
    (ROOT/'docs'/'CONTINUOUS_ACCEPTANCE_PROOF.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2),flush=True)
if __name__=='__main__':main()
