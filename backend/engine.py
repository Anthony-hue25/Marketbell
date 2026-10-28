"""One authority model for replay, fresh inference and completed-session operation."""
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from .config import now, timestamp, SYMBOLS
from .store import digest
from .ai import AI
from .market import session_state
from .explain import evidence_current, STALE_DAYS

DEMO_HOLDINGS=(('NVDA',5),('MSFT',4),('JPM',4),('XOM',4),('COST',3),('LLY',3))
DEMO_WATCHLIST=('PSX','VLO','AMD','GS')

# The portfolio the authentic recordings (format marketbell-authentic-v1, captured 2026-10-14) were
# decided against. Verified replays are evaluated against it, so a recorded case reproduces its
# recorded outcome however the live demo portfolio changes (decision-time context is immutable).
RECORDING_PORTFOLIO={'name':'Demonstration portfolio at recording time (2026-10-14)','version':1,'monitoring_enabled':True,'cash_pct':25,
    'max_position_pct':5,'max_sector_pct':30,'holdings':[{'symbol':'NVDA','position_pct':7,'sector':'Technology'},{'symbol':'JPM','position_pct':3,'sector':'Financials'}],
    'watchlist':['PSX','VLO'],'sector_exposure':{'Energy':16,'Technology':28,'Financials':10},'private':False}

def untouched_legacy(p):
    keys=('cash_pct','max_position_pct','max_sector_pct','holdings','watchlist','sector_exposure')
    return p.get('name')=='Isolated demonstration portfolio' and p.get('version')==1 and all(p.get(k)==RECORDING_PORTFOLIO[k] for k in keys)

def default_portfolio():
    """Demo investor: six holdings across six sectors, every one inside its own limits (5 % per stock,
    30 % per sector), so cases reach a decision instead of a guardrail. Watchlist stocks drive the
    teach-once story (PSX, VLO earnings) and the candidate comparison (AMD vs NVDA)."""
    from .universe import CATALOG
    holdings=[{'symbol':s,'position_pct':pct,'sector':CATALOG[s][1]} for s,pct in DEMO_HOLDINGS]
    exposure={}
    for h in holdings:exposure[h['sector']]=exposure.get(h['sector'],0)+h['position_pct']
    return {'name':'Isolated demonstration portfolio','version':1,'monitoring_enabled':True,'cash_pct':25,'max_position_pct':5,'max_sector_pct':30,
        'holdings':holdings,'watchlist':list(DEMO_WATCHLIST),'sector_exposure':exposure,'private':False}

def checks(event,portfolio,expected,mandate=None):
    position=event.get('position')
    current=next((h['position_pct'] for h in portfolio['holdings'] if h['symbol']==event['symbol']),0)
    if type(position) in (int,float):
        position=max(position,current)
    # Exposure is computed from holdings, so a sector with no holdings is 0 %, not unknown.
    sector=portfolio.get('sector_exposure',{}).get(event.get('sector'),0 if event.get('sector') else None)
    change=position if event['symbol'] not in {h['symbol'] for h in portfolio['holdings']} else 0
    def num(n):
        return type(n) in (int,float) and 0<=n<=100
    return {'eligible_symbol':event['symbol'] in SYMBOLS, 'completed_session_fresh':event['session']==expected,
        'position_limit':num(position) and 0<position<=portfolio['max_position_pct'],
        'sector_limit':num(sector) and num(change) and sector+change<=portfolio['max_sector_pct'],
        'cash_available':num(portfolio.get('cash_pct')) and num(change) and change<=portfolio['cash_pct'],
        'evidence_present':bool(event.get('evidence')), 'preparation_only':event.get('action_type')=='prepare_note',
        'execution_disabled':True} | ({'mandate_eligible':event['symbol'] not in mandate['excluded_symbols'] and event.get('sector') not in mandate['excluded_sectors']} if mandate else {})

def plain_policy(event,portfolio,policy):
    """One sentence a person can act on, instead of a list of internal check names."""
    s=event['symbol']
    held=next((h['position_pct'] for h in portfolio['holdings'] if h['symbol']==s),0)
    position=event.get('position') if type(event.get('position')) in (int,float) else held
    reasons=[]
    for key,ok in policy.items():
        if ok:continue
        if key=='position_limit':
            reasons.append(f"{s} would be {max(position,held):g}% of your portfolio, above your {portfolio['max_position_pct']:g}% per-stock limit" if max(position,held)>0 else f"I have no verified position size for {s}")
        elif key=='sector_limit':
            reasons.append(f"{event.get('sector','its sector')} would go above your {portfolio['max_sector_pct']:g}% sector limit (now {portfolio.get('sector_exposure',{}).get(event.get('sector'),0):g}%)")
        elif key=='cash_available':
            reasons.append(f"you hold {portfolio.get('cash_pct',0):g}% cash, not enough for the position size considered")
        elif key=='completed_session_fresh':
            reasons.append("I could not confirm today's closing price, so I have no verified price baseline")
        elif key=='evidence_present':
            reasons.append('there was no evidence to cite')
        elif key=='mandate_eligible':
            reasons.append(f'{s} or its sector is excluded by your investing rules')
        elif key=='eligible_symbol':
            reasons.append(f'{s} is outside the stocks I am allowed to cover')
        else:
            reasons.append(key.replace('_',' ')+' was not satisfied')
    if not reasons:
        return ''
    text='; '.join(reasons)
    return f"I didn't prepare a note on {s} because {text}. Nothing was changed; you can still review the research."

WEAK_EVIDENCE=('headline_only','daily_market_data','illustrative_only')

def substantive(event):
    """Each independent review must cite at least one source that is more than a headline or price
    data. Headline/price-only support can still reach a person, but never automatic authority."""
    evidence={e['id']:e for e in event.get('evidence',[])}
    def counts(item):
        if not item or item.get('type') in ('daily_market_data','illustrative_only'):
            return False
        # A headline counts only when its publisher is the issuer, a regulator or an established
        # newsroom (tier from the feed's publisher domain, see sources.tier); commentary never does.
        if item.get('type')=='headline_only':
            return item.get('source_tier') in (1,2)
        return item.get('type') not in WEAK_EVIDENCE
    def strong(review):
        return any(counts(evidence.get(c.get('evidence_id'))) for c in (review or {}).get('claims',[]))
    if event.get('simulation'):
        return True  # Isolated illustrative history is labelled synthetic; it carries no live authority.
    return strong(event.get('research')) and strong(event.get('challenge'))

def approved_before(r,event):
    """A rule never retroactively authorizes a case detected before the rule's current terms took effect:
    its approval, or its latest explicit edit or resume (`effective_from`). Widening a rule by editing it, or
    resuming it, therefore never reaches back to cases detected earlier."""
    try:
        start=r.get('effective_from') or r.get('approved_at')
        return bool(r.get('approved_at')) and timestamp(start)<=timestamp(event['created'])
    except (KeyError,TypeError,ValueError):
        return False

def matching(r,event,portfolio,at=None):
    current=next((h['position_pct'] for h in portfolio['holdings'] if h['symbol']==event['symbol']),0)
    proposed=event.get('position')
    if type(current) not in (int,float) or type(proposed) not in (int,float):return False
    position=max(current,proposed)
    added=0 if event['symbol'] in {h['symbol'] for h in portfolio['holdings']} else position
    exposure=portfolio['sector_exposure'].get(event['sector'])
    return (type(exposure) in (int,float) and r.get('active') is True and not r.get('revoked') and approved_before(r,event)
        and timestamp(r['expires'])>(timestamp(at) if at else datetime.now(timezone.utc))
        and r.get('scope')=='opportunity_note' and event['kind']=='Opportunity' and r.get('sector') in ('Any',event['sector'])
        and 0<position<=r['max_position_pct'] and exposure+added<=r['max_sector_pct'])


def rule_text(r):
    return (('Prepare' if r['effect']=='allow' else 'Ask before preparing')+' opportunity consideration notes for '+r['sector']+
        f" when both independent reviews clear, all hard policies pass, position ≤ {r['max_position_pct']}%, and resulting sector exposure ≤ {r['max_sector_pct']}%. No orders. Expires {r['expires'][:10]}.")

def current_enough(event):
    """Standing-rule authority also needs current evidence: at least one cited source no older than
    STALE_DAYS at detection (webhooks accept items up to 90 days old; SEC indexes can lag)."""
    if event.get('simulation') or event.get('mode')=='verified':
        return True  # Labelled simulations and recorded replays reproduce their recorded outcome (decision-time context is immutable).
    return evidence_current(event)

class Engine:
    def __init__(self,store,ai=None,clock=now,policy=None):
        # Replaceable boundaries: `ai` is the research/challenge provider, `policy` the deterministic hard-policy
        # evaluator (same signature as `checks`). Either can be swapped without touching the other.
        self.store=store
        self.clock=clock
        self.ai=ai or AI(store)
        self.policy=policy or checks

    def ensure(self,tenant):
        with self.store.transaction() as db:
            current=self.store.get(db,tenant,'portfolio','main')
            if not current:
                self.store.put(db,tenant,'portfolio','main',default_portfolio())
                self.store.append(db,tenant,'Workspace created','Isolated demo portfolio; no connected accounts or execution authority.')
            elif untouched_legacy(current):
                # A workspace still holding the untouched 2026-10-14 demo portfolio (which broke its own 5 %
                # limit) moves to the current demo portfolio. Edited portfolios are never changed, and
                # past cases keep the context they were decided in.
                upgraded={**default_portfolio(),'version':current['version']+1}
                self.store.put(db,tenant,'portfolio','main',upgraded)
                self.store.append(db,tenant,'Demo portfolio upgraded','Untouched 2026-10-14 demo portfolio replaced by the current demo portfolio (every holding inside its limits). Earlier cases are unchanged.')

    def ingest(self,tenant,package,mode='autonomous',recording=None):
        self.ensure(tenant)
        id=digest({'symbol':package['symbol'],'session':package['session'],'mode':mode,'recording':recording})[:24]
        with self.store.transaction() as db:
            old=self.store.get(db,tenant,'event',id)
            if old:
                if old['status']=='failed' and digest(old['evidence'])!=digest(package['evidence']):
                    for ev in package['evidence']:self.store.put_evidence(db,tenant,ev)
                    self.store.put(db,tenant,'event_revision',id+'-'+str(old['version']),old)
                    old.update(evidence=package['evidence'],research=None,challenge=None,status='queued',version=old['version']+1)
                    old.pop('research_trace',None)
                    old.pop('challenge_trace',None)
                    self.store.put(db,tenant,'event',id,old)
                    self.store.append(db,tenant,'Evidence refreshed',old['symbol']+': previous failed revision retained; investigation restarted against refreshed evidence.')
                return old
            symbol=package['symbol']
            portfolio=self.policy_portfolio(db,tenant,{'mode':mode,'recording':recording})
            proposed=next((h['position_pct'] for h in portfolio['holdings'] if h['symbol']==symbol),2)
            mandate=self.store.get(db,tenant,'mandate','main')
            event={**package,'id':id,'created':self.clock(),'name':SYMBOLS[symbol][0],'sector':SYMBOLS[symbol][1],
                'mode':mode,'recording':recording,'position':proposed,
                'status':'queued','kind':'Pending','action_type':'prepare_note',
                'mandate_context':{k:mandate[k] for k in ('objective','horizon','excluded_symbols','excluded_sectors')} if mandate else None,
                'research':None,'challenge':None,'summary':'Waiting for investigation.','version':1}
            self.store.put(db,tenant,'event',id,event)
            for ev in event['evidence']:
                self.store.put_evidence(db,tenant,ev)
            self.store.append(db,tenant,'Event noticed',f"{symbol}: {event['trigger']}. Evidence package {digest(event['evidence'])}.")
        return event

    def investigate(self,tenant,id,traces=None,expected=None):
        # External calls occur outside transactions. Version check prevents stale workers overwriting decisions.
        with self.store.transaction() as db:
            event=self.store.get(db,tenant,'event',id)
            if not event:
                raise ValueError('EVENT_NOT_FOUND')
            if event['status'] not in ('queued','investigating','failed'):
                return event
            event['status']='investigating'
            event.pop('error',None)
            self.store.put(db,tenant,'event',id,event)
        try:
            for stage,key in [('RESEARCH','research'),('CHALLENGE','challenge')]:
                if event.get(key):
                    continue
                if traces:
                    trace=traces[key]
                elif key=='challenge' and self.skip_challenge(tenant,event):
                    r=event['research']
                    held=r['decision']=='hold'
                    # Early stop with no authority attached (the Quant lesson: stop when the first review has
                    # already fixed the outcome). For a stock the investor neither owns nor watches, a first
                    # review that holds, or finds only routine news, cannot lead to a note or a promotion.
                    trace={'stage':'CHALLENGE','skipped':True,'model':None,'provider':None,'at':self.clock(),'duration_ms':0,'usage':None,
                        'reason':('First review held the case' if held else 'First review found only routine news')+' for a discovery-pool stock; a second review could not change the outcome.',
                        'output':{'decision':'hold' if held else 'no_action','classification':'Routine','material':False,'skipped':True,
                            'summary':'Second review skipped: '+('the first review already held this case' if held else 'routine news')+' on a stock you neither own nor watch.','claims':r['claims'][:1]}}
                else:
                    from .ai import bind_tenant
                    with bind_tenant(tenant):trace=self.ai.analyze(stage,event)
                with self.store.transaction() as db:
                    current=self.store.get(db,tenant,'event',id)
                    if current['version']!=event['version']:
                        raise ValueError('STALE_EVENT_VERSION')
                    current[key]=trace['output']
                    current[key+'_trace']=trace
                    event=current
                    self.store.put(db,tenant,'event',id,event)
                    self.store.append(db,tenant,stage.title()+(' skipped' if trace.get('skipped') else ' completed'),f"{event['symbol']}: {trace['output']['decision']}. Trace {digest(trace)}. "+('Blind invocation; no Research verdict in request.' if key=='challenge' else ''))
            with self.store.transaction() as db:
                result=self.resolve(db,tenant,event,expected or session_state()['latest'])
            from .universe import consider
            consider(self.store,tenant,result)
            return result
        except Exception as error:
            code=str(error)
            import re
            code=code if re.fullmatch('[A-Z0-9_]+',code) else 'INVESTIGATION_TECHNICAL_FAILURE'
            with self.store.transaction() as db:
                current=self.store.get(db,tenant,'event',id)
                if current['status']=='investigating':
                    current.update(status='failed',summary='Investigation could not be verified. No action taken.',error=code)
                    self.store.put(db,tenant,'event',id,current)
                    self.store.append(db,tenant,'Technical failure',event['symbol']+': '+code)
            raise ValueError(code) from None

    def skip_challenge(self,tenant,event):
        r=event.get('research') or {}
        if os.environ.get('MARKETBELL_SKIP_ROUTINE_CHALLENGE','1')!='1' or event.get('simulation'):
            return False
        routine=r.get('decision')=='no_action' and r.get('classification')=='Routine' and not r.get('material')
        if not (routine or r.get('decision')=='hold'):
            return False
        with self.store.connect() as db:
            p=self.store.get(db,tenant,'portfolio','main')
        return bool(p) and event['symbol'] not in p['watchlist']+[h['symbol'] for h in p['holdings']]

    def policy_portfolio(self,db,tenant,event):
        if event.get('mode')=='verified' and event.get('recording') in ('psx','vlo','risk','routine','challenge'):
            return RECORDING_PORTFOLIO
        return self.store.get(db,tenant,'portfolio','main')

    def resolve(self,db,tenant,event,expected):
        portfolio=self.policy_portfolio(db,tenant,event)
        if portfolio is RECORDING_PORTFOLIO:
            event['portfolio_basis']='Recorded case: checked against the demo portfolio as it was when recorded (NVDA 7%, JPM 3%), not today\'s.'
        research,challenge=event['research'],event['challenge']
        event['kind']=('Risk' if 'Risk' in (research['classification'],challenge['classification']) else
            'Opportunity' if 'Opportunity' in (research['classification'],challenge['classification']) else 'Routine')
        policy_start=time.perf_counter()
        event['policy']=self.policy(event,portfolio,expected,self.store.get(db,tenant,'mandate','main'))
        event['policy_duration_ms']=round((time.perf_counter()-policy_start)*1000,4)
        event['resolved_at']=self.clock()
        event['version']+=1
        event['resolution']={'research':research['decision'],'challenge':challenge['decision'],
            'evidence_ids':sorted({c['evidence_id'] for r in (research,challenge) for c in r['claims']}),
            'method':'Conservative resolution: either hold/disagreement vetoes preparation; both must clear.'}
        for rule in self.store.all(db,tenant,'rule'):
            key=rule['id']+'-'+event['id']+'-'+str(rule['version'])
            if not self.store.get(db,tenant,'rule_evaluation',key):
                matched=matching(rule,event,portfolio,self.clock())
                self.store.put(db,tenant,'rule_evaluation',key,{'rule':rule['id'],'rule_version':rule['version'],'event':event['id'],'symbol':event['symbol'],'at':self.clock(),
                    'matched':matched,'policy_passed':all(event['policy'].values()),'reviews_clear':all(r['decision']=='clear' for r in (research,challenge)),
                    'evidence_substantive':substantive(event),'evidence_current':current_enough(event),'approved_before_detection':approved_before(rule,event),
                    'reason':'Conditions match; authority still depends on policy and independent reviews.' if matched else 'Scope, expiry, activation, approval timing or current allocation conditions do not match.'})
        held_symbols={h['symbol'] for h in portfolio['holdings']}
        evaluated=[self.store.get(db,tenant,'rule_evaluation',r['id']+'-'+event['id']+'-'+str(r['version'])) for r in self.store.all(db,tenant,'rule')]
        # Decision-time context for explanations only; it never feeds authority.
        event['decision_context']={'held':event['symbol'] in held_symbols,'watched':event['symbol'] in portfolio['watchlist'],
            'sector_exposure_pct':portfolio.get('sector_exposure',{}).get(event['sector']),'max_sector_pct':portfolio.get('max_sector_pct'),
            'rules':[{'rule':e['rule'],'version':e['rule_version'],'matched':e['matched']} for e in evaluated if e]}
        event['evidence_sufficiency']=('substantive' if substantive(event) else 'headline or price data only')+('' if current_enough(event) else f'; cited evidence older than {STALE_DAYS} days at detection')
        material_risk=any(r['classification']=='Risk' and r['material'] and r['decision']!='no_action' for r in (research,challenge))
        personally_relevant=event['symbol'] in portfolio['watchlist']+[h['symbol'] for h in portfolio['holdings']]
        if material_risk and not personally_relevant:
            event.update(status='held',summary='Risk identified for an unowned discovery candidate. No portfolio exposure or action changed. '+challenge['summary'])
        elif material_risk:
            event.update(status='needs',summary='A material holding or company risk needs your judgement. '+challenge['summary'])
        elif not all(event['policy'].values()):
            event.update(status='blocked',summary=plain_policy(event,portfolio,event['policy']),
                blocked_checks=[k for k,v in event['policy'].items() if not v])
        elif research['decision']=='no_action' and challenge['decision']=='no_action':
            event.update(status='handled',summary='Routine review completed. No material action required. '+challenge['summary'])
        elif research['decision']!='clear' or challenge['decision']!='clear' or research['classification']!=challenge['classification']:
            event.update(status='held',summary='Independent reviews did not agree on preparation. '+challenge['summary'])
        elif event['kind']=='Risk':
            event.update(status='needs',summary='Holding risk needs your judgement. '+challenge['summary'])
        else:
            rules=[r for r in self.store.all(db,tenant,'rule') if matching(r,event,portfolio,self.clock())]
            effects={r['effect'] for r in rules}
            if len(effects)>1:
                event.update(status='needs',summary='Your standing rules conflict. Please decide this case.',rule_conflict=[r['id'] for r in rules])
            elif rules and effects=={'allow'} and not substantive(event):
                event.update(status='needs',rule_withheld=[r['id'] for r in rules],rule_withheld_reason='The reviews rely only on headlines or price data, which is too thin for automatic authority.',
                    summary='Both reviews cleared a research note, but they rely only on headlines or price data. Your standing rule is not applied automatically to evidence this thin; your authority is needed.')
            elif rules and effects=={'allow'} and not current_enough(event):
                event.update(status='needs',rule_withheld=[r['id'] for r in rules],rule_withheld_reason=f'All cited evidence was more than {STALE_DAYS} days old when the case was detected; stale evidence never carries automatic authority.',
                    summary=f'Both reviews cleared a research note, but every cited source was more than {STALE_DAYS} days old when this case was detected. Your standing rule is not applied automatically to stale evidence; your authority is needed.')
            elif rules and effects=={'allow'}:
                rule=sorted(rules,key=lambda r:(r['max_position_pct'],r['max_sector_pct'],r['id']))[0]
                event['ruleId']=rule['id']
                event['rule_version']=rule['version']
                event['decision_context']['applied_rule_text']=rule.get('text') or rule_text(rule)
                self.prepare(db,tenant,event,'standing rule')
                rule['uses']+=1
                rule['lastApplied']=self.clock()
                self.store.put(db,tenant,'rule',rule['id'],rule)
                event['summary']='Handled using a preference you taught MarketBell. A research note was prepared and verified. No order placed.'
                self.store.append(db,tenant,'Standing rule applied',f"{event['symbol']}: {rule['id']} v{rule['version']}. Fresh policy and both independent reviews passed.")
            else:
                event.update(status='needs',summary=research['summary']+' Both independent reviews cleared a research note; your authority is needed.')
        self.store.put(db,tenant,'event',event['id'],event)
        self.store.append(db,tenant,'Resolution recorded',event['symbol']+': '+event['status']+'. '+event['summary'])
        return event

    def prepare(self,db,tenant,event,authority):
        note={'id':event['id'],'symbol':event['symbol'],'created':self.clock(),'action_type':'prepare_note','authority':authority,
            'research':event['research'],'challenge':event['challenge'],'evidence':event['resolution']['evidence_ids'],
            'limitations':'Research consideration only. No trade instruction or execution. Review source limitations.'}
        if self.store.get(db,tenant,'note',note['id']):
            raise ValueError('DUPLICATE_ACTION')
        self.store.put(db,tenant,'note',note['id'],note)
        if self.store.get(db,tenant,'note',note['id'])!=note:
            raise ValueError('ACTION_READBACK_FAILED')
        event.update(status='handled',action={'type':'prepare_note','id':note['id'],'verified':True,'hash':digest(note)})
        self.store.append(db,tenant,'Action verified',event['symbol']+': research note persisted and read back. '+digest(note))

    def decide(self,tenant,id,choice,version):
        with self.store.transaction() as db:
            event=self.store.get(db,tenant,'event',id)
            if not event or event['version']!=version or event['status']!='needs':
                raise ValueError('STALE_OR_UNAVAILABLE_DECISION')
            event['user_involved']=True
            if choice=='acknowledge' and event['kind']=='Risk':
                event.update(status='handled',summary='You acknowledged this risk review. No research note, ownership change or order was authorized.')
            elif choice=='approve':
                p=self.policy_portfolio(db,tenant,event)
                # Replays reproduce historical authority in isolated demo only.
                expected=event['session'] if event['mode']=='verified' else session_state()['latest']
                if not all(self.policy(event,p,expected,self.store.get(db,tenant,'mandate','main')).values()) or any(event[k]['decision']!='clear' for k in ('research','challenge')):
                    raise ValueError('POLICY_OR_REVIEW_BLOCKED')
                self.prepare(db,tenant,event,'explicit user approval')
                event['summary']='You approved. Research note prepared and verified. No order placed.'
            elif choice=='reject':
                event.update(status='rejected',summary='You rejected this case. No note or order prepared.')
            else:
                raise ValueError('DECISION_INVALID')
            event['version']+=1
            self.store.put(db,tenant,'event',id,event)
            self.store.append(db,tenant,'User decision',event['symbol']+': '+choice)
        return event

    def propose(self,tenant,id,text,trace=None):
        if type(text) is not str or not 5<=len(text)<=1000:
            raise ValueError('CORRECTION_LENGTH_INVALID')
        with self.store.connect() as db:
            event=self.store.get(db,tenant,'event',id)
            if not event or event['status']!='needs':
                raise ValueError('SOURCE_DECISION_UNAVAILABLE')
        if not trace:
            from .ai import bind_tenant
            with bind_tenant(tenant):trace=self.ai.preference(text,event)
        output=trace['output']
        with self.store.transaction() as db:
            current=self.store.get(db,tenant,'event',id)
            if current['version']!=event['version'] or current['status']!='needs':
                raise ValueError('STALE_CORRECTION')
            current['user_involved']=True
            self.store.put(db,tenant,'event',id,current)
            candidate=None
            if output['interpretable']:
                candidate={**output['rule'],'id':secrets.token_hex(12),'version':1,'active':False,'uses':0,
                    'source':id,'source_version':event['version'],'correction':text,'trace':trace,'created':self.clock(),
                    'expires':(datetime.fromisoformat(self.clock())+timedelta(days=output['rule']['expires_days'])).isoformat()}
                candidate['text']=rule_text(candidate)
            self.store.put(db,tenant,'candidate','current',candidate)
            self.store.put(db,tenant,'preference',id,{'text':text,'reason':output['reason'],'grants_authority':False})
            self.store.append(db,tenant,'Correction interpreted',f"{event['symbol']}: "+output['reason']+' No standing authority until explicit confirmation.')
        return {'candidate':candidate,'reason':output['reason']}

    def approve_rule(self,tenant,id):
        with self.store.transaction() as db:
            c=self.store.get(db,tenant,'candidate','current')
            if not c or c['id']!=id or timestamp(c['created'])<datetime.fromisoformat(self.clock())-timedelta(minutes=30):
                raise ValueError('CANDIDATE_MISSING_OR_EXPIRED')
            e=self.store.get(db,tenant,'event',c['source'])
            if e['status']!='needs' or e['version']!=c['source_version']:
                raise ValueError('SOURCE_DECISION_CHANGED')
            c.update(active=True,approved_at=self.clock(),effective_from=self.clock())
            self.store.put(db,tenant,'rule',id,c)
            self.store.put(db,tenant,'candidate','current',None)
            self.store.append(db,tenant,'Standing rule approved',id+' v1: '+c['text'])
        return c

    def rule_action(self,tenant,id,action,version,updates=None):
        with self.store.transaction() as db:
            r=self.store.get(db,tenant,'rule',id)
            if not r or r['version']!=version or r.get('revoked'):
                raise ValueError('STALE_OR_REVOKED_RULE')
            self.store.put(db,tenant,'rule_revision',id+'-'+str(r['version']),dict(r))
            if action=='pause': r['active']=False
            elif action=='resume':
                if timestamp(r['expires'])<=datetime.fromisoformat(self.clock()):
                    raise ValueError('RULE_EXPIRED')
                r['active']=True
                r['effective_from']=self.clock()
            elif action=='revoke': r.update(active=False,revoked=True)
            elif action=='edit':
                from .ai import validate_rule
                new={k:r[k] for k in ('scope','sector','max_position_pct','max_sector_pct','effect','expires_days')}
                if type(updates) is not dict or not set(updates)<= {'max_position_pct','max_sector_pct','effect','sector'}:
                    raise ValueError('RULE_EDIT_INVALID')
                new.update(updates)
                validate_rule({'interpretable':True,'reason':'Explicit user edit','rule':new})
                r.update(new)
                r['effective_from']=self.clock()
            else: raise ValueError('RULE_ACTION_INVALID')
            r['version']+=1
            r['text']=rule_text(r)
            self.store.put(db,tenant,'rule',id,r)
            self.store.append(db,tenant,'Standing rule '+action,id+f" v{r['version']}: "+r['text'])
        return r
