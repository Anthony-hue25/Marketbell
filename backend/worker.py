"""Durable queue, progress recovery and completed-session scheduler. Singleton worker."""
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from .config import now, SYMBOLS
from .store import encoded, digest
from .market import acquire, session_state, news
from .recordings import load

JOB_LEASE_SECONDS=int(os.environ.get('MARKETBELL_JOB_LEASE_SECONDS','90'))
ROLE_LEASE_SECONDS=int(os.environ.get('MARKETBELL_ROLE_LEASE_SECONDS','60'))

def _age_hours(value):
    try:return (datetime.now(timezone.utc)-datetime.fromisoformat(str(value).replace('Z','+00:00'))).total_seconds()/3600
    except ValueError:return 1e9


class Worker:
    def __init__(self,store,engine,owner=None):
        self.store,self.engine=store,engine
        self.stop=threading.Event()
        import socket,secrets
        self.owner=owner or socket.gethostname()[:40]+'-'+str(os.getpid())+'-'+secrets.token_hex(3)
        self.current_job=None          # most recent claim (health display / single-slot compatibility)
        self.active_jobs=set()          # every job this process currently holds a lease for
        self.active_lock=threading.Lock()
        self.concurrency=max(1,min(8,int(os.environ.get('MARKETBELL_WORKER_CONCURRENCY','1'))))
        self.roles={'worker':False,'monitor':False}
        self.last_loop={'worker':0.0,'monitor':0.0}
        self.threads=[]

    def holding_symbols(self):
        # Read every workspace's portfolio at most once per 30 s (it was re-read for every enqueue and
        # scan; in the public demo each visitor has a workspace). Priority only; never authority.
        hit=getattr(self,'_holdings_cache',None)
        if hit and time.monotonic()-hit[0]<30:
            return set(hit[1])
        with self.store.connect() as db:
            held={h['symbol'] for r in db.execute("SELECT body FROM documents WHERE kind='portfolio' AND tenant NOT LIKE 'illustrative-%' AND COALESCE(json_extract(body,'$.monitoring_enabled'),1)=1") for h in json.loads(r[0])['holdings']}
        self._holdings_cache=(time.monotonic(),held)
        return set(held)

    @staticmethod
    def enqueue_id(tenant,kind,key):
        return digest({'tenant':tenant,'kind':kind,'key':key})[:32]

    def enqueue(self,tenant,kind,payload,key=None):
        id=digest({'tenant':tenant,'kind':kind,'key':key or payload})[:32]
        priority=0 if kind in ('correction','list-correction','replay') or kind=='watch-event' and payload.get('symbol') in self.holding_symbols() else 2 if kind in ('monitored-case','user-investigate') else 5
        with self.store.transaction() as db:
            db.execute('INSERT OR IGNORE INTO jobs(id,tenant,kind,payload,status,created,updated,priority) VALUES(?,?,?,?,?,?,?,?)',
                (id,tenant,kind,encoded(payload),'queued',now(),now(),priority))
        return id

    def context(self,symbol):
        try:return acquire(symbol)
        except ValueError:
            from .market import evidence_items
            from .sources import gather
            evidence=evidence_items(gather(symbol,followed=True))
            if not evidence:raise ValueError('NO_QUALITATIVE_EVIDENCE')
            return {'symbol':symbol,'session':'unknown','trigger':'News-led investigation; verified completed-session prices unavailable.',
                'source':'Public news headlines; price context unavailable','evidence':evidence,'change':0,'priority':0}

    def execute(self,tenant,kind,payload):
        if kind=='replay':
            rec=load(payload['recording'])
            e=self.engine.ingest(tenant,rec['package'],'verified',payload['recording'])
            return self.engine.investigate(tenant,e['id'],rec['traces'],rec['package']['session'])
        if kind=='live':
            rec=load(payload['recording'])
            e=self.engine.ingest(tenant,rec['package'],'live',payload['recording']+'-live-'+payload.get('request_id','legacy'))
            return self.engine.investigate(tenant,e['id'])
        if kind=='monitored-case':
            with self.store.connect() as db:
                source=self.store.get(db,'monitor-public','event',payload['event'])
            if not source:
                raise ValueError('PUBLIC_CASE_NOT_FOUND')
            package={k:source[k] for k in ('symbol','session','trigger','source','evidence','change','priority')}
            e=self.engine.ingest(tenant,package,'live','monitored-'+source['id'])
            return self.engine.investigate(tenant,e['id'])
        if kind=='watch-event':
            symbol=payload['symbol']
            session=session_state()['latest']
            with self.store.connect() as db:
                prior=[e for e in self.store.all(db,tenant,'event') if e['symbol']==symbol and e['session']==session and e.get('recording') is None]
            if prior:
                old=prior[0]
                package={k:old[k] for k in ('symbol','session','trigger','source','evidence','change','priority')}
            else:
                try:
                    package=acquire(symbol,session)
                except ValueError:
                    # New qualitative information must not wait for a completed price refresh.
                    # Unknown price context permits analysis but fails the preparation governor.
                    package={'symbol':symbol,'session':'unknown','trigger':'Incoming information without a verified current price baseline.',
                        'source':'Incoming event; price baseline unavailable','evidence':[],'change':0,'priority':0}
            notice=payload['notice']
            session=package['session']
            max_age=90 if notice['type'] in ('issuer_announcement','regulatory_filing') else 7
            if datetime.fromisoformat(notice['published_at'].replace('Z','+00:00'))<datetime.now(timezone.utc)-timedelta(days=max_age):
                raise ValueError('WATCH_NOTICE_STALE')
            package['evidence']=[e for e in package['evidence'] if e['id']!=notice['id']]+[notice]
            package['trigger']='Continuous watch received previously unseen information (source published '+notice['published_at']+'): '+notice['text']+'. Price context is the last completed session ('+session+'), not an intraday quote.'
            e=self.engine.ingest(tenant,package,'autonomous','watch-'+notice['id'])
            result=self.engine.investigate(tenant,e['id'])
            with self.store.transaction() as db:
                deferred=self.store.get(db,tenant,'deferred_notice',symbol+'-'+notice['id'])
                if deferred and deferred['state']=='deferred':
                    deferred.update(state='investigated',investigated_at=now(),event=result['id'])
                    self.store.put(db,tenant,'deferred_notice',symbol+'-'+notice['id'],deferred)
            self.personalize(result)
            return result
        if kind=='compare':
            from .universe import propose_portfolio_change
            candidate=self.context(payload['candidate']);holding=self.context(payload['holding'])
            package={**candidate,'evidence':candidate['evidence']+holding['evidence'],
                'trigger':'Compare candidate '+payload['candidate']+' with user-recorded holding '+payload['holding']+'. Assess reasons to research a possible replacement and counterarguments. No trade or ownership change. News assertions are not verified quantitative observations.',
                'comparison':{'candidate':payload['candidate'],'holding':payload['holding']}}
            e=self.engine.ingest(tenant,package,'live','comparison-'+digest(payload))
            result=self.engine.investigate(tenant,e['id'])
            if result.get('research') and result.get('challenge'):
                propose_portfolio_change(self.store,tenant,result['id'],'replace_holding',payload['holding'])
            return result
        if kind=='ask':
            from .ask import answer
            return answer(self.store,tenant,payload['question'],self.engine.ai,payload['request_id'])
        if kind=='quiet-review':
            item=payload['item']
            package=self.context(payload['symbol'])
            package['evidence']=[e for e in package['evidence'] if e['id']!=item['id']]+[item]
            package['trigger']='You marked a set-aside item as important: '+item.get('text','')[:300]+'. '+package['trigger']
            e=self.engine.ingest(tenant,package,'live','quiet-'+digest(payload))
            return self.engine.investigate(tenant,e['id'])
        if kind=='user-investigate':
            package=self.context(payload['symbol'])
            package['trigger']='Authenticated user requested investigation: '+payload['reason']+'. '+package['trigger']
            e=self.engine.ingest(tenant,package,'live','user-'+digest(payload))
            return self.engine.investigate(tenant,e['id'])
        if kind=='autonomous':
            session=payload['session']
            packages=[]
            errors=[]
            quiet=[]
            self.observed_symbols()  # refreshes self.followed (holdings and watchlists)
            followed=sorted(getattr(self,'followed',set()))
            held=self.holding_symbols()
            min_move=float(os.environ.get('MARKETBELL_DAILY_REVIEW_MIN_MOVE','2'))
            for symbol in followed:
                try:
                    package=acquire(symbol,session)
                except Exception:
                    errors.append(symbol+': MARKET_ACQUISITION_FAILED');continue
                fresh=[e for e in package['evidence'] if e.get('type')!='daily_market_data' and _age_hours(e.get('published_at'))<=24]
                # A model review only where it can tell the investor something: a material move or
                # information published in the last day. Otherwise the stock is recorded as reviewed
                # by rule, at no model cost (the Quant lesson: do not research what cannot change).
                if abs(package['change'])>=min_move or fresh:
                    packages.append(package)
                else:
                    quiet.append(symbol)
            packages.sort(key=lambda p:(p['symbol'] in held,p['priority']),reverse=True)  # holdings first, then largest move
            packages=packages[:int(os.environ.get('MARKETBELL_DAILY_REVIEW_MAX','8'))]
            for p in packages:
                e=self.engine.ingest(tenant,p,'autonomous')
                try:
                    self.engine.investigate(tenant,e['id'])
                except ValueError as error:
                    errors.append(p['symbol']+': '+str(error))
            if not packages and not quiet:
                raise ValueError('NO_CURRENT_MARKET_DATA')
            with self.store.transaction() as db:
                self.store.put(db,tenant,'monitor','latest',{'session':session,'completed':now(),'symbols':len(packages),'reviewed_by_rule':quiet,'errors':errors})
                self.store.append(db,tenant,'Session processed',session+': '+str(len(packages))+' securities investigated; '+str(len(quiet))+' reviewed by rule (move under '+f'{min_move:g}'+'% and no new information); '+str(len(errors))+' unresolved errors. No orders.')
            return {'errors':errors,'reviewed_by_rule':quiet}
        if kind=='followup':
            from .impact import evaluate
            with self.store.connect() as db:
                row=db.execute('SELECT body FROM decision_snapshots WHERE tenant=? AND event=?',(tenant,payload['event'])).fetchone()
            if not row:raise ValueError('SNAPSHOT_NOT_FOUND')
            original=json.loads(row[0])
            try:
                later=news(original['symbol'])
            except ValueError:
                later=[]
            horizon_end=datetime.fromisoformat(original['at'])+timedelta(days=payload['horizon'])
            valid=[item for item in later if datetime.fromisoformat(original['at'])<datetime.fromisoformat(item['published_at'])<=horizon_end]
            from .ai import bind_tenant
            with bind_tenant(tenant):assessment=self.engine.ai.followup(original,valid,payload['horizon']) if valid else None
            observation=None
            if original.get('price_reference'):
                from .market import market_bars
                try:
                    horizon_time=datetime.fromisoformat(original['at'])+timedelta(days=payload['horizon'])
                    horizon_session=session_state(horizon_time)['latest'];data=market_bars(original['symbol'],horizon_session);bar=data['bars'][-1]
                    observation={'session':bar['session_date'],'close':bar['close'],'source':data['source'],'retrieved_at':data['retrieved_at'],'adjustment':data.get('adjustment','unknown')}
                    # Same-retrieval reference close: a split or provider correction after the decision
                    # rescales history, so the decision-time close alone is not a comparable basis.
                    try:
                        reference=original['price_reference'];ref=market_bars(original['symbol'],reference['session'])
                        if ref['bars'][-1]['session_date']==reference['session'] and ref.get('adjustment')==data.get('adjustment'):
                            observation['reference_close_same_retrieval']=ref['bars'][-1]['close']
                    except (ValueError,KeyError,IndexError):pass
                except ValueError:pass
            return evaluate(self.store,tenant,payload['event'],payload['horizon'],valid,assessment,market_observation=observation)
        if kind=='list-correction':
            from .universe import propose_list_rule
            return propose_list_rule(self.store,self.engine.ai,tenant,payload['recommendation'],payload['text'])
        if kind=='correction':
            return self.engine.propose(tenant,payload['event'],payload['text'])
        raise ValueError('JOB_KIND_INVALID')

    def personalize(self,source):
        """One fresh source investigation, separate current authority for each subscribed demo."""
        with self.store.connect() as db:
            tenants=[r[0] for r in db.execute("SELECT tenant FROM documents WHERE kind='portfolio' AND id='main' AND tenant!='monitor-public' AND tenant NOT LIKE 'illustrative-%'")]
        package={k:source[k] for k in ('symbol','session','trigger','source','evidence','change','priority')}
        for tenant in tenants:
            with self.store.connect() as db:
                p=self.store.get(db,tenant,'portfolio','main')
            with self.store.connect() as db:
                mandate=self.store.get(db,tenant,'mandate','main')
            if p and mandate and source['symbol'] not in p['watchlist']+[h['symbol'] for h in p['holdings']] and (source['symbol'] in mandate['excluded_symbols'] or source['sector'] in mandate['excluded_sectors']):
                continue
            if not p or not p.get('monitoring_enabled',True) or (source['symbol'] not in p['watchlist']+[h['symbol'] for h in p['holdings']] and not (mandate and mandate.get('approved_at'))):
                continue
            event=self.engine.ingest(tenant,package,'autonomous',source['recording'])
            if event['status'] not in ('queued','failed'):
                continue
            result=self.engine.investigate(tenant,event['id'],{k:source[k+'_trace'] for k in ('research','challenge')})
            with self.store.transaction() as db:
                result['shared_investigation']=True
                self.store.put(db,tenant,'event',result['id'],result)
                self.store.append(db,tenant,'Continuous personal review',source['symbol']+': source investigation reused with identical evidence; your current policy and authority checked. Outcome '+result['status'])

    def claim(self,db,id,owner=None,ttl=None):
        """Atomic, fenced claim: exactly one process can move a queued job to running."""
        return db.execute("UPDATE jobs SET status='running',attempts=attempts+1,updated=?,started_at=?,lease_owner=?,lease_expires=? WHERE id=? AND status='queued'",
            (now(),now(),owner or self.owner,time.time()+(ttl or JOB_LEASE_SECONDS),id)).rowcount==1

    def replay_now(self,id):
        """Instant replay is independent of slow inference jobs; claim the exact job atomically."""
        owner='api-'+self.owner
        with self.store.transaction() as db:
            job=db.execute("SELECT * FROM jobs WHERE id=? AND kind='replay' AND status='queued'",(id,)).fetchone()
            if not job or not self.claim(db,id,owner,120):
                return
        try:
            self.execute(job['tenant'],job['kind'],json.loads(job['payload']))
            with self.store.transaction() as db:
                db.execute("UPDATE jobs SET status='completed',updated=?,error=NULL,lease_expires=NULL WHERE id=? AND lease_owner=?",(now(),id,owner))
        except Exception:
            with self.store.transaction() as db:
                db.execute("UPDATE jobs SET status='failed',updated=?,error='REPLAY_VERIFICATION_FAILED',lease_expires=NULL WHERE id=? AND lease_owner=?",(now(),id,owner))
            raise ValueError('REPLAY_VERIFICATION_FAILED') from None

    def once(self):
        with self.store.transaction() as db:
            job=db.execute("SELECT * FROM jobs WHERE status='queued' AND attempts<3 ORDER BY priority,created LIMIT 1").fetchone()
            if not job or not self.claim(db,job['id']):
                return False
        self.current_job=job['id']
        with self.active_lock:self.active_jobs.add(job['id'])
        started=time.monotonic()
        from .telemetry import emit
        try:
            result=self.execute(job['tenant'],job['kind'],json.loads(job['payload']))
            if isinstance(result,dict) and result.get('id') and result.get('research') and result.get('challenge'):
                from .universe import consider
                consider(self.store,job['tenant'],result)
            with self.store.transaction() as db:
                unresolved=isinstance(result,dict) and bool(result.get('errors'))
                # Fenced completion: a worker whose lease was taken over never overwrites the new owner.
                db.execute("UPDATE jobs SET status=?,updated=?,error=?,lease_expires=NULL WHERE id=? AND lease_owner=?",('completed_with_errors' if unresolved else 'completed',now(),'SOME_CASES_UNRESOLVED' if unresolved else None,job['id'],self.owner))
            emit('job.completed',kind=job['kind'],job=job['id'],duration_ms=round((time.monotonic()-started)*1000),attempt=job['attempts']+1,unresolved=unresolved)
        except Exception as error:
            import re
            code=str(error)
            code=code if re.fullmatch('[A-Z0-9_]+',code) else 'BACKGROUND_JOB_FAILED'
            with self.store.transaction() as db:
                db.execute("UPDATE jobs SET status='failed',error=?,updated=?,lease_expires=NULL WHERE id=? AND lease_owner=?",(code,now(),job['id'],self.owner))
                self.store.append(db,job['tenant'],'Background failure',job['kind']+': '+code+'; no unverified action completed.')
                if code.endswith('BUDGET_EXHAUSTED') and job['kind']=='watch-event':
                    self.defer_notice(db,json.loads(job['payload']),code)
            emit('job.failed',kind=job['kind'],job=job['id'],code=code,duration_ms=round((time.monotonic()-started)*1000),attempt=job['attempts']+1)
        finally:
            with self.active_lock:self.active_jobs.discard(job['id'])
            if self.current_job==job['id']:self.current_job=None
        return True

    def defer_notice(self,db,payload,reason):
        """Budget exhaustion never silently drops new information about a recorded holding."""
        symbol=payload.get('symbol');notice=payload.get('notice') or {}
        if not symbol or not notice.get('id') or symbol not in self.holding_symbols():
            return False
        key=symbol+'-'+notice['id']
        if self.store.get(db,'monitor-public','deferred_notice',key):
            return False
        body={'symbol':symbol,'notice_id':notice['id'],'text':notice.get('text','')[:500],'source':notice.get('source'),
            'published_at':notice.get('published_at'),'at':now(),'state':'deferred','reason':reason,
            'message':'New information about a recorded holding could not be investigated because the AI budget is exhausted. It is preserved and will be retried when budget is available; review the source yourself if it is urgent.'}
        self.store.put(db,'monitor-public','deferred_notice',key,body)
        self.store.append(db,'monitor-public','Holding notice deferred',symbol+': '+reason+'; preserved for retry and shown for attention. No conclusion drawn.')
        from .telemetry import emit
        emit('budget.deferred',symbol=symbol,reason=reason)
        return True

    def recover(self):
        """Requeue only jobs whose lease has expired, so a live owner (another replica, or the API's
        instant replay) is never pre-empted. Legacy rows without a lease are treated as expired."""
        at=time.time()
        with self.store.transaction() as db:
            if os.environ.get('MARKETBELL_ALLOW_LIVE','1')=='1':
                db.execute("UPDATE jobs SET status='queued',updated=? WHERE status='failed' AND error='LIVE_INFERENCE_DISABLED' AND attempts<3",(now(),))
            requeued=db.execute("UPDATE jobs SET status='queued',updated=?,lease_owner=NULL,lease_expires=NULL WHERE status='running' AND attempts<3 AND (lease_expires IS NULL OR lease_expires<?)",(now(),at)).rowcount
            exhausted=db.execute("UPDATE jobs SET status='failed',error='RETRY_LIMIT',updated=?,lease_expires=NULL WHERE status='running' AND attempts>=3 AND (lease_expires IS NULL OR lease_expires<?)",(now(),at)).rowcount
            # A model reservation whose caller died never completes; mark it so it is not mistaken for
            # an in-flight call. Reserved spend stays counted (conservative budget).
            db.execute("UPDATE model_calls SET status='abandoned',error='CALLER_INTERRUPTED' WHERE status='reserved' AND at<?",((datetime.now(timezone.utc)-timedelta(minutes=15)).isoformat(),))
        if requeued or exhausted:
            from .telemetry import emit
            emit('worker.recovered',requeued=requeued,retry_limit=exhausted)
        return requeued

    def renew(self):
        """Hold the singleton role leases and keep the current job's lease alive."""
        for role in ('worker','monitor'):
            try:self.roles[role]=self.store.acquire_lease(role,self.owner,ROLE_LEASE_SECONDS)
            except Exception:self.roles[role]=False
        with self.active_lock:held=set(self.active_jobs)|({self.current_job} if self.current_job else set())
        if held:
            try:
                with self.store.transaction() as db:
                    for id in held:
                        db.execute("UPDATE jobs SET lease_expires=? WHERE id=? AND lease_owner=? AND status='running'",(time.time()+JOB_LEASE_SECONDS,id,self.owner))
            except Exception:
                pass

    def schedule(self):
        if os.environ.get('MARKETBELL_SCHEDULER','1')!='1':
            return
        session=session_state()['latest']
        id=self.enqueue('monitor-public','autonomous',{'session':session},key=session)
        with self.store.transaction() as db:
            db.execute("UPDATE jobs SET status='queued',updated=? WHERE id=? AND status IN ('failed','completed_with_errors') AND attempts<3 AND updated<?",(now(),id,(datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat()))

    def observed_symbols(self):
        # A bounded catalog, holdings first, plus rotating wider discovery; never full-market coverage.
        from .universe import CATALOG
        with self.store.connect() as db:
            portfolios=self.store.all(db,'monitor-public','portfolio')
            portfolios += [json.loads(r[0]) for r in db.execute("SELECT body FROM documents WHERE kind='portfolio' AND tenant!='monitor-public' AND tenant NOT LIKE 'illustrative-%' LIMIT 2000")]
        holdings={h['symbol'] for p in portfolios if p.get('monitoring_enabled',True) for h in p['holdings']}
        watched={s for p in portfolios if p.get('monitoring_enabled',True) for s in p['watchlist']}
        self.followed=holdings|watched
        from .engine import DEMO_HOLDINGS,DEMO_WATCHLIST
        observed=holdings|watched|{s for s,_ in DEMO_HOLDINGS}|set(DEMO_WATCHLIST)
        # Discovery rotation skips stocks the shared mandate excludes: research on them could never
        # lead to a watchlist promotion (screen and policy use the same yardstick).
        with self.store.connect() as db:
            mandate=self.store.get(db,'monitor-public','mandate','main') or {}
        excluded=set(mandate.get('excluded_symbols') or [])|{s for s in CATALOG if CATALOG[s][1] in set(mandate.get('excluded_sectors') or [])}
        candidates=sorted(set(CATALOG)-observed-excluded)
        step=getattr(self,'discovery_step',0)
        extra=[candidates[(step+i)%len(candidates)] for i in range(min(4,len(candidates)))] if candidates else []
        self.discovery_step=step+4
        return sorted(holdings)+sorted(observed-holdings)+extra

    def scan_watch(self,fetch_news=None):
        """Always-on noticing, independent of the completed-session research cadence."""
        interval=max(60,min(3600,int(os.environ.get('MARKETBELL_WATCH_SECONDS','300'))))
        scan_started=time.monotonic()
        discovered=[]
        errors=[]
        # Read public sources outside database locks and outside the slow model worker.
        self.engine.ensure('monitor-public')  # a fresh deployment's first scan already knows its holdings
        observed=self.observed_symbols()
        if fetch_news is None:
            from . import sources
            for channel in ('google_news','press_wires','sec_edgar'):
                sources.health.pop(channel,None)  # this scan's results only; no stale per-symbol states
            followed=getattr(self,'followed',set())
            # Every observed stock gets general news; followed stocks (holdings, watchlist) also get
            # the issuer's own press releases and SEC filings.
            fetch_news=lambda symbol:sources.gather(symbol,followed=symbol in followed)
        from concurrent.futures import ThreadPoolExecutor
        def fetch(symbol):
            try:return symbol,fetch_news(symbol),None
            except Exception:return symbol,[],symbol+': SOURCE_UNAVAILABLE'
        with ThreadPoolExecutor(max_workers=4,thread_name_prefix='MarketBell-news') as pool:
            for symbol,items,error in pool.map(fetch,observed):
                if error:errors.append(error)
                discovered.append((symbol,items))
        fetched_at=time.monotonic()
        at=datetime.now(timezone.utc)
        queued=[]
        holdings=self.holding_symbols()
        with self.store.transaction() as db:
            candidates=[]
            new_screened=0
            first_read=0
            catchup=False
            for symbol,items in discovered:
                cursor=self.store.get(db,'monitor-public','watch_cursor',symbol)
                if cursor and (at-datetime.fromisoformat(cursor['checked_at'])).total_seconds()>interval*2:
                    catchup=True
                known=set(cursor['seen']) if cursor else set()
                new=[]
                for item in items:
                    from .impact import screen
                    new_screened+=screen(db,'monitor-public',symbol,item)
                    if item['id'] in known:
                        continue
                    first_read+=1
                    if item.get('entity_match') is False:
                        self.quiet(db,symbol,item,'AMBIGUOUS_NAME' if item.get('entity_basis')=='AMBIGUOUS_COMMON_WORD' else 'WRONG_COMPANY')
                        known.add(item['id']);continue
                    if item.get('also_reported_by'):
                        self.quiet(db,symbol,item,'DUPLICATE')
                    try:
                        published=datetime.fromisoformat(item['published_at'])
                        age=(at-published).total_seconds()
                    except (ValueError,TypeError,KeyError):
                        continue
                    # First scan establishes the baseline; only very recent items can initiate work.
                    if 0<=age<=(1800 if cursor is None else 7*86400):
                        reason=self.triage(db,symbol,item)
                        if reason:
                            self.quiet(db,symbol,item,reason);known.add(item['id'])
                        else:
                            new.append(item)
                    elif cursor is None and age>1800:
                        self.quiet(db,symbol,item,'BEFORE_MONITORING')
                        known.add(item['id'])
                    elif age>7*86400:
                        # First seen when already old (e.g. a 30-day press-release search): background
                        # only. Remember it so it is neither counted nor logged again on every scan.
                        self.quiet(db,symbol,item,'OLD_NEWS')
                        known.add(item['id'])
                for item in new:
                    notice_key=symbol+item['id']
                    if not self.store.get(db,'monitor-public','pending_notice',notice_key):
                        self.store.put(db,'monitor-public','pending_notice',notice_key,{'symbol':symbol,'item':item,'state':'pending','received':now()})
                    known.add(item['id'])
                # Keep the most recently seen ids in order (a set has no order, so truncating it dropped
                # arbitrary ids and let old items look new again).
                previous=cursor['seen'] if cursor else []
                seen_before=set(previous)
                in_order=[i['id'] for i in items]+sorted(known)  # this scan's items in feed order first
                ordered=list(dict.fromkeys(previous+[i for i in in_order if i in known and i not in seen_before]))
                self.store.put(db,'monitor-public','watch_cursor',symbol,{'seen':ordered[-300:],'checked_at':now()})
            # Only waiting notices are loaded (the table also holds every notice ever queued). A notice
            # not reached within 7 days is closed with a visible reason instead of waiting forever.
            candidates=[]
            for key,raw in db.execute("SELECT id,body FROM documents WHERE tenant='monitor-public' AND kind='pending_notice' AND json_extract(body,'$.state')='pending'").fetchall():
                n=json.loads(raw)
                try:stale=(at-datetime.fromisoformat(n['item']['published_at'])).total_seconds()>7*86400
                except (ValueError,TypeError,KeyError):stale=True
                if stale:
                    n.update(state='expired',closed=now());self.store.put(db,'monitor-public','pending_notice',key,n)
                    self.quiet(db,n['symbol'],n['item'],'NOT_REACHED');continue
                candidates.append((n['symbol'],n['item']))
            db.execute("DELETE FROM documents WHERE tenant='monitor-public' AND kind='pending_notice' AND json_extract(body,'$.state')!='pending' AND json_extract(body,'$.received')<?",((at-timedelta(days=30)).isoformat(),))
            calls=db.execute('SELECT calls FROM costs WHERE day=?',(now()[:10],)).fetchone()
            used=calls[0] if calls else 0
            pending=db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running') AND kind IN ('watch-event','live','correction','autonomous')").fetchone()[0]
            budget=int(os.environ.get('MARKETBELL_DAILY_CALLS','60'))
            if candidates and used+4>budget:
                # Explicit deferred state for holdings instead of a silent budget drop.
                for symbol,item in candidates:
                    if symbol in holdings:self.defer_notice(db,{'symbol':symbol,'notice':item},'DAILY_MODEL_BUDGET_EXHAUSTED')
            # The per-scan cap follows the scan cadence: one new investigation per 5 minutes of cadence,
            # so an hourly worker can still start up to 10 instead of 1 per hour (budget gates still apply).
            per_scan=max(1,min(10,int(os.environ.get('MARKETBELL_SCAN_MAX_NEW') or max(1,round(interval/300)))))
            queue_cap=max(4,per_scan)
            ranked=sorted(candidates,key=lambda c:(c[0] in holdings,c[1]['published_at']),reverse=True)
            for symbol,item in ranked[:per_scan]:
                # Bounded new investigations per scan (default one); every notice uses the same governor.
                if not (used+4<=budget and pending<queue_cap):break
                pending+=1;used+=4
                payload={'symbol':symbol,'notice':item}
                id=digest({'tenant':'monitor-public','kind':'watch-event','key':symbol+item['id']})[:32]
                db.execute('INSERT OR IGNORE INTO jobs(id,tenant,kind,payload,status,created,updated,priority) VALUES(?,?,?,?,?,?,?,?)',
                    (id,'monitor-public','watch-event',encoded(payload),'queued',now(),now(),0 if symbol in holdings else 3))
                cursor=self.store.get(db,'monitor-public','watch_cursor',symbol)
                cursor['seen']=list(dict.fromkeys(cursor['seen']+[item['id']]))[-300:]
                self.store.put(db,'monitor-public','watch_cursor',symbol,cursor)
                self.store.append(db,'monitor-public','Continuous watch noticed',symbol+': '+item['text']+'; investigation durably queued as '+id)
                self.tally(db,investigated=1)
                saved=self.store.get(db,'monitor-public','pending_notice',symbol+item['id'])
                saved.update(state='queued',job=id)
                self.store.put(db,'monitor-public','pending_notice',symbol+item['id'],saved)
                queued.append(id)
            status={'checkedAt':now(),'intervalSeconds':interval,'symbols':len(observed),
                'status':'Watching for new information' if len(errors)<len(observed) else 'Sources unavailable; retrying',
                'active':len(errors)<len(observed),'source':'Public news headlines; daily price baseline',
                'intradayQuotes':False,'newInvestigations':len(queued),'deferredNotices':max(0,len(candidates)-len(queued)),
                'budgetDeferred':bool(candidates and used+4>budget),'sourceErrors':errors}
            status.update(catchup=catchup,catchupCoverage='Bounded source lookback: 7 days, at most 5 returned headlines per security; complete missed-event coverage is not guaranteed.')
            prior=self.store.get(db,'monitor-public','monitor','continuous') or {}
            status['lastReceivedAt']=now() if new_screened else prior.get('lastReceivedAt')
            status['receivedItems']=sum(len(items) for _,items in discovered)
            status['newUniqueItems']=new_screened
            status['waiting']=max(0,len(candidates)-len(queued))
            status['fetchSeconds']=round(fetched_at-scan_started,2)
            status['scanSeconds']=round(time.monotonic()-scan_started,2)
            status['coverageLimitations']='Headlines, press-release wires and SEC filing indexes; full articles are not read. Query matching may include unrelated or multi-company stories. Empty polls do not prove complete coverage.'
            from . import sources
            status['channels']={k:dict(v) for k,v in sources.health.items()}
            status['source']='News headlines, press-release wires'+(', SEC EDGAR filings' if sources.sec_agent() else '')+'; daily price baseline'
            self.tally(db,read=first_read)
            db.execute("DELETE FROM documents WHERE tenant='monitor-public' AND kind='quiet' AND json_extract(body,'$.at')<?",((at-timedelta(days=14)).isoformat(),))
            status['lastSuccessAt']=status['checkedAt'] if status['active'] else prior.get('lastSuccessAt') or (prior.get('checkedAt') if prior.get('active') else None)
            if errors:
                body={'at':now(),'kind':'provider_outage','text':'; '.join(errors),'attribution':'Observed source retrieval failures; empty successful polls are not failures.'}
                db.execute('INSERT OR IGNORE INTO impact_reports VALUES(?,?,?,?,?,?)',('monitor-public',digest({'errors':sorted(errors),'day':now()[:10]}),body['at'],body['kind'],None,encoded(body)))
            self.store.put(db,'monitor-public','monitor','continuous',status)
        from .telemetry import emit
        emit('watch.scan',symbols=len(observed),received=status['receivedItems'],new_items=new_screened,queued=len(queued),
            waiting=status['waiting'],fetch_seconds=status['fetchSeconds'],scan_seconds=round(time.monotonic()-scan_started,2),
            channels_failed=sorted(k for k,v in status.get('channels',{}).items() if not v.get('ok') and not v.get('disabled')))
        return status

    QUIET={
        'WRONG_COMPANY':'The headline does not name {name} or its ticker, so I did not treat it as news about {symbol}.',
        'AMBIGUOUS_NAME':'The word that matched is also an everyday word, and nothing in the headline says it means {name} the company.',
        'DUPLICATE':'{others} ran the same story as {publisher}. I kept the {publisher} version, the stronger source, and read it once.',
        'BEFORE_MONITORING':'Published before I started watching {symbol}. I act only on news that arrives while I am watching.',
        'COMMENTARY_ONLY':'Commentary from {publisher}, not news from {name}, a regulator or an established newsroom. I will look if a stronger source reports it.',
        'ROUTINE_FILING':'A routine Form {form} filing (for example an insider share-sale report). Nothing here for you to decide.',
        'OLD_NEWS':'Published more than 7 days before I first saw it. I keep it as background for later reviews, not as new news.',
        'NOT_REACHED':'I did not get to this within 7 days because the daily AI budget or the investigation queue was full. It is now too old to act on; check the source if it matters.'}

    def trusted(self,db,symbol,publisher):
        return bool(publisher) and bool(self.store.get(db,'monitor-public','trusted_publisher',digest({'symbol':symbol,'publisher':publisher})[:24]))

    def triage(self,db,symbol,item):
        """Deterministic, free triage before any model call. Returns a quiet reason or None."""
        if item.get('type')=='regulatory_filing' and item.get('routine'):
            return 'ROUTINE_FILING'
        if item.get('source_tier')==3 and os.environ.get('MARKETBELL_INVESTIGATE_COMMENTARY','0')!='1' and not self.trusted(db,symbol,item.get('publisher')):
            return 'COMMENTARY_ONLY'
        return None

    def quiet(self,db,symbol,item,reason):
        name=SYMBOLS.get(symbol,(symbol,))[0]
        plain=self.QUIET[reason].format(name=name,symbol=symbol,publisher=item.get('publisher') or 'an unknown publisher',
            others=', '.join(item.get('also_reported_by',[])[:3]) or 'another publisher',form=item.get('form','4'))
        key=digest({'symbol':symbol,'item':item['id'],'reason':reason})[:24]
        if self.store.get(db,'monitor-public','quiet',key):
            return
        self.store.put(db,'monitor-public','quiet',key,{'id':key,'at':now(),'symbol':symbol,'reason':reason,'plain':plain,
            'headline':item.get('text','')[:300],'publisher':', '.join(item.get('also_reported_by',[])[:3]) if reason=='DUPLICATE' else item.get('publisher'),'tier':None if reason=='DUPLICATE' else item.get('source_tier'),
            'source':item.get('source'),'published_at':item.get('published_at'),'item':item})
        self.tally(db,quiet=1)

    def tally(self,db,**counts):
        from .config import market_day
        day=market_day()
        row=self.store.get(db,'monitor-public','brief_day',day) or {'day':day,'read':0,'quiet':0,'investigated':0}
        for k,v in counts.items():row[k]=row.get(k,0)+v
        self.store.put(db,'monitor-public','brief_day',day,row)

    def queue_source_notices(self,channel,notices):
        """Atomic notice deduplication and queueing, shared by recurring source detectors."""
        holdings=self.holding_symbols()
        with self.store.transaction() as db:
            cursor=self.store.get(db,'monitor-public','channel_cursor',channel) or {'seen':[]}
            known=set(cursor['seen'])
            new=[(s,n) for s,n in notices if n['id'] not in known]
            calls=db.execute('SELECT calls FROM costs WHERE day=?',(now()[:10],)).fetchone()
            pending=db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running') AND kind!='replay'").fetchone()[0]
            if not new or (calls[0] if calls else 0)+4>int(os.environ.get('MARKETBELL_DAILY_CALLS','60')) or pending>=4:
                return []
            symbol,notice=sorted(new,key=lambda pair:(pair[0] in holdings,pair[1]['published_at']),reverse=True)[0]
            id=digest({'tenant':'monitor-public','kind':'watch-event','key':symbol+notice['id']})[:32]
            db.execute('INSERT OR IGNORE INTO jobs(id,tenant,kind,payload,status,created,updated,priority) VALUES(?,?,?,?,?,?,?,?)',
                (id,'monitor-public','watch-event',encoded({'symbol':symbol,'notice':notice}),'queued',now(),now(),0 if symbol in holdings else 3))
            cursor['seen']=(cursor['seen']+[notice['id']])[-500:]
            self.store.put(db,'monitor-public','channel_cursor',channel,cursor)
            self.store.append(db,'monitor-public','Continuous '+channel+' detected',symbol+': '+notice['text'][:400]+'; queued '+id)
            return [id]

    def scheduled_health(self):
        self.engine.ensure('monitor-public')
        with self.store.transaction() as db:
            tenants={'monitor-public'}|{r[0] for r in db.execute("SELECT tenant FROM documents WHERE kind='portfolio' AND id='main' AND tenant NOT LIKE 'illustrative-%'")}
            for tenant in tenants:
                portfolio=self.store.get(db,tenant,'portfolio','main')
                if not portfolio or not portfolio.get('monitoring_enabled',True):
                    continue
                day=now()[:10]
                key=digest({'day':day,'portfolio':portfolio})
                if not self.store.get(db,tenant,'health_review',key):
                    breaches=[h['symbol'] for h in portfolio['holdings'] if h['position_pct']>portfolio['max_position_pct']]
                    result={'at':now(),'positionBreaches':breaches,'cash_pct':portfolio['cash_pct'],
                        'source':'Deterministic portfolio health; no model call needed for arithmetic.'}
                    self.store.put(db,tenant,'health_review',key,result)
                    self.store.append(db,tenant,'Scheduled portfolio health',json.dumps(result))
            self.store.put(db,'monitor-public','monitor','scheduled',{'checkedAt':now(),'active':True,
                'status':'Portfolio exposure checks and completed-session opportunity review scheduled','coverage':'Four approved securities; no whole-market coverage claim'})

    def universe_maintenance(self):
        from .universe import maintain
        with self.store.connect() as db:tenants=[r[0] for r in db.execute("SELECT tenant FROM documents WHERE kind='mandate'")]
        for tenant in tenants:maintain(self.store,tenant)

    def followups(self):
        """One bounded review job per pass; original recommendations remain untouched."""
        from .impact import HORIZONS
        with self.store.connect() as db:
            for h in HORIZONS:
                rows=db.execute("SELECT s.tenant,s.event FROM decision_snapshots s LEFT JOIN evaluations e ON e.tenant=s.tenant AND e.event=s.event AND e.horizon=? WHERE s.origin='genuine' AND s.at<=? AND e.event IS NULL ORDER BY s.at LIMIT 1",
                    (h,(datetime.now(timezone.utc)-timedelta(days=h)).isoformat())).fetchall()
                for row in rows:self.enqueue(row['tenant'],'followup',{'event':row['event'],'horizon':h},key=row['event']+str(h))

    def monitor_loop(self):
        next_watch=next_health=0
        while not self.stop.is_set():
            if not self.roles['monitor']:
                self.stop.wait(5);continue  # Standby: another process owns monitoring.
            self.last_loop['monitor']=time.monotonic()
            try:
                self.heartbeat('monitor')
                with self.store.transaction() as db:
                    db.execute("UPDATE jobs SET status='queued',updated=? WHERE status='failed' AND attempts<3 AND ((error IN ('DAILY_MODEL_BUDGET_EXHAUSTED','SPEND_BUDGET_EXHAUSTED') AND substr(updated,1,10)<?) OR (error IN ('NEBIUS_TRANSPORT_FAILURE','NEBIUS_HTTP_429','NEBIUS_HTTP_500','NEBIUS_HTTP_503','MODEL_CITATION_NOT_GROUNDED','MODEL_SCHEMA_INVALID','BACKGROUND_JOB_FAILED','INVESTIGATION_TECHNICAL_FAILURE') AND updated<?))",
                        (now(),now()[:10],(datetime.now(timezone.utc)-timedelta(seconds=int(os.environ.get('MARKETBELL_RETRY_DELAY_SECONDS','300')))).isoformat()))
                self.schedule()
                if os.environ.get('MARKETBELL_WATCH','1')=='1' and time.monotonic()>=next_watch:
                    self.scan_watch()
                    next_watch=time.monotonic()+max(60,min(3600,int(os.environ.get('MARKETBELL_WATCH_SECONDS','300'))))
                if time.monotonic()>=next_health:
                    self.scheduled_health()
                    self.universe_maintenance()
                    self.followups()
                    next_health=time.monotonic()+3600
            except Exception:
                try:
                    with self.store.transaction() as db:
                        prior=self.store.get(db,'monitor-public','monitor','continuous') or {}
                        self.store.put(db,'monitor-public','monitor','continuous',{**prior,'checkedAt':now(),'active':False,'status':'Monitor could not verify sources; retrying','intervalSeconds':300,'intradayQuotes':False})
                except Exception:
                    pass  # A database outage must not kill the recovery loop.
            self.stop.wait(30)

    def heartbeat(self, name):
        with self.store.transaction() as db:
            self.store.put(db,'monitor-public','heartbeat',name,{'at':now(),'status':'running','owner':self.owner})

    def metrics(self):
        """Operational gauges for structured logs and alerts (no tenant content)."""
        with self.store.connect() as db:
            queued=db.execute("SELECT count(*) FROM jobs WHERE status='queued' AND attempts<3").fetchone()[0]
            running=db.execute("SELECT count(*) FROM jobs WHERE status='running'").fetchone()[0]
            oldest=db.execute("SELECT min(created) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            failed=db.execute("SELECT count(*) FROM jobs WHERE status='failed' AND updated>=?",((datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(),)).fetchone()[0]
            watch=self.store.get(db,'monitor-public','monitor','continuous') or {}
            calls=db.execute("SELECT count(*),COALESCE(sum(CASE WHEN status='failed' THEN 1 ELSE 0 END),0),COALESCE(sum(reserved),0) FROM model_calls WHERE at>=?",(now()[:10],)).fetchone()
            deferred=db.execute("SELECT count(*) FROM documents WHERE tenant='monitor-public' AND kind='deferred_notice' AND json_extract(body,'$.state')='deferred'").fetchone()[0]
            attention=db.execute("SELECT count(*) FROM impact_cases WHERE attention=1 AND origin='genuine'").fetchone()[0]
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(oldest)).total_seconds() if oldest else 0
        last=watch.get('lastSuccessAt')
        return {'queue_depth':queued,'running':running,'oldest_pending_seconds':round(age),'failed_last_hour':failed,
            'last_news_success':last,'news_freshness_seconds':round((datetime.now(timezone.utc)-datetime.fromisoformat(last)).total_seconds()) if last else None,
            'source_errors':len(watch.get('sourceErrors') or []),'model_calls_today':calls[0],'model_failures_today':calls[1],
            'estimated_spend_today_usd':round(calls[2],4),'deferred_holding_notices':deferred,'awaiting_user':attention,
            'roles':dict(self.roles),'owner':self.owner}

    def run(self):
        last_heartbeat=last_recover=last_metrics=0
        from .telemetry import emit
        while not self.stop.is_set():
            self.last_loop['worker']=time.monotonic()
            try:
                if time.monotonic()-last_heartbeat>=10:
                    self.renew()
                    if self.roles['worker']:self.heartbeat('worker')
                    last_heartbeat=time.monotonic()
                if not self.roles['worker']:
                    self.stop.wait(2);continue  # Standby replica: never claims work.
                if time.monotonic()-last_recover>=30:
                    self.recover();last_recover=time.monotonic()
                if time.monotonic()-last_metrics>=60:
                    emit('worker.heartbeat',**self.metrics());last_metrics=time.monotonic()
                if not self.once():
                    self.stop.wait(1)
            except Exception as error:
                # A failed state write leaves a running job whose lease expires for recovery.
                emit('worker.error',code=type(error).__name__)
                self.stop.wait(2)

    def slot(self):
        """Additional investigation slot. Jobs are I/O-bound on the model provider, so a few slots in
        one active worker multiply throughput; claims stay atomic and fenced, budgets stay global."""
        while not self.stop.is_set():
            if not self.roles['worker']:
                self.stop.wait(2);continue
            try:
                if not self.once():self.stop.wait(1)
            except Exception:
                self.stop.wait(2)

    def run_once(self,max_seconds=240):
        """Scheduled-job mode (Azure Container Apps Job): take the roles if free, recover expired work,
        scan news once, run periodic maintenance, drain the queue within a time budget, then exit.
        Unfinished jobs stay queued (or lease-expire) for the next run; nothing is lost between runs."""
        from .telemetry import emit
        started=time.monotonic()
        self.renew()
        if not self.roles['worker']:
            emit('worker.run_once',status='standby',owner=self.owner);return {'status':'standby'}
        self.heartbeat('worker');self.recover()
        if self.roles['monitor']:
            self.heartbeat('monitor')
            try:
                with self.store.transaction() as db:
                    db.execute("UPDATE jobs SET status='queued',updated=? WHERE status='failed' AND attempts<3 AND ((error IN ('DAILY_MODEL_BUDGET_EXHAUSTED','SPEND_BUDGET_EXHAUSTED') AND substr(updated,1,10)<?) OR (error IN ('NEBIUS_TRANSPORT_FAILURE','NEBIUS_HTTP_429','NEBIUS_HTTP_500','NEBIUS_HTTP_503','MODEL_CITATION_NOT_GROUNDED','MODEL_SCHEMA_INVALID','BACKGROUND_JOB_FAILED','INVESTIGATION_TECHNICAL_FAILURE') AND updated<?))",
                        (now(),now()[:10],(datetime.now(timezone.utc)-timedelta(seconds=int(os.environ.get('MARKETBELL_RETRY_DELAY_SECONDS','300')))).isoformat()))
                self.schedule()
                if os.environ.get('MARKETBELL_WATCH','1')=='1':self.scan_watch()
                self.scheduled_health();self.universe_maintenance();self.followups()
            except Exception as error:
                emit('worker.error',code=type(error).__name__)
        processed=0
        from concurrent.futures import ThreadPoolExecutor
        def drain():
            n=0
            while time.monotonic()-started<max_seconds and self.once():
                n+=1;self.renew()
            return n
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            processed=sum(pool.map(lambda _:drain(),range(self.concurrency)))
        emit('worker.heartbeat',**self.metrics())
        for role in ('worker','monitor'):
            self.store.release_lease(role,self.owner)
        result={'status':'ran','processed':processed,'seconds':round(time.monotonic()-started,1)}
        emit('worker.run_once',**result)
        return result

    def lease_keeper(self):
        while not self.stop.wait(15):
            self.renew()

    def start(self):
        if self.store.dialect=='sqlite':
            # Local single-file mode: also refuse a second process on the same database file.
            self.lock_file=open(self.store.path+'.worker.lock','a+b')
            self.lock_file.seek(0)
            if not self.lock_file.read(1):
                self.lock_file.write(b'0')
                self.lock_file.flush()
            self.lock_file.seek(0)
            try:
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(self.lock_file.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(self.lock_file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:
                self.lock_file.close()
                raise ValueError('WORKER_ALREADY_RUNNING') from None
        self.renew()
        thread=threading.Thread(target=self.run,daemon=True,name='MarketBell-worker')
        thread.start()
        monitor=threading.Thread(target=self.monitor_loop,daemon=True,name='MarketBell-continuous-watch')
        monitor.start()
        keeper=threading.Thread(target=self.lease_keeper,daemon=True,name='MarketBell-lease')
        keeper.start()
        self.threads=[thread,monitor,keeper]
        for n in range(1,self.concurrency):
            extra=threading.Thread(target=self.slot,daemon=True,name='MarketBell-slot-'+str(n));extra.start();self.threads.append(extra)
        return thread

    def alive(self):
        return bool(self.threads) and all(t.is_alive() for t in self.threads)

    def shutdown(self,timeout=20):
        """Graceful stop: no new claims; let the current job finish within the grace period, otherwise
        return it to the queue without consuming a retry and release roles for fast takeover."""
        self.stop.set()
        deadline=time.monotonic()+timeout
        for t in self.threads:
            t.join(max(0,deadline-time.monotonic()))
        with self.active_lock:held=set(self.active_jobs)|({self.current_job} if self.current_job else set())
        job=sorted(held)[0] if len(held)==1 else sorted(held) or None
        try:
            with self.store.transaction() as db:
                for id in held:
                    db.execute("UPDATE jobs SET status='queued',attempts=CASE WHEN attempts>0 THEN attempts-1 ELSE 0 END,updated=?,lease_owner=NULL,lease_expires=NULL WHERE id=? AND lease_owner=? AND status='running'",(now(),id,self.owner))
            for role in ('worker','monitor'):
                self.store.release_lease(role,self.owner)
        except Exception:
            pass
        lock=getattr(self,'lock_file',None)
        if lock and not lock.closed:
            lock.close()  # Releases the local single-file lock (SQLite mode).
        from .telemetry import emit
        emit('worker.stopped',requeued_job=job,owner=self.owner)
