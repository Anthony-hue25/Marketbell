"""Same-origin WSGI API. Only isolated demo portfolios are accepted in this release."""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import datetime as _dt
from .config import ROOT, PRODUCTION, MODEL, SYMBOLS, now, timestamp
from .store import Store, digest
from .engine import Engine
from .explain import explain, operations
from .worker import Worker
from .market import session_state
from .recordings import available, load
from .market import sanitize
from . import universe

class Application:
    def __init__(self,store=None,start_worker=True):
        if PRODUCTION:
            configured=urlparse(os.environ.get('MARKETBELL_PUBLIC_ORIGIN',''))
            if configured.scheme!='https' or not configured.hostname or configured.username or configured.query or configured.path not in ('','/'):
                raise ValueError('VALID_HTTPS_PUBLIC_ORIGIN_REQUIRED')
        self.store=store or Store()
        self.engine=Engine(self.store)
        self.worker=Worker(self.store,self.engine)
        self.rates={}
        self.rate_lock=threading.Lock()
        self.allow_live=os.environ.get('MARKETBELL_ALLOW_LIVE','0' if PRODUCTION else '1')=='1'
        # MARKETBELL_WORKER=0 runs the website/API alone; monitoring then runs in a separate,
        # independently scaled worker process (backend.worker_main). Default keeps local dev simple.
        self.worker_enabled=start_worker and os.environ.get('MARKETBELL_WORKER','1')=='1'
        if self.worker_enabled:
            self.worker.start()

    @staticmethod
    def client_address(environ):
        """Client IP for rate limits. Behind Azure Container Apps ingress REMOTE_ADDR is the proxy, so
        every visitor would share one limit; trust exactly MARKETBELL_TRUSTED_PROXY_HOPS forwarders."""
        hops=int(os.environ.get('MARKETBELL_TRUSTED_PROXY_HOPS','0') or 0)
        forwarded=[p.strip() for p in environ.get('HTTP_X_FORWARDED_FOR','').split(',') if p.strip()]
        if hops>0 and len(forwarded)>=hops:
            candidate=forwarded[-hops]
            if len(candidate)<=64 and all(c in '0123456789abcdefABCDEF.:' for c in candidate):
                return candidate
        return environ.get('REMOTE_ADDR','unknown')

    def limited(self,key,limit=60,period=60):
        with self.rate_lock:
            at=time.time()
            if len(self.rates)>5000:
                self.rates={k:v for k,v in self.rates.items() if v[0]+3600>at}
            when,n=self.rates.get(key,(at,0))
            if at-when>=period: when,n=at,0
            if n>=limit: raise ValueError('RATE_LIMITED')
            self.rates[key]=(when,n+1)

    def session(self,environ):
        cookie=SimpleCookie()
        try:
            cookie.load(environ.get('HTTP_COOKIE',''))
            token=cookie['mb_session'].value if 'mb_session' in cookie else ''
        except Exception:
            token=''
        with self.store.connect() as db:
            row=db.execute('SELECT tenant,expires FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone() if len(token)==64 else None
        if row and row['expires']>time.time():
            return row['tenant'],token,False
        self.limited('new-session:'+self.client_address(environ),int(os.environ.get('MARKETBELL_NEW_SESSIONS_PER_HOUR','20')),3600)
        token=secrets.token_hex(32)
        tenant='demo-'+secrets.token_hex(12)
        with self.store.transaction() as db:
            db.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
            if db.execute("SELECT count(*) FROM documents WHERE kind='portfolio'").fetchone()[0]>=2000 or db.execute('SELECT count(*) FROM sessions').fetchone()[0]>=2000:
                raise ValueError('DEMO_CAPACITY_REACHED')
            db.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),tenant,time.time()+30*86400))
        self.engine.ensure(tenant)
        return tenant,token,True

    @staticmethod
    def public_snapshot(snapshot):
        # Preserve full evidence server-side; requests and raw model transcripts are not UI data.
        allowed=('stage','model','provider','at','duration_ms','usage','api_request_id','skipped','reason')
        for event in snapshot.get('events',[]):
            for key in ('research_trace','challenge_trace'):
                if event.get(key):event[key]={k:event[key].get(k) for k in allowed}
            # Derived on read from the stored record: labels facts, interpretation, proposals and verified actions.
            event['explanation']=explain(event)
        if snapshot.get('candidate'):snapshot['candidate'].pop('trace',None)
        for rule in snapshot.get('rules',[]):rule.pop('trace',None)
        return snapshot

    def dispatch(self,environ):
        path=environ.get('PATH_INFO','/')
        method=environ['REQUEST_METHOD']
        if path=='/livez' and method=='GET':
            return {'status':'alive'},[]
        if path in ('/healthz','/readyz') and method=='GET':
            # Readiness: the database answers and migrations are present. Never reports secrets.
            try:
                with self.store.connect() as db:
                    db.execute('SELECT 1').fetchone()
                    version=db.execute('SELECT max(version) FROM migrations').fetchone()[0]
            except Exception:
                raise ValueError('DATABASE_UNAVAILABLE') from None
            body={'status':'ok','database':'ready','schema':version,'execution_enabled':False,'worker_in_process':self.worker_enabled}
            if self.worker_enabled:body['worker_alive']=self.worker.alive()
            return body,[]
        host=environ.get('HTTP_HOST','127.0.0.1:4317')
        origin=os.environ.get('MARKETBELL_PUBLIC_ORIGIN','http://'+host)
        allowed_host=urlparse(origin).netloc
        if PRODUCTION and not os.environ.get('MARKETBELL_PUBLIC_ORIGIN'):
            raise ValueError('PUBLIC_ORIGIN_REQUIRED')
        if host!=allowed_host or not PRODUCTION and host.split(':')[0] not in ('127.0.0.1','localhost','testserver'):
            raise ValueError('HOST_REJECTED')
        if path=='/api/diagnostics' and method=='GET':
            admin=os.environ.get('MARKETBELL_ADMIN_TOKEN','')
            if len(admin)<24 or not hmac.compare_digest(environ.get('HTTP_AUTHORIZATION',''),'Bearer '+admin):
                raise ValueError('ADMIN_REQUIRED')
            return self.store.diagnostics(),[]
        if path=='/api/webhook' and method=='POST':
            configured=os.environ.get('MARKETBELL_WEBHOOK_TOKEN','')
            key_file=os.environ.get('MARKETBELL_WEBHOOK_KEY_FILE')
            if not configured and key_file and Path(key_file).is_file():
                configured=Path(key_file).read_text(encoding='utf-8').strip()
            if len(configured)<24 or not hmac.compare_digest(environ.get('HTTP_AUTHORIZATION',''),'Bearer '+configured):
                raise ValueError('WEBHOOK_AUTH_REQUIRED')
            self.limited('webhook',int(os.environ.get('MARKETBELL_WEBHOOK_PER_MINUTE','20')),60)
            size=int(environ.get('CONTENT_LENGTH') or 0)
            if not 0<size<=6000 or not environ.get('CONTENT_TYPE','').startswith('application/json'):
                raise ValueError('WEBHOOK_BODY_INVALID')
            item=json.loads(environ['wsgi.input'].read(size))
            if type(item) is not dict or item.get('symbol') not in SYMBOLS or type(item.get('event_id')) is not str or not 1<=len(item['event_id'])<=150 or type(item.get('text')) is not str or not 20<=len(item['text'])<=3000:
                raise ValueError('WEBHOOK_EVENT_INVALID')
            published=timestamp(item.get('published_at',''))
            from datetime import datetime,timedelta,timezone
            if published>datetime.now(timezone.utc)+timedelta(minutes=5) or published<datetime.now(timezone.utc)-timedelta(days=90):
                raise ValueError('WEBHOOK_TIMESTAMP_INVALID')
            text=sanitize(item['text'],3000)
            source=item.get('source','Authenticated integration webhook')
            if type(source) is not str or len(source)>400 or '?' in source or '@' in source:
                raise ValueError('WEBHOOK_SOURCE_INVALID')
            notice={'id':digest({'source':source,'event_id':item['event_id'],'symbol':item['symbol']}),
                'source':source,'published_at':published.isoformat(),'retrieved_at':now(),'text':text,
                'type':'issuer_announcement','limitation':'Authenticated webhook delivery; source text remains untrusted and must be investigated.'}
            first=self.worker.enqueue_id('monitor-public','watch-event',item['symbol']+notice['id'])
            with self.store.connect() as db:
                prior=db.execute('SELECT payload FROM jobs WHERE id=?',(first,)).fetchone()
            status='accepted_for_investigation'
            if prior and json.loads(prior[0])['notice']['text']!=text:
                # Same source event, changed content: the earlier delivery and any decision on it stay
                # as they were; the revision is new evidence with its own investigation.
                original=notice['id']
                notice={**notice,'id':digest({'source':source,'event_id':item['event_id'],'symbol':item['symbol'],'content':digest(text)}),'revision_of':original,
                    'limitation':notice['limitation']+' Revised delivery of an earlier event; the original record is unchanged.'}
                status='revision_accepted_for_investigation'
                with self.store.transaction() as db:
                    self.store.append(db,'monitor-public','Source revision received',item['symbol']+': event '+original+' redelivered with changed content as '+notice['id']+'; original evidence and decisions preserved.')
            elif prior:
                status='duplicate_ignored'
            job=self.worker.enqueue('monitor-public','watch-event',{'symbol':item['symbol'],'notice':notice},key=item['symbol']+notice['id'])
            return {'job':job,'status':status},[]
        if path=='/api/universe' and method=='GET':
            tenant,token,fresh=self.session(environ)
            return universe.current(self.store,tenant),[]
        if path=='/api/impact-event' and method=='GET':
            tenant,token,fresh=self.session(environ);query=parse_qs(environ.get('QUERY_STRING',''));dataset=query.get('dataset',['workspace'])[0]
            if dataset not in ('workspace','monitor','replay','illustrative'):raise ValueError('IMPACT_DATASET_INVALID')
            target='monitor-public' if dataset=='monitor' else 'illustrative-shared-v3' if dataset=='illustrative' else tenant
            id=query.get('id',[''])[0]
            with self.store.connect() as db:
                event=self.store.get(db,target,'event',id)
                if not event:raise ValueError('EVENT_NOT_FOUND')
                trail=[dict(r) for r in db.execute('SELECT stage,at,body FROM lifecycle WHERE tenant=? AND event=? ORDER BY at',(target,id))]
                row=db.execute('SELECT body FROM decision_snapshots WHERE tenant=? AND event=?',(target,id)).fetchone()
                reviews=[json.loads(r[0]) for r in db.execute('SELECT body FROM evaluations WHERE tenant=? AND event=?',(target,id))]
                note=self.store.get(db,target,'note',event.get('action',{}).get('id')) if event.get('action') else None
            return {'event':self.public_snapshot({'events':[event]})['events'][0],'trail':trail,'snapshot':json.loads(row[0]) if row else None,'evaluations':reviews,'note':note},[]
        if path=='/api/impact' and method=='GET':
            from .impact import summary
            tenant,token,fresh=self.session(environ)
            query=parse_qs(environ.get('QUERY_STRING',''))
            dataset=query.get('dataset',['workspace'])[0]
            if dataset not in ('workspace','monitor','replay','illustrative'):raise ValueError('IMPACT_DATASET_INVALID')
            target='monitor-public' if dataset=='monitor' else 'illustrative-shared-v3' if dataset=='illustrative' else tenant
            result=summary(self.store,target,int(query.get('days',['30'])[0]),'illustrative' if dataset=='illustrative' else 'replay' if dataset=='replay' else 'genuine',int(query.get('offset',['0'])[0]))
            result.update(dataset=dataset,label={'workspace':'Your genuine workspace history','monitor':'Shared public-source monitoring — demo universe','replay':'Authentic recorded inference — replay workspace','illustrative':'ILLUSTRATIVE DEMO DATA — deterministic simulations; not live operation'}[dataset])
            return result,[]
        if path=='/api/state' and method=='GET':
            tenant,token,fresh=self.session(environ)
            with self.store.connect() as db:
                db.execute('BEGIN')
                state=self.public_snapshot(self.store.snapshot(tenant,db))
                monitor=self.public_snapshot(self.store.snapshot('monitor-public',db))
                soft=self.store.all(db,tenant,'preference')
                watch=self.store.get(db,'monitor-public','monitor','continuous')
                channels={k:self.store.get(db,'monitor-public','monitor',k) for k in ('scheduled',)}
                mandate=self.store.get(db,tenant,'mandate','main')
                operating_counts={'investigations':db.execute("SELECT count(DISTINCT event) FROM impact_cases WHERE tenant IN (?,?) AND origin='genuine' AND status='investigating'",(tenant,'monitor-public')).fetchone()[0],'source_material':db.execute("SELECT count(*) FROM impact_cases WHERE tenant='monitor-public' AND origin='genuine' AND material=1 AND json_extract(body,'$.source_event') LIKE 'watch-%'").fetchone()[0]}
                if os.environ.get('MARKETBELL_WATCH','1')!='1':
                    watch={**(watch or {}),'active':False,'status':'Continuous polling paused by configuration'}
                beat=self.store.get(db,'monitor-public','heartbeat','monitor') or {}
                worker_beat=self.store.get(db,'monitor-public','heartbeat','worker') or {}
                deferred=[json.loads(r[0]) for r in db.execute("SELECT body FROM documents WHERE tenant='monitor-public' AND kind='deferred_notice' AND json_extract(body,'$.state')='deferred' ORDER BY json_extract(body,'$.at') DESC LIMIT 20").fetchall()][::-1]
                calls=db.execute('SELECT calls FROM costs WHERE day=?',(now()[:10],)).fetchone()
                portfolio=state['portfolio'] or {'holdings':[],'watchlist':[]}
                followed=[h['symbol'] for h in portfolio['holdings']]+portfolio['watchlist']
                overturned={o['quiet'] for o in self.store.all(db,tenant,'quiet_overturn')}
                quiet=sorted(self.store.all(db,'monitor-public','quiet'),key=lambda q:(q['symbol'] in followed,q['at']),reverse=True)[:30]
                quiet=[{**{k:q.get(k) for k in ('id','at','symbol','reason','plain','headline','publisher','tier','source','published_at')},'followed':q['symbol'] in followed,'overturned':q['id'] in overturned} for q in quiet]
                from .config import market_day
                today=market_day()
                day=self.store.get(db,'monitor-public','brief_day',today) or {'read':0,'quiet':0,'investigated':0}
                answers=sorted(self.store.all(db,tenant,'answer'),key=lambda a:a['at'],reverse=True)[:5]
                outcomes=db.execute("SELECT count(*) FROM evaluations WHERE tenant IN (?,?) AND outcome NOT IN ('not_evaluated','not_evaluable')",(tenant,'monitor-public')).fetchone()[0]
            def age(value):
                try:return (_dt.datetime.now(_dt.timezone.utc)-timestamp(value)).total_seconds()
                except (ValueError,TypeError,AttributeError):return None
            interval=(watch or {}).get('intervalSeconds') or 300
            checked=age((watch or {}).get('checkedAt'))
            if watch and watch.get('active') and (checked is None or checked>interval*2.5):
                # A responding website is not evidence that monitoring runs: report a stale monitor honestly.
                watch={**watch,'active':False,'stale':True,'status':'Monitoring has not checked in recently; the background worker may be stopped.'}
            mine={h['symbol'] for h in (state['portfolio'] or {}).get('holdings',[])}
            state.update(worker={'lastHeartbeat':worker_beat.get('at'),'monitorHeartbeat':beat.get('at'),
                'healthy':(age(worker_beat.get('at')) or 1e9)<int(os.environ.get('MARKETBELL_WORKER_HEARTBEAT_MAX_AGE','120')) and (age(beat.get('at')) or 1e9)<int(os.environ.get('MARKETBELL_WORKER_HEARTBEAT_MAX_AGE','120'))+60},
                deferredNotices=[{k:d.get(k) for k in ('symbol','published_at','at','message','source','text')} for d in deferred if d.get('symbol') in mine])
            relevant=[e for e in state['events']+monitor['events'] if e.get('symbol') in followed or e in state['events']]
            state.update(quiet=quiet,answers=answers,brief={'day':today,'waiting':(watch or {}).get('waiting',0),'read':day.get('read',0),'setAside':day.get('quiet',0),'investigated':day.get('investigated',0),
                'needYou':sum(1 for e in relevant if e.get('status')=='needs'),
                'sentence':f"Today I read {day.get('read',0)} new item{'s' if day.get('read',0)!=1 else ''}, set aside {day.get('quiet',0)}, investigated {day.get('investigated',0)}"+
                    (f", and {sum(1 for e in relevant if e.get('status')=='needs')} need{'s' if sum(1 for e in relevant if e.get('status')=='needs')==1 else ''} you." if any(e.get('status')=='needs' for e in relevant) else '. Nothing needs you.')})
            state.update(operations=operations(relevant,len(state.get('deferredNotices') or []),outcomes),operatingCounts=operating_counts,exposureIssues=universe.exposure_issues(state['portfolio'],mandate),csrf=hashlib.sha256((token+'csrf').encode()).hexdigest(),
                calendar=session_state(),watch=watch,channels=channels,model=MODEL,provider='Nebius Token Factory',modes=['verified','live','autonomous'],
                recordings=available(),liveEnabled=self.allow_live,preferences=soft,
                monitored={'events':monitor['events'],'jobs':monitor['jobs'],'audit':monitor['audit']},dailyCalls=calls[0] if calls else 0,
                executionEnabled=False,scope='Isolated demo portfolio; no private account import is enabled.')
            headers=[]
            if fresh:
                headers.append(('Set-Cookie','mb_session='+token+'; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000'+('; Secure' if PRODUCTION else '')))
            return state,headers
        if not path.startswith('/api/'):
            files={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
            if method!='GET' or path not in files:
                raise ValueError('NOT_FOUND')
            file=ROOT/'public'/files[path]
            return file.read_bytes(),[('Content-Type','text/html; charset=utf-8' if path=='/' else 'text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')]
        if method!='POST' or path!='/api/action':
            raise ValueError('NOT_FOUND')
        if environ.get('HTTP_ORIGIN')!=origin:
            raise ValueError('ORIGIN_REJECTED')
        if not environ.get('CONTENT_TYPE','').startswith('application/json'):
            raise ValueError('JSON_REQUIRED')
        size=int(environ.get('CONTENT_LENGTH') or 0)
        if size<=0 or size>30000:
            raise ValueError('REQUEST_SIZE_INVALID')
        tenant,token,fresh=self.session(environ)
        if fresh:
            raise ValueError('SESSION_REQUIRED')
        csrf=hashlib.sha256((token+'csrf').encode()).hexdigest()
        if not hmac.compare_digest(environ.get('HTTP_X_CSRF_TOKEN',''),csrf):
            raise ValueError('CSRF_REJECTED')
        self.limited(tenant)
        body=json.loads(environ['wsgi.input'].read(size))
        if type(body) is not dict:
            raise ValueError('BODY_INVALID')
        action=body.get('action')
        if action in ('investigate','compare-candidates','list-correct','ask','quiet-overturn') or action in ('start','next') and body.get('mode')=='live':
            self.limited(tenant+':model-jobs',6,3600)
            with self.store.connect() as db:
                if db.execute("SELECT count(*) FROM jobs WHERE tenant=? AND status IN ('queued','running') AND kind!='replay'",(tenant,)).fetchone()[0]>=4:raise ValueError('WORKSPACE_QUEUE_LIMIT')
        if action in ('start','next'):
            mode=body.get('mode','verified')
            if mode not in ('verified','live'):
                raise ValueError('MODE_INVALID')
            if mode=='live' and not self.allow_live:
                raise ValueError('LIVE_INFERENCE_DISABLED')
            self.limited(tenant+':runs',8,3600)
            recording=body.get('recording','psx') if action=='start' else 'vlo'
            if recording not in ('psx','vlo','risk','routine','challenge'):
                raise ValueError('RECORDING_NOT_ALLOWED')
            payload={'recording':recording}
            if mode=='live':payload['request_id']=secrets.token_hex(12)
            job=self.worker.enqueue(tenant,'replay' if mode=='verified' else 'live',payload)
            if mode=='verified':
                self.worker.replay_now(job)
            return {'job':job},[]
        if action in ('approve','reject','acknowledge'):
            event=self.engine.decide(tenant,body.get('id'),action,body.get('version'))
            return {'event':event},[]
        if action=='correct':
            text=body.get('text')
            with self.store.connect() as db:
                event=self.store.get(db,tenant,'event',body.get('id'))
            if not event:
                raise ValueError('EVENT_NOT_FOUND')
            if event['mode']=='verified':
                rec=load('preference')
                if text==rec['text']:
                    return self.engine.propose(tenant,event['id'],text,rec['trace']),[]
            if not self.allow_live:
                raise ValueError('USE_RECORDED_CORRECTION_OR_ENABLE_LIVE')
            self.limited(tenant+':corrections',5,3600)
            with self.store.transaction() as db:
                if db.execute("SELECT 1 FROM jobs WHERE tenant=? AND kind='correction' AND status IN ('queued','running')",(tenant,)).fetchone():
                    raise ValueError('CORRECTION_ALREADY_PENDING')
                self.store.put(db,tenant,'candidate','current',None)
            job=self.worker.enqueue(tenant,'correction',{'event':event['id'],'text':text},key=secrets.token_hex(12))
            return {'job':job},[]
        if action=='review-monitored':
            if not self.allow_live:
                raise ValueError('LIVE_INFERENCE_DISABLED')
            self.limited(tenant+':runs',8,3600)
            with self.store.connect() as db:
                public=self.store.get(db,'monitor-public','event',body.get('id'))
            if not public:
                raise ValueError('PUBLIC_CASE_NOT_FOUND')
            return {'job':self.worker.enqueue(tenant,'monitored-case',{'event':public['id']})},[]
        if action=='ask':
            question=body.get('question')
            if type(question) is not str or not 5<=len(question.strip())<=300:
                raise ValueError('QUESTION_INVALID')
            from .ask import answer
            self.limited(tenant+':ask',20,3600)
            if self.allow_live and os.environ.get('MARKETBELL_ASK_MODEL','1')=='1':
                return {'job':self.worker.enqueue(tenant,'ask',{'question':question.strip(),'request_id':secrets.token_hex(12)})},[]
            return {'answer':answer(self.store,tenant,question.strip())},[]
        if action=='quiet-overturn':
            with self.store.connect() as db:
                item=self.store.get(db,'monitor-public','quiet',body.get('id'))
            if not item:
                raise ValueError('QUIET_ITEM_NOT_FOUND')
            self.limited(tenant+':overturn',20,3600)
            with self.store.transaction() as db:
                self.store.put(db,tenant,'quiet_overturn',item['id'],{'quiet':item['id'],'symbol':item['symbol'],'publisher':item.get('publisher'),'reason':item['reason'],'at':now()})
                self.store.append(db,tenant,'Set-aside item marked important',item['symbol']+': '+item['reason']+' overturned for '+(item.get('publisher') or 'unknown publisher'))
                if item['reason']=='COMMENTARY_ONLY' and item.get('publisher') and not os.environ.get('MARKETBELL_PUBLIC_DEMO')=='1':
                    # Personal deployment: the owner's judgement changes future triage for this stock.
                    # Public demo: anonymous visitors must not reshape shared monitoring.
                    self.store.put(db,'monitor-public','trusted_publisher',digest({'symbol':item['symbol'],'publisher':item['publisher']})[:24],
                        {'symbol':item['symbol'],'publisher':item['publisher'],'at':now(),'from':item['id']})
            job=None
            if self.allow_live and item['reason'] in ('COMMENTARY_ONLY','BEFORE_MONITORING','ROUTINE_FILING','WRONG_COMPANY','AMBIGUOUS_NAME','OLD_NEWS','NOT_REACHED'):
                self.limited(tenant+':runs',8,3600)
                job=self.worker.enqueue(tenant,'quiet-review',{'symbol':item['symbol'],'item':item['item']},key=item['id'])
            learned=('From now on I will look at '+item['publisher']+' stories about '+item['symbol']+'.') if item['reason']=='COMMENTARY_ONLY' and item.get('publisher') and os.environ.get('MARKETBELL_PUBLIC_DEMO')!='1' else 'Noted for this workspace.'
            return {'job':job,'learned':learned},[]
        if action=='investigate':
            if not self.allow_live:
                raise ValueError('LIVE_INFERENCE_DISABLED')
            symbol=body.get('symbol')
            reason=body.get('reason')
            if symbol not in SYMBOLS or type(reason) is not str or not 5<=len(reason)<=500:
                raise ValueError('INVESTIGATION_REQUEST_INVALID')
            self.limited(tenant+':runs',8,3600)
            return {'job':self.worker.enqueue(tenant,'user-investigate',{'symbol':symbol,'reason':reason,'request_id':secrets.token_hex(12)})},[]
        if action=='portfolio-update':
            from .engine import default_portfolio
            holdings=body.get('holdings')
            cash=body.get('cash_pct')
            if type(holdings) is not list or len(holdings)>12 or type(cash) not in (int,float) or not 0<=cash<=100:
                raise ValueError('PORTFOLIO_INVALID')
            clean=[]
            for h in holdings:
                if type(h) is not dict or h.get('symbol') not in universe.CATALOG or h['symbol'] in {v['symbol'] for v in clean} or type(h.get('position_pct')) not in (int,float) or not 0<h['position_pct']<=100:
                    raise ValueError('PORTFOLIO_INVALID')
                clean.append({'symbol':h['symbol'],'position_pct':h['position_pct'],'sector':SYMBOLS[h['symbol']][1]})
            if sum(h['position_pct'] for h in clean)+cash>100:
                raise ValueError('PORTFOLIO_ALLOCATION_INVALID')
            with self.store.transaction() as db:
                p=self.store.get(db,tenant,'portfolio','main')
                if self.store.get(db,tenant,'mandate','main'):raise ValueError('USE_REVIEWED_UNIVERSE_SETUP')
                if p['version']!=body.get('version'):
                    raise ValueError('STALE_PORTFOLIO_VERSION')
                p.update(holdings=clean,cash_pct=cash,version=p['version']+1)
                previous={h['symbol'] for h in p['holdings']}
                p['sector_exposure']={sector:sum(h['position_pct'] for h in clean if h['sector']==sector) for sector in {h['sector'] for h in clean}}
                # A stock removed from holdings stays watched; nothing is silently dropped from view.
                p['watchlist']=list(dict.fromkeys([s for s in p['watchlist']+sorted(previous) if s not in {h['symbol'] for h in clean}]))[:12]
                self.store.put(db,tenant,'portfolio','main',p)
                self.store.append(db,tenant,'Portfolio changed','Demo portfolio revision '+str(p['version'])+' saved. Queued actions recheck current authority and exposure.')
            if self.allow_live:
                for h in clean[:2]:
                    self.worker.enqueue(tenant,'user-investigate',{'symbol':h['symbol'],'reason':'New or revised demo holding; investigate relevant developments.','portfolio_version':p['version']})
            return {'portfolio':p},[]
        if action=='monitoring-toggle':
            if type(body.get('enabled')) is not bool:
                raise ValueError('MONITORING_PREFERENCE_INVALID')
            with self.store.transaction() as db:
                p=self.store.get(db,tenant,'portfolio','main')
                p['monitoring_enabled']=body['enabled']
                p['version']+=1
                self.store.put(db,tenant,'portfolio','main',p)
                self.store.append(db,tenant,'Monitoring preference', 'Personal demo monitoring '+('enabled' if body['enabled'] else 'paused'))
            return {'ok':True},[]
        if action=='portfolio-proposal':
            return universe.propose_portfolio_change(self.store,tenant,body.get('event'),body.get('operation'),body.get('holding')),[]
        if action=='compare-candidates':
            if not self.allow_live:raise ValueError('LIVE_DISABLED')
            candidate=universe.symbol(body.get('candidate'));holding=universe.symbol(body.get('holding'))
            with self.store.connect() as db:p=self.store.get(db,tenant,'portfolio','main')
            if holding not in {h['symbol'] for h in p['holdings']} or candidate==holding:raise ValueError('COMPARISON_INVALID')
            return {'job':self.worker.enqueue(tenant,'compare',{'candidate':candidate,'holding':holding,'portfolio_version':p['version']})},[]
        if action=='list-correct':
            if not self.allow_live:raise ValueError('LIVE_DISABLED')
            text=body.get('text')
            if type(text) is not str or not 10<=len(text)<=1000:raise ValueError('CORRECTION_LENGTH_INVALID')
            return {'job':self.worker.enqueue(tenant,'list-correction',{'recommendation':body.get('id'),'text':text,'request_id':secrets.token_hex(12)})},[]
        if action=='list-rule':
            return universe.list_rule_action(self.store,tenant,body.get('id'),body.get('operation'),body.get('version'),body.get('updates')),[]
        if action=='universe-preview':
            rows=universe.parse_csv(body['csv']) if 'csv' in body else universe.parse_rows(body.get('rows'))
            return {'rows':rows,'review':'Review before explicit confirmation; no changes have been saved.'},[]
        if action=='universe-commit':
            if os.environ.get('MARKETBELL_PUBLIC_DEMO')=='1' and type(body.get('rows')) is list:
                # Public demo: never retain personal holding details (share counts, cost basis) from
                # anonymous visitors. Percent allocations remain for the deterministic limits.
                body['rows']=[{**r,'quantity':1 if str(r.get('tier','watchlist')).lower()=='portfolio' else 0,'cost_basis':None} if type(r) is dict else r for r in body['rows']]
            with self.store.connect() as db:before=self.store.get(db,tenant,'portfolio','main')
            result=universe.commit(self.store,tenant,body.get('rows'),body.get('mandate'),body.get('version'),body.get('confirmed'),body.get('cash_pct',25))
            with self.store.connect() as db:after=self.store.get(db,tenant,'portfolio','main')
            changed=[h['symbol'] for h in after['holdings'] if h not in before['holdings']]+[s for s in after['watchlist'] if s not in before['watchlist']]
            result['investigation_jobs']=[]
            if self.allow_live:
                for symbol in dict.fromkeys(changed):
                    result['investigation_jobs'].append(self.worker.enqueue(tenant,'user-investigate',{'symbol':symbol,'reason':'User confirmed portfolio/watchlist revision '+str(after['version'])+'. Review relevant news and risks.','request_id':'portfolio-'+str(after['version'])+'-'+symbol}))
            return result,[]
        if action=='universe-decision':
            return universe.decide(self.store,tenant,body.get('id'),body.get('choice'),body.get('version')),[]
        if action=='universe-archive':
            return universe.archive(self.store,tenant,body.get('symbol'),body.get('version'),body.get('confirmed',False)),[]
        if action=='impact-feedback':
            from .impact import report
            return report(self.store,tenant,body.get('kind'),sanitize(body.get('text'),1000),body.get('event')),[]
        if action=='impact-demo':
            from .demo_history import seed
            return seed(self.store,'illustrative-shared-v3'),[]
        if action=='save-rule':
            return {'rule':self.engine.approve_rule(tenant,body.get('id'))},[]
        if action=='decline-rule':
            with self.store.transaction() as db:
                self.store.put(db,tenant,'candidate','current',None)
                self.store.append(db,tenant,'Standing rule declined','No authority granted.')
            return {'ok':True},[]
        if action in ('pause','resume','revoke','edit'):
            return {'rule':self.engine.rule_action(tenant,body.get('id'),action,body.get('version'),body.get('updates'))},[]
        if action=='monitor':
            admin=os.environ.get('MARKETBELL_ADMIN_TOKEN','')
            if len(admin)<24 or not hmac.compare_digest(environ.get('HTTP_AUTHORIZATION',''),'Bearer '+admin):
                raise ValueError('ADMIN_REQUIRED')
            return {'job':self.worker.enqueue('monitor-public','autonomous',{'session':session_state()['latest']})},[]
        if action=='retry':
            with self.store.transaction() as db:
                job=db.execute('SELECT * FROM jobs WHERE id=? AND tenant=?',(body.get('id'),tenant)).fetchone()
                if not job or job['status']!='failed' or job['attempts']>=3:
                    raise ValueError('JOB_NOT_RETRYABLE')
                db.execute("UPDATE jobs SET status='queued',updated=? WHERE id=?",(now(),job['id']))
            return {'ok':True},[]
        if action=='restart':
            # Preserve old audit; expire cookie and allocate a separate workspace on next read.
            with self.store.transaction() as db:
                db.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
                self.store.append(db,tenant,'Demo restarted','Session ended; history retained in its isolated workspace.')
            return {'ok':True},[('Set-Cookie','mb_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0')]
        raise ValueError('ACTION_INVALID')

    def __call__(self,environ,start_response):
        status='200 OK'
        headers=[('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('Referrer-Policy','no-referrer'),
            ('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
            ('Cross-Origin-Opener-Policy','same-origin'),('Permissions-Policy','camera=(), microphone=(), geolocation=(), payment=()')]
        if PRODUCTION:
            headers.append(('Strict-Transport-Security','max-age=31536000; includeSubDomains'))
        try:
            result,extra=self.dispatch(environ)
            headers+=extra
        except Exception as error:
            import re
            code=str(error)
            code=code if re.fullmatch('[A-Z0-9_]+',code) else 'REQUEST_FAILED'
            status=('429 Too Many Requests' if 'LIMIT' in code or 'BUDGET' in code else
                '403 Forbidden' if code in ('ORIGIN_REJECTED','HOST_REJECTED','CSRF_REJECTED','ADMIN_REQUIRED','SESSION_REQUIRED','WEBHOOK_AUTH_REQUIRED') else
                '503 Service Unavailable' if code in ('DATABASE_UNAVAILABLE','DATABASE_BUSY','DATABASE_POOL_EXHAUSTED') else
                '404 Not Found' if code in ('NOT_FOUND','EVENT_NOT_FOUND') else '409 Conflict')
            if status[:3] in ('403','429','503'):
                from .telemetry import emit
                emit('security.denied' if status[:3]!='503' else 'service.unavailable',code=code,status=int(status[:3]),path=environ.get('PATH_INFO','')[:80],method=environ.get('REQUEST_METHOD'))
            result={'error':code}
        if isinstance(result,bytes):
            raw=result
        else:
            headers.append(('Content-Type','application/json; charset=utf-8'))
            raw=json.dumps(result,ensure_ascii=True,allow_nan=False).encode()
        headers.append(('Content-Length',str(len(raw))))
        start_response(status,headers)
        return [raw]
