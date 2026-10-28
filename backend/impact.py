"""Durable operational facts, immutable original decisions, append-only later reviews.
Metrics describe note preparation and research, never investment returns or orders.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from .config import now
from .store import encoded, digest

ZONE=ZoneInfo('America/Port_of_Spain')
HORIZONS=(7,30,90)
SCHEMA='''
CREATE TABLE IF NOT EXISTS lifecycle(
 tenant TEXT,event TEXT,stage TEXT,key TEXT,at TEXT,body TEXT NOT NULL,
 PRIMARY KEY(tenant,event,stage,key));
CREATE TABLE IF NOT EXISTS impact_cases(
 tenant TEXT,event TEXT,origin TEXT,created TEXT,status TEXT,material INTEGER,
 autonomous INTEGER,attention INTEGER,rule_id TEXT,user_involved INTEGER,body TEXT NOT NULL,
 PRIMARY KEY(tenant,event));
CREATE INDEX IF NOT EXISTS impact_period ON impact_cases(tenant,origin,created,status);
CREATE INDEX IF NOT EXISTS impact_attention ON impact_cases(tenant,attention,created);
CREATE TABLE IF NOT EXISTS decision_snapshots(
 tenant TEXT,event TEXT,at TEXT,origin TEXT,body TEXT NOT NULL,hash TEXT NOT NULL,
 PRIMARY KEY(tenant,event));
CREATE TABLE IF NOT EXISTS evaluations(
 tenant TEXT,event TEXT,horizon INTEGER,at TEXT,outcome TEXT,body TEXT NOT NULL,hash TEXT NOT NULL,
 PRIMARY KEY(tenant,event,horizon));
CREATE TABLE IF NOT EXISTS screened_news(
 tenant TEXT,id TEXT,at TEXT,symbol TEXT,body TEXT NOT NULL,PRIMARY KEY(tenant,id));
CREATE INDEX IF NOT EXISTS screened_period ON screened_news(tenant,at);
CREATE TABLE IF NOT EXISTS news_identity(tenant TEXT,id TEXT,at TEXT,body TEXT NOT NULL,PRIMARY KEY(tenant,id));
CREATE INDEX IF NOT EXISTS news_identity_period ON news_identity(tenant,at);
CREATE TABLE IF NOT EXISTS impact_reports(
 tenant TEXT,id TEXT,at TEXT,kind TEXT,event TEXT,body TEXT NOT NULL,PRIMARY KEY(tenant,id));
CREATE INDEX IF NOT EXISTS reports_period ON impact_reports(tenant,at);
'''

IMMUTABLE_TABLES=('lifecycle','decision_snapshots','evaluations','screened_news','news_identity','impact_reports')

def initialize(store):
    if store.dialect=='postgres':
        with store.connect() as db:
            db.execute('BEGIN READ COMMITTED');db._raw.execute('SELECT pg_advisory_xact_lock(724100002)')
            db._raw.execute(SCHEMA)
            for table in IMMUTABLE_TABLES:
                db._raw.execute(f"CREATE OR REPLACE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION mb_immutable('IMMUTABLE_HISTORY')")
                db._raw.execute(f"CREATE OR REPLACE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION mb_immutable('IMMUTABLE_HISTORY')")
            db._raw.execute("CREATE OR REPLACE TRIGGER immutable_universe_history BEFORE UPDATE OR DELETE ON documents FOR EACH ROW WHEN (OLD.kind IN ('universe_history','rule_revision','rule_evaluation')) EXECUTE FUNCTION mb_immutable('IMMUTABLE_HISTORY')")
            db._raw.execute("CREATE INDEX IF NOT EXISTS document_created ON documents(tenant,kind,json_extract(body,'$.created'))")
            db._raw.execute("CREATE INDEX IF NOT EXISTS document_at ON documents(tenant,kind,json_extract(body,'$.at'))")
            db.execute('INSERT OR IGNORE INTO migrations VALUES(?,?)',(4,now()))
            db.commit()
        _migrate(store)
        return
    with store.connect() as db:
        db.executescript(SCHEMA)
        for table in ('lifecycle','decision_snapshots','evaluations','screened_news','news_identity','impact_reports'):
            for op in ('UPDATE','DELETE'):
                db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'IMMUTABLE_HISTORY'); END")
        db.execute("CREATE TRIGGER IF NOT EXISTS immutable_universe_history BEFORE UPDATE ON documents WHEN OLD.kind IN ('universe_history','rule_revision','rule_evaluation') BEGIN SELECT RAISE(ABORT,'IMMUTABLE_HISTORY'); END")
        db.execute("CREATE TRIGGER IF NOT EXISTS immutable_universe_history_delete BEFORE DELETE ON documents WHEN OLD.kind IN ('universe_history','rule_revision','rule_evaluation') BEGIN SELECT RAISE(ABORT,'IMMUTABLE_HISTORY'); END")
        db.execute("CREATE INDEX IF NOT EXISTS document_created ON documents(tenant,kind,json_extract(body,'$.created'))")
        db.execute("CREATE INDEX IF NOT EXISTS document_at ON documents(tenant,kind,json_extract(body,'$.at'))")
        db.execute("CREATE TRIGGER IF NOT EXISTS rule_eval_no_update BEFORE UPDATE ON documents WHEN OLD.kind='rule_evaluation' BEGIN SELECT RAISE(ABORT,'IMMUTABLE_HISTORY'); END")
        db.execute("CREATE TRIGGER IF NOT EXISTS rule_eval_no_delete BEFORE DELETE ON documents WHEN OLD.kind='rule_evaluation' BEGIN SELECT RAISE(ABORT,'IMMUTABLE_HISTORY'); END")
        db.execute('INSERT OR IGNORE INTO migrations VALUES(4,CURRENT_TIMESTAMP)')
    _migrate(store)

def _migrate(store):
    # Incremental and idempotent migration, preserving original event/trace timestamps.
    with store.transaction() as db:
        for row in db.execute("SELECT d.tenant,d.body FROM documents d LEFT JOIN impact_cases i ON i.tenant=d.tenant AND i.event=d.id WHERE d.kind='event' AND i.event IS NULL").fetchall():
            record(store,db,row['tenant'],json.loads(row['body']),backfilled=True)
        if not db.execute('SELECT 1 FROM migrations WHERE version=6').fetchone():
            for row in db.execute("SELECT tenant,body FROM documents WHERE kind='event'").fetchall():record(store,db,row['tenant'],json.loads(row['body']),backfilled=True)
            db.execute('INSERT INTO migrations VALUES(6,CURRENT_TIMESTAMP)')
        if not db.execute('SELECT 1 FROM migrations WHERE version=7').fetchone():
            for row in db.execute('SELECT * FROM screened_news ORDER BY at').fetchall():
                item=json.loads(row['body']);key=news_key(item)
                db.execute('INSERT OR IGNORE INTO news_identity VALUES(?,?,?,?)',(row['tenant'],key,row['at'],row['body']))
                cursor=store.get(db,row['tenant'],'watch_cursor',row['symbol'])
                if cursor and item['id'] in cursor['seen']:
                    cursor['seen']=list(dict.fromkeys(cursor['seen']+[key]))[-500:];store.put(db,row['tenant'],'watch_cursor',row['symbol'],cursor)
            db.execute('INSERT INTO migrations VALUES(7,CURRENT_TIMESTAMP)')
        if not db.execute('SELECT 1 FROM migrations WHERE version=8').fetchone():
            for row in db.execute("SELECT tenant,id,body FROM documents WHERE kind='universe_recommendation' AND json_extract(body,'$.action')='compare_replacement'").fetchall():
                old=json.loads(row['body']);old.update(status='superseded',reason='Historical holding review had no independently investigated replacement candidate; no ownership change was authorized.',version=old['version']+1)
                store.put(db,row['tenant'],'universe_recommendation',row['id'],old);store.append(db,row['tenant'],'Legacy proposal superseded',row['id']+': original record remains in immutable universe history.')
            db.execute('INSERT INTO migrations VALUES(8,CURRENT_TIMESTAMP)')

def origin(event):
    return 'illustrative' if event.get('simulation') else 'replay' if event.get('mode')=='verified' else 'genuine'

def stage(db,tenant,event,name,at,body,key='initial'):
    db.execute('INSERT OR IGNORE INTO lifecycle VALUES(?,?,?,?,?,?)',(tenant,event,name,key,at,encoded(body)))

def record(store,db,tenant,event,backfilled=False):
    if not event or 'created' not in event:return
    eid=event['id'];at=now();old=db.execute('SELECT body FROM impact_cases WHERE tenant=? AND event=?',(tenant,eid)).fetchone()
    old=json.loads(old[0]) if old else {}
    persisted_note=store.get(db,tenant,'note',event.get('action',{}).get('id')) if event.get('action') else None
    correction=store.get(db,tenant,'preference',eid)
    user=bool((persisted_note and persisted_note.get('authority')=='explicit user approval') or correction or old.get('user_involved') or event.get('user_involved') or event.get('action',{}).get('authority')=='explicit user approval' or event['status']=='rejected')
    auto=event['status']=='handled' and not user and (not event.get('action') or bool(event.get('ruleId')))
    material=bool(event.get('research',{} ) and event['research'].get('material') or event.get('challenge',{}) and event['challenge'].get('material'))
    o=origin(event)
    projection={'id':eid,'symbol':event['symbol'],'created':event['created'],'status':event['status'],
        'summary':event['summary'],'rule_id':event.get('ruleId'),'user_involved':user,'origin':o,
        'kind':event['kind'],'material':material,'autonomous':auto,'resolved_at':event.get('resolved_at'),
        'disagreement':bool(event.get('research') and event.get('challenge') and (event['research']['decision']!=event['challenge']['decision'] or event['research']['classification']!=event['challenge']['classification'])),
        'error':event.get('error'),'policy':event.get('policy'),'source_event':event.get('recording')}
    db.execute('INSERT INTO impact_cases VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(tenant,event) DO UPDATE SET status=excluded.status,material=excluded.material,autonomous=excluded.autonomous,attention=excluded.attention,rule_id=excluded.rule_id,user_involved=excluded.user_involved,body=excluded.body',
        (tenant,eid,o,event['created'],event['status'],int(material),int(auto),int(event['status']=='needs'),event.get('ruleId'),int(user),encoded(projection)))
    stage(db,tenant,eid,'DETECTED',event['created'],{'source':event['source'],'evidence':[e['id'] for e in event['evidence']],'backfilled':backfilled})
    stage(db,tenant,eid,'TRIAGED',event['created'],{'symbol':event['symbol'],'priority':event.get('priority'),'scope':'Configured holding/watchlist; qualitative materiality remains for Nemotron','backfilled':backfilled})
    for key,name in (('research','INVESTIGATED'),('challenge','CHALLENGED')):
        if event.get(key):stage(db,tenant,eid,name,event.get(key+'_trace',{}).get('at',at),event[key])
    if event.get('policy'):
        stage(db,tenant,eid,'POLICY CHECKED',event.get('resolved_at',at),event['policy'])
        stage(db,tenant,eid,'RESOLVED',event.get('resolved_at',at),projection)
        # Never rewrite the thesis after a user decision or later evidence.
        price=None
        for ev in event['evidence']:
            if ev['type']=='daily_market_data':
                try:
                    data=json.loads(ev['text']);bar=data['bars'][-1]
                    price={'close':bar['close'],'session':bar.get('session_date'),'source':ev['source'],'kind':'completed_session','adjustment':data.get('adjustment','unknown'),'evidence_id':ev['id']}
                except (KeyError,ValueError,TypeError,IndexError):pass
        snapshot={'event':eid,'symbol':event['symbol'],'at':event.get('resolved_at',at),'origin':o,
            'evidence_refs':[e['id'] for e in event['evidence']],
            # Sealed decision-time evidence: content hashes of exactly what was read, so a later
            # revision of the same source can be distinguished from what this decision used.
            'evidence_sealed':[{'id':e['id'],'sha256':digest(e),'type':e.get('type'),'published_at':e.get('published_at'),'retrieved_at':e.get('retrieved_at'),'publisher':e.get('publisher')} for e in event['evidence']],
            'evidence_package_sha256':digest(event['evidence']),
            'research':event['research'],'challenge':event['challenge'],
            'policy':event['policy'],'disposition':event['status'],'rationale':event['summary'],
            'recommendation_type':'research consideration note' if event['kind']=='Opportunity' else 'risk/routine observation',
            'uncertainty':'Qualitative only; no calibrated probability. Disagreement vetoes preparation.',
            'price_reference':price,'hypothesis':'Original qualitative thesis: '+event['research']['summary']+' Independent countercase: '+event['challenge']['summary']+' Evaluate against later attributable facts; positive returns alone cannot establish correctness.',
            'horizons_days':list(HORIZONS),'benchmark':'No investment-return benchmark: note preparation is not a directional trade thesis.',
            'action_category':'simulated historical decision' if o=='illustrative' else 'historical recorded inference' if o=='replay' else 'market observation / permitted non-trading note',
            'backfilled':backfilled}
        h=digest(snapshot)
        inserted=db.execute('INSERT OR IGNORE INTO decision_snapshots VALUES(?,?,?,?,?,?)',(tenant,eid,snapshot['at'],o,encoded(snapshot),h)).rowcount
        if inserted:store.append(db,tenant,'Original decision snapshot',eid+' '+h)
    if user and not old.get('user_involved'):stage(db,tenant,eid,'USER DECISION',at,projection)
    if event.get('ruleId'):stage(db,tenant,eid,'RULE APPLIED',at,{'rule':event['ruleId'],'version':event.get('rule_version'),'question_avoided':auto})
    if event['status']=='failed':stage(db,tenant,eid,'FAILED',at,projection,key=event.get('error','unknown'))

def news_key(item):
    story=re.sub(r'[^a-z0-9]','',item.get('text','').rsplit(' - ',1)[0].lower())
    return digest({'story':story})

def screen(db,tenant,symbol,item,at=None):
    # A syndicated normalized headline is one screened item across monitored securities.
    db.execute('INSERT OR IGNORE INTO screened_news VALUES(?,?,?,?,?)',(tenant,item['id'],at or now(),symbol,encoded(item)))
    return db.execute('INSERT OR IGNORE INTO news_identity VALUES(?,?,?,?)',(tenant,news_key(item),at or now(),encoded(item))).rowcount

def evaluate(store,tenant,eid,horizon,later=None,assessment=None,at=None,market_observation=None):
    at=at or now()
    if horizon not in HORIZONS:raise ValueError('EVALUATION_HORIZON_INVALID')
    with store.transaction() as db:
        row=db.execute('SELECT * FROM decision_snapshots WHERE tenant=? AND event=?',(tenant,eid)).fetchone()
        if not row:raise ValueError('SNAPSHOT_NOT_FOUND')
        snap=json.loads(row['body'])
        if datetime.fromisoformat(at)<datetime.fromisoformat(row['at'])+timedelta(days=horizon):raise ValueError('FOLLOWUP_NOT_DUE')
        later=later or []
        # Later evidence must postdate the original decision and exist by review time.
        valid=[]
        for item in later:
            try:
                published=datetime.fromisoformat(item['published_at'].replace('Z','+00:00'))
                if datetime.fromisoformat(row['at'])<published<=min(datetime.fromisoformat(at),datetime.fromisoformat(row['at'])+timedelta(days=horizon)):valid.append(item)
            except (KeyError,ValueError,TypeError,AttributeError):continue
        outcome='not_evaluable' if not valid else 'mixed'
        thesis='Missing later attributable evidence.' if not valid else 'Later headlines are available, but do not alone establish the original thesis. Further assessment is required.'
        if assessment and valid:
            outcome=assessment['outcome'];thesis=assessment['summary']
        performance={'status':'not_evaluated','reason':'No validated horizon-matched price observations and total-return benchmark available.'}
        reference=snap.get('price_reference')
        if reference and market_observation:
            end=datetime.fromisoformat(row['at'])+timedelta(days=horizon)
            try:
                from .vendor.calendar import calendar
                date=calendar().session_close(market_observation['session']).to_pydatetime()
                close=market_observation['close'];initial=reference['close']
                decided=datetime.fromisoformat(row['at'])
                # Revised history (splits, provider corrections) can rescale the decision-time close.
                # Prefer a reference close retrieved together with the later close (one basis); the
                # decision-time snapshot itself is never rewritten.
                revision=None
                same=market_observation.get('reference_close_same_retrieval')
                if type(same) in (int,float) and same>0:
                    if same!=initial:revision={'decision_time_close':initial,'provider_close_now':same,'note':'Provider history differs from the decision-time snapshot; comparison uses one retrieval basis. Original snapshot unchanged.'}
                    initial=same;basis_ok=True
                else:
                    basis_ok=reference.get('adjustment') not in (None,'unknown') and reference.get('adjustment')==market_observation.get('adjustment')
                if not basis_ok:
                    performance={'status':'not_evaluated','reason':'Price adjustment basis unknown or different between the decision-time reference and the later observation; values are not comparable.'}
                elif type(close) in (int,float) and type(initial) in (int,float) and 0<close<1e12 and 0<initial<1e12 and market_observation['session']>reference['session'] and decided<date<=end:
                    performance={'status':'observed','reference':reference,'later':market_observation,'close_change_pct':round((close/initial-1)*100,4),'benchmark':None,'provider_revision':revision,'reason':'Provider-adjusted close change only; not total return, alpha, a trade result or proof of thesis accuracy. No validated comparison benchmark.'}
            except (KeyError,TypeError,ValueError):pass
        body={'event':eid,'horizon_days':horizon,'reviewed_at':at,'original_snapshot_hash':row['hash'],
            'outcome':outcome,'operational_correctness':'Original policy and action records preserved; investment correctness is a separate question.',
            'thesis_quality':thesis,'market_performance':performance,
            'user_usefulness':'Not provided; see separately attributed feedback.','later_evidence':valid,
            'assessment':assessment,'late':datetime.fromisoformat(at)>datetime.fromisoformat(row['at'])+timedelta(days=horizon+1)}
        h=digest(body)
        if db.execute('INSERT OR IGNORE INTO evaluations VALUES(?,?,?,?,?,?,?)',(tenant,eid,horizon,at,outcome,encoded(body),h)).rowcount:
            stage(db,tenant,eid,'FOLLOWED UP',at,body,key=str(horizon));store.append(db,tenant,'Outcome reviewed',eid+' '+h)
        return body

def report(store,tenant,kind,text,eid=None):
    if kind not in ('missed_risk','missed_opportunity','wrong_ticker','false_alert','unsupported_conclusion','useful','not_useful'):raise ValueError('FEEDBACK_KIND_INVALID')
    if type(text) is not str or not 10<=len(text)<=1000:raise ValueError('FEEDBACK_TEXT_INVALID')
    with store.transaction() as db:
        if eid and not db.execute('SELECT 1 FROM impact_cases WHERE tenant=? AND event=?',(tenant,eid)).fetchone():raise ValueError('EVENT_NOT_FOUND')
        body={'kind':kind,'text':text,'event':eid,'at':now(),'attribution':'User report; not independently established'}
        id=digest({'kind':kind,'text':text,'event':eid})
        db.execute('INSERT OR IGNORE INTO impact_reports VALUES(?,?,?,?,?,?)',(tenant,id,body['at'],kind,eid,encoded(body)))
        store.append(db,tenant,'Impact feedback',kind+': '+text)
        return {'id':id,**body}

def summary(store,tenant,days=30,origin_filter='genuine',offset=0):
    if days not in (1,7,30,90) or type(offset) is not int or not 0<=offset<=100000:raise ValueError('IMPACT_PERIOD_INVALID')
    current=datetime.now(ZONE)
    since=(current.replace(hour=0,minute=0,second=0,microsecond=0) if days==1 else current-timedelta(days=days)).astimezone(timezone.utc).isoformat()
    with store.connect() as db:
        args=(tenant,origin_filter,since)
        sql=' FROM impact_cases WHERE tenant=? AND origin=? AND created>=?'
        counts=dict(db.execute('SELECT count(*) AS developments,COALESCE(sum(material),0) AS material,COALESCE(sum(autonomous),0) AS autonomous,COALESCE(sum(attention),0) AS attention,COALESCE(sum(user_involved),0) AS user_involved,COALESCE(sum(CASE WHEN rule_id IS NOT NULL AND autonomous=1 THEN 1 ELSE 0 END),0) AS rule_reused,COALESCE(sum(status="blocked"),0) AS blocked,COALESCE(sum(status IN ("held","failed","queued","investigating")),0) AS unresolved'+sql,args).fetchone())
        counts['attention']=db.execute('SELECT count(*) FROM impact_cases WHERE tenant=? AND origin=? AND attention=1',(tenant,origin_filter)).fetchone()[0]
        counts['news_screened']=db.execute('SELECT count(*) FROM news_identity WHERE tenant=? AND at>=?',('monitor-public' if origin_filter=='genuine' else tenant,since)).fetchone()[0] if origin_filter!='replay' else 0
        cases=[json.loads(r[0]) for r in db.execute('SELECT body'+sql+' ORDER BY created DESC LIMIT 20 OFFSET ?',args+(offset,))]
        evaluations=[json.loads(r[0]) for r in db.execute('SELECT e.body FROM evaluations e JOIN impact_cases i ON i.tenant=e.tenant AND i.event=e.event WHERE e.tenant=? AND i.origin=? AND e.at>=? ORDER BY e.at DESC LIMIT 30',args)]
        snapshots={r['event']:json.loads(r['body']) for r in db.execute('SELECT s.event,s.body FROM decision_snapshots s JOIN impact_cases i ON i.tenant=s.tenant AND i.event=s.event WHERE s.tenant=? AND i.origin=? AND (s.at>=? OR EXISTS(SELECT 1 FROM evaluations e WHERE e.tenant=s.tenant AND e.event=s.event AND e.at>=?)) ORDER BY EXISTS(SELECT 1 FROM evaluations e WHERE e.tenant=s.tenant AND e.event=s.event) DESC,s.at DESC LIMIT 10',args+(since,))}
        first=db.execute('SELECT min(created) FROM impact_cases WHERE tenant=? AND origin=?',(tenant,origin_filter)).fetchone()[0]
        weeks=[dict(r) for r in db.execute('SELECT strftime("%Y-W%W",datetime(created,"-4 hours")) AS week,count(*) AS cases,sum(autonomous) AS autonomous,sum(attention) AS attention,sum(rule_id IS NOT NULL AND autonomous=1) AS rules,sum(status IN ("failed","held","queued","investigating")) AS unresolved'+sql+' GROUP BY week ORDER BY week',args)]
        reports=[json.loads(r[0]) for r in db.execute('SELECT body FROM impact_reports WHERE tenant=? AND at>=? ORDER BY at DESC LIMIT 30',(tenant,since))]
        rules=[{'id':r['rule_id'],'applications':r['n'],'questions_avoided':r['n']} for r in db.execute('SELECT rule_id,count(*) AS n'+sql+' AND rule_id IS NOT NULL AND autonomous=1 GROUP BY rule_id',args)]
        lifecycle=[]  # Full per-event history is fetched on demand via the authorized detail endpoint.
        universe={'discovered':db.execute("SELECT count(*) FROM documents d JOIN impact_cases i ON i.tenant=d.tenant AND i.event=json_extract(d.body,'$.event') WHERE d.tenant=? AND d.kind='universe_history' AND json_extract(d.body,'$.kind')='discovered' AND i.origin=? AND json_extract(d.body,'$.at')>=?",args).fetchone()[0]}
        operations=[dict(r) for r in db.execute("SELECT json_extract(d.body,'$.action') AS action,json_extract(d.body,'$.status') AS status,count(*) AS count,sum(json_extract(d.body,'$.ruleId') IS NOT NULL) AS learned FROM documents d JOIN impact_cases i ON i.tenant=d.tenant AND i.event=json_extract(d.body,'$.event') WHERE d.tenant=? AND d.kind='universe_recommendation' AND i.origin=? AND json_extract(d.body,'$.created')>=? GROUP BY 1,2",args)]
        universe['automatically_archived']=db.execute("SELECT count(*) FROM documents WHERE tenant=? AND kind='universe_history' AND json_extract(body,'$.kind')='archived' AND json_extract(body,'$.automatic')=1 AND json_extract(body,'$.origin')=? AND json_extract(body,'$.at')>=?",args).fetchone()[0]
        universe.update(operations=operations,automatically_organized=sum(r['count'] for r in operations if r['status']=='applied'),learned_applications=sum(r['learned'] for r in operations if r['status']=='applied'))
        comparisons=[dict(r) for r in db.execute("SELECT json_extract(d.body,'$.action') AS action,json_extract(d.body,'$.status') AS disposition,e.outcome,count(*) AS horizon_reviews FROM documents d JOIN impact_cases i ON i.tenant=d.tenant AND i.event=json_extract(d.body,'$.event') JOIN evaluations e ON e.tenant=d.tenant AND e.event=i.event WHERE d.tenant=? AND d.kind='universe_recommendation' AND i.origin=? AND e.at>=? GROUP BY 1,2,3",args)]
        failure_counts={r[0]:r[1] for r in db.execute('SELECT status,count(*)'+sql+' GROUP BY status',args)}
        disagreements=db.execute('SELECT count(*)'+sql+' AND json_extract(body,"$.disagreement")=1',args).fetchone()[0]
    mature=bool(first and datetime.fromisoformat(first)<=datetime.now(timezone.utc)-timedelta(days=days))
    return {'days':days,'newsScreenedScope':'shared' if origin_filter=='genuine' else 'isolated','timezone':str(ZONE),'since':since,'origin':origin_filter,'counts':counts,'cases':cases,'offset':offset,
        'nextOffset':offset+20 if offset+20<counts['developments'] else None,'evaluations':evaluations,'snapshots':snapshots,
        'firstRecord':first,'sufficientHistory':mature,'weeks':weeks,'rules':rules,'reports':reports,'lifecycle':lifecycle,
        'universe':universe,'candidateComparisons':comparisons,'failures':{'statusCounts':failure_counts,'disagreements':disagreements},
        'definitions':{'autonomous':'Handled outcome with no case-specific user decision; either routine no-action or a verified note under an approved rule. Blocks and unresolved cases do not count.',
        'questions_avoided':'One distinct verified note prepared under an approved standing rule, with no case-specific user decision. This estimates a repeat approval avoided; not minutes saved.',
        'material':'At least one independent review identifies materiality; disagreement remains visible.',
        'news_screened':'Unique normalized headlines received by the shared public-source monitor across securities. Shared ingestion is reused across workspaces; not a count of articles read for this portfolio, full articles, or comprehensive coverage.',
        'attention':'All currently open decisions in this origin, including those detected before the selected period.',
        'denominator':'All unique cases detected in this period and origin, including failures and policy blocks.'}}

