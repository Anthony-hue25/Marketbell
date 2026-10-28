"""Non-trading universe management. All ownership records require separate user action."""
import csv
import io
import json
import math
from datetime import datetime,timedelta,timezone
from .config import SYMBOLS,now
from .store import digest

CATALOG={
 'PSX':('Phillips 66','Energy','XNYS'),'VLO':('Valero Energy','Energy','XNYS'),'NVDA':('NVIDIA','Technology','XNAS'),'JPM':('JPMorgan Chase','Financials','XNYS'),
 'AAPL':('Apple','Technology','XNAS'),'MSFT':('Microsoft','Technology','XNAS'),'GOOGL':('Alphabet','Communication Services','XNAS'),
 'AMZN':('Amazon','Consumer Discretionary','XNAS'),'META':('Meta Platforms','Communication Services','XNAS'),'TSLA':('Tesla','Consumer Discretionary','XNAS'),
 'XOM':('Exxon Mobil','Energy','XNYS'),'CVX':('Chevron','Energy','XNYS'),'COP':('ConocoPhillips','Energy','XNYS'),
 'BAC':('Bank of America','Financials','XNYS'),'GS':('Goldman Sachs','Financials','XNYS'),'V':('Visa','Financials','XNYS'),
 'JNJ':('Johnson & Johnson','Healthcare','XNYS'),'UNH':('UnitedHealth Group','Healthcare','XNYS'),'LLY':('Eli Lilly','Healthcare','XNYS'),
 'WMT':('Walmart','Consumer Staples','XNAS'),'COST':('Costco','Consumer Staples','XNAS'),'PG':('Procter & Gamble','Consumer Staples','XNYS'),
 'CAT':('Caterpillar','Industrials','XNYS'),'GE':('GE Aerospace','Industrials','XNYS'),'RTX':('RTX','Industrials','XNYS'),
 'NEE':('NextEra Energy','Utilities','XNYS'),'DUK':('Duke Energy','Utilities','XNYS'),'PLD':('Prologis','Real Estate','XNYS'),
 'AMD':('Advanced Micro Devices','Technology','XNAS'),'AVGO':('Broadcom','Technology','XNAS'),'NFLX':('Netflix','Communication Services','XNAS'),'DIS':('Walt Disney','Communication Services','XNYS')}
SYMBOLS.update({s:(v[0],v[1]) for s,v in CATALOG.items()})

def default_mandate():
 return {'version':1,'objective':'Long-term, evidence-led research; no orders','horizon':'long_term','excluded_sectors':[],
  'excluded_symbols':[],'max_watchlist':12,'automatic':{'promote':False,'deprioritize':False,'archive':False},
  'max_position_pct':5,'max_sector_pct':30,'approved_at':None}

def symbol(value,exchange=None):
 if type(value) is not str or value.upper().strip() not in CATALOG:raise ValueError('SYMBOL_OUTSIDE_VALIDATED_CATALOG')
 s=value.upper().strip()
 if exchange and exchange not in (CATALOG[s][2],):raise ValueError('SYMBOL_EXCHANGE_MISMATCH')
 return s

def number(value,low,high):
 if type(value) is bool:raise ValueError('NUMBER_INVALID')
 try:n=float(value)
 except (ValueError,TypeError):raise ValueError('NUMBER_INVALID') from None
 if not math.isfinite(n) or not low<=n<=high:raise ValueError('NUMBER_INVALID')
 return n

def parse_rows(rows):
 if type(rows) is not list or not 1<=len(rows)<=80:raise ValueError('UNIVERSE_ROWS_INVALID')
 out=[];seen=set()
 for row in rows:
  if type(row) is not dict:raise ValueError('UNIVERSE_ROW_INVALID')
  s=symbol(row.get('ticker',row.get('symbol')),row.get('exchange'))
  if s in seen:raise ValueError('DUPLICATE_COMPANY')
  seen.add(s);tier=row.get('tier','watchlist').lower()
  if tier not in ('portfolio','watchlist'):raise ValueError('TIER_INVALID')
  h=row.get('horizon','long_term')
  if h not in ('short_term','medium_term','long_term'):raise ValueError('HORIZON_INVALID')
  qty=number(row.get('quantity',0) or 0,0,1e9)
  if tier=='portfolio' and qty<=0:raise ValueError('HOLDING_QUANTITY_REQUIRED')
  out.append({'symbol':s,'exchange':CATALOG[s][2],'name':CATALOG[s][0],'sector':CATALOG[s][1],
   'tier':tier,'quantity':qty if tier=='portfolio' else 0,'cost_basis':number(row['cost_basis'],0,1e9) if row.get('cost_basis') not in ('',None) else None,
   'position_pct':number(row.get('position_pct',0) or 0,0,100),'horizon':h,'pinned':row.get('pinned') in (True,'true','1','yes'),
   'ownership':'User-entered; not verified by a brokerage' if tier=='portfolio' else 'Potential investment; not owned'})
 return out

def parse_csv(text):
 if type(text) is not str or len(text)>20000:raise ValueError('CSV_SIZE_INVALID')
 try:return parse_rows(list(csv.DictReader(io.StringIO(text.lstrip('\ufeff')))))
 except csv.Error:raise ValueError('CSV_INVALID') from None

def validate_mandate(value):
 if type(value) is not dict or set(value)!=set(default_mandate())-{'version','approved_at'}:raise ValueError('MANDATE_SCHEMA_INVALID')
 if type(value['objective']) is not str or not 5<=len(value['objective'])<=500 or value['horizon'] not in ('short_term','medium_term','long_term'):raise ValueError('MANDATE_INVALID')
 if type(value['automatic']) is not dict or set(value['automatic'])!={'promote','deprioritize','archive'} or any(type(v) is not bool for v in value['automatic'].values()):raise ValueError('MANDATE_AUTHORITY_INVALID')
 sectors={v[1] for v in CATALOG.values()}
 if type(value['excluded_sectors']) is not list or not set(value['excluded_sectors'])<=sectors or type(value['excluded_symbols']) is not list:raise ValueError('MANDATE_EXCLUSION_INVALID')
 value={**value,'excluded_symbols':[symbol(s) for s in value['excluded_symbols']]}
 if type(value['max_watchlist']) is not int or not 1<=value['max_watchlist']<=30:raise ValueError('WATCHLIST_LIMIT_INVALID')
 value['max_position_pct']=number(value['max_position_pct'],0.1,5);value['max_sector_pct']=number(value['max_sector_pct'],1,30)
 return value

def ledger(store,db,tenant,kind,body,key=None,at=None):
 key=key or digest(body)
 if not store.get(db,tenant,'universe_history',key):
  value={'at':at or now(),'kind':kind,**body};store.put(db,tenant,'universe_history',key,value)
  store.append(db,tenant,'Universe '+kind,json.dumps(value))

def current(store,tenant):
 with store.connect() as db:
  p=store.get(db,tenant,'portfolio','main')
  m=store.get(db,tenant,'mandate','main') or default_mandate()
  entries=store.all(db,tenant,'universe')
  if not entries and p:
   entries=[{'symbol':h['symbol'],'tier':'portfolio','position_pct':h['position_pct'],'quantity':None,'pinned':True,'ownership':'Illustrative demo holding; not brokerage verified'} for h in p['holdings']]+[{'symbol':s,'tier':'watchlist','pinned':False} for s in p['watchlist']]
  return {'entries':entries,'mandate':m,'portfolio_version':p['version'] if p else 1,
   'recommendations':[json.loads(r[0]) for r in db.execute("SELECT body FROM documents WHERE tenant=? AND kind='universe_recommendation' ORDER BY json_extract(body,'$.created') DESC LIMIT 30",(tenant,))],
   'list_rules':store.all(db,tenant,'list_rule'),'list_candidate':store.get(db,tenant,'list_candidate','current'),
   'history':[json.loads(r[0]) for r in db.execute("SELECT body FROM documents WHERE tenant=? AND kind='universe_history' ORDER BY json_extract(body,'$.at') DESC LIMIT 60",(tenant,))],
   'catalog':[{'symbol':s,'name':v[0],'sector':v[1],'exchange':v[2]} for s,v in CATALOG.items()],
   'coverage':'32 curated US company identities; broader unvalidated symbols are rejected. Search news is not full-market coverage.'}

def commit(store,tenant,rows,mandate,version,confirmed=False,cash=25):
 if confirmed is not True:raise ValueError('EXPLICIT_CONFIRMATION_REQUIRED')
 rows=parse_rows(rows);m=validate_mandate(mandate)
 if sum(r['tier']=='watchlist' for r in rows)>m['max_watchlist']:raise ValueError('WATCHLIST_FULL')
 if sum(r['position_pct'] for r in rows if r['tier']=='portfolio')+number(cash,0,100)>100.00001:raise ValueError('ALLOCATION_EXCEEDS_TOTAL')
 with store.transaction() as db:
  p=store.get(db,tenant,'portfolio','main')
  if not p or p['version']!=version:raise ValueError('STALE_PORTFOLIO')
  old=store.get(db,tenant,'mandate','main') or default_mandate()
  m.update(version=old['version']+1,approved_at=now());store.put(db,tenant,'mandate','main',m)
  for rule in store.all(db,tenant,'list_rule'):
   if rule.get('active') and not m['automatic'].get(rule['operation'],False):
    rule.update(active=False,version=rule['version']+1);store.put(db,tenant,'list_rule',rule['id'],rule);ledger(store,db,tenant,'list rule paused by mandate',rule)
  for r in store.all(db,tenant,'universe'):
   if r['symbol'] not in {e['symbol'] for e in rows}:
    r.update(tier='archived');store.put(db,tenant,'universe',r['symbol'],r)
  for r in rows:store.put(db,tenant,'universe',r['symbol'],r)
  exposure={s:0 for s in {v[1] for v in CATALOG.values()}}
  for r in rows:
   if r['tier']=='portfolio':exposure[r['sector']]+=r['position_pct']
  p.update(version=p['version']+1,name='Manually maintained portfolio',holdings=[r for r in rows if r['tier']=='portfolio'],watchlist=[r['symbol'] for r in rows if r['tier']=='watchlist'],sector_exposure=exposure,cash_pct=float(cash),max_position_pct=m['max_position_pct'],max_sector_pct=m['max_sector_pct'])
  store.put(db,tenant,'portfolio','main',p)
  ledger(store,db,tenant,'onboarding confirmed',{'mandate':m,'entries':rows,'description':'User-maintained records only; no trades executed or ownership verified.'})
 return current(store,tenant)

def consider(store,tenant,event):
 """Evidence-backed tier proposal; never mutate portfolio holdings here."""
 with store.transaction() as db:
  p=store.get(db,tenant,'portfolio','main');m=store.get(db,tenant,'mandate','main') or default_mandate()
  if not p:return
  event_at=event.get('resolved_at',event['created']) if event.get('simulation') else now()
  s=event['symbol'];entry=store.get(db,tenant,'universe',s)
  holding=s in {h['symbol'] for h in p['holdings']}
  if not entry:
   entry={'symbol':s,'name':CATALOG[s][0],'sector':CATALOG[s][1],'tier':'portfolio' if holding else 'watchlist' if s in p['watchlist'] else 'pool','pinned':holding,'discovered':event_at}
   store.put(db,tenant,'universe',s,entry);ledger(store,db,tenant,'discovered',{'symbol':s,'event':event['id'],'evidence':[e['id'] for e in event['evidence']]},key='discover-'+s,at=event_at)
  entry['last_reviewed']=event.get('resolved_at',now());store.put(db,tenant,'universe',s,entry)
  if not event.get('research') or not event.get('challenge'):return
  if all(event[k]['decision']=='no_action' for k in ('research','challenge')):
   ledger(store,db,tenant,'routine observation',{'symbol':s,'event':event['id'],'ownership_changed':False},key='routine-'+event['id'],at=event_at);return
  key='rec-'+event['id']
  if store.get(db,tenant,'universe_recommendation',key):return
  eligible=s not in m['excluded_symbols'] and entry['sector'] not in m['excluded_sectors']
  clear=all(event[k]['decision']=='clear' and event[k]['classification']=='Opportunity' for k in ('research','challenge'))
  adverse=event['kind']=='Risk'
  if clear and entry['tier']=='watchlist':
   ledger(store,db,tenant,'watchlist review',{'symbol':s,'event':event['id'],'reason':'Already monitored; no duplicate promotion.'},key='review-'+event['id'],at=event_at);return
  action='review_holding' if holding else 'promote' if clear else 'deprioritize'
  if holding and not adverse:
   ledger(store,db,tenant,'holding observation',{'symbol':s,'event':event['id'],'reason':'No independent evidence-backed portfolio change proposed.'},key='holding-'+event['id'],at=event_at);return
  if action=='deprioritize' and entry.get('priority')=='low' and entry['tier']=='pool':
   ledger(store,db,tenant,'pool review',{'symbol':s,'event':event['id'],'reason':'Already deprioritized; no duplicate list action.'},key='pool-review-'+event['id'],at=event_at);return
  fresh_news=False
  for ev in event['evidence']:
   try:
    published=datetime.fromisoformat(ev['published_at'].replace('Z','+00:00'))
    if datetime.now(timezone.utc)-timedelta(days=7)<=published<=datetime.now(timezone.utc):fresh_news=True
   except (KeyError,TypeError,ValueError,AttributeError):pass
  # A research watchlist changes no portfolio exposure. Note preparation retains all eight checks.
  hard=eligible and bool(event.get('policy')) and event['policy'].get('evidence_present') and (fresh_news or event['policy'].get('completed_session_fresh'))
  if action=='promote':hard=hard and len(p['watchlist'])<m['max_watchlist'] and clear
  from .engine import approved_before
  # Same timing rule as note rules: never authorize a case detected before the rule's current terms took effect.
  rules=[r for r in store.all(db,tenant,'list_rule') if r.get('active') and not r.get('revoked') and r.get('expires','')>now() and (event.get('simulation') or approved_before(r,event)) and r['operation']==action and r['sector'] in ('Any',entry['sector']) and len(p['watchlist'])<r['max_watchlist']]
  effects={r['effect'] for r in rules}
  learned=bool(rules and effects=={'allow'})
  conflict=len(effects)>1
  automatic=bool(not conflict and 'ask' not in effects and not entry.get('pinned') and not holding and hard and ((action=='promote' and (m['automatic']['promote'] or learned)) or (action=='deprioritize' and (m['automatic']['deprioritize'] or learned))))
  reason=('Independent reviews clear; source freshness and mandate checked. List organization changes no exposure; note preparation retains its separate quantitative governor.' if clear else 'Reviews remain uncertain or adverse; lower priority does not mean no risk.')
  status='blocked' if not hard else 'escalated' if holding else 'applied' if automatic else 'proposed'
  rec={'id':key,'event':event['id'],'symbol':s,'action':action,'status':status,'created':event_at,'version':1,
   'portfolio_version':p['version'],'mandate_version':m['version'],'reason':reason,'research':event['research'],'challenge':event['challenge'],
   'evidence':[e['id'] for e in event['evidence']],'policy':event['policy'],'ownership_changed':False,'authority':'Explicit mandate v'+str(m['version']) if automatic else 'Prepare and ask',
   'expires':(datetime.fromisoformat(event_at)+timedelta(days=2)).isoformat(),
   'hypothesis':'Candidate may merit a place in the research watchlist; future evidence may contradict this rationale.',
   'horizons_days':[7,30,90],'comparison_benchmark':'Compare research usefulness and later evidence, not simulated trading returns.'}
  if automatic:
   if learned:
    rule=sorted(rules,key=lambda r:r['max_watchlist'])[0];rule['uses']+=1;rule['lastApplied']=now();store.put(db,tenant,'list_rule',rule['id'],rule);rec['ruleId']=rule['id'];rec['authority']='Approved list rule '+rule['id']
   if action=='promote':
    entry['tier']='watchlist';entry['priority']='high'
    if s not in p['watchlist']:p['watchlist'].append(s)
    p['version']+=1;store.put(db,tenant,'portfolio','main',p)
   else:
    entry['priority']='low'
    if entry['tier']=='watchlist':
     entry['tier']='pool';p['watchlist']=[x for x in p['watchlist'] if x!=s];p['version']+=1;store.put(db,tenant,'portfolio','main',p)
   store.put(db,tenant,'universe',s,entry)
  store.put(db,tenant,'universe_recommendation',key,rec);ledger(store,db,tenant,'candidate '+status,rec,key=key,at=event_at)

def decide(store,tenant,id,choice,version):
 with store.transaction() as db:
  r=store.get(db,tenant,'universe_recommendation',id);p=store.get(db,tenant,'portfolio','main');m=store.get(db,tenant,'mandate','main') or default_mandate()
  if not r or r['status']!='proposed' or r['version']!=version or r['portfolio_version']!=p['version'] or r['mandate_version']!=m['version'] or r['expires']<now():raise ValueError('STALE_RECOMMENDATION')
  if choice not in ('approve','reject'):raise ValueError('DECISION_INVALID')
  if choice=='approve' and r['action']=='promote':
   if len(p['watchlist'])>=m['max_watchlist'] or r['symbol'] in m['excluded_symbols'] or CATALOG[r['symbol']][1] in m['excluded_sectors']:raise ValueError('MANDATE_BLOCKED')
   entry=store.get(db,tenant,'universe',r['symbol']);entry['tier']='watchlist';entry['priority']='high';store.put(db,tenant,'universe',r['symbol'],entry)
   if r['symbol'] not in p['watchlist']:p['watchlist'].append(r['symbol'])
   p['version']+=1;store.put(db,tenant,'portfolio','main',p)
  r.update(status='confirmed_recommendation' if choice=='approve' else 'rejected',version=version+1,decided_at=now(),ownership_changed=False)
  store.put(db,tenant,'universe_recommendation',id,r);ledger(store,db,tenant,'user '+choice,r)
 return r

def archive(store,tenant,s,version,confirm=False):
 with store.transaction() as db:
  p=store.get(db,tenant,'portfolio','main');m=store.get(db,tenant,'mandate','main') or default_mandate();e=store.get(db,tenant,'universe',symbol(s))
  if not e and p and s in p['watchlist']:
   e={'symbol':s,'tier':'watchlist','pinned':False}
  if not e or not p or p['version']!=version:raise ValueError('STALE_UNIVERSE')
  if e.get('pinned') or e['tier']=='portfolio':raise ValueError('PINNED_OR_HOLDING_PROTECTED')
  if not confirm and not m['automatic']['archive']:raise ValueError('EXPLICIT_AUTHORITY_REQUIRED')
  e['tier']='archived';p['watchlist']=[x for x in p['watchlist'] if x!=s];p['version']+=1
  store.put(db,tenant,'universe',s,e);store.put(db,tenant,'portfolio','main',p);ledger(store,db,tenant,'archived',{'symbol':s,'automatic':not confirm,'origin':'illustrative' if tenant.startswith('illustrative-') else 'genuine','authority':'User confirmation' if confirm else 'Mandate v'+str(m['version'])})
 return e
# A proposal remains non-authoritative until an explicit confirmation endpoint saves it.
def propose_list_rule(store,ai,tenant,id,text):
 from .ai import validate_list_rule
 with store.connect() as db:
  rec=store.get(db,tenant,'universe_recommendation',id)
 if not rec or rec['status']!='proposed':raise ValueError('RECOMMENDATION_UNAVAILABLE')
 if type(text) is not str or not 10<=len(text)<=1000:raise ValueError('CORRECTION_LENGTH_INVALID')
 from .ai import bind_tenant
 with bind_tenant(tenant):trace=ai.call('LIST_PREFERENCE',{'user_correction':text,'candidate':{'symbol':rec['symbol'],'sector':CATALOG[rec['symbol']][1]},
  'purpose':'Propose a narrow list-management rule. Never infer ownership or trading authority.'},validate_list_rule)
 out=trace['output'];candidate=None
 with store.transaction() as db:
  if out['interpretable']:
   candidate={**out['rule'],'id':digest({'rec':id,'trace':trace})[:24],'source':id,'created':now(),'correction':text,'trace_hash':digest(trace),'active':False,'version':1,'uses':0}
   store.put(db,tenant,'list_candidate','current',candidate)
  ledger(store,db,tenant,'list correction interpreted',{'source':id,'text':text,'reason':out['reason'],'authority_granted':False})
 return {'candidate':candidate,'reason':out['reason']}

def list_rule_action(store,tenant,id,action,version=None,updates=None):
 with store.transaction() as db:
  if action=='approve':
   r=store.get(db,tenant,'list_candidate','current')
   if not r or r['id']!=id or r['created']<(datetime.now(timezone.utc)-timedelta(minutes=30)).isoformat():raise ValueError('CANDIDATE_MISSING_OR_EXPIRED')
   r.update(active=True,approved_at=now(),effective_from=now(),expires=(datetime.now(timezone.utc)+timedelta(days=r['expires_days'])).isoformat());store.put(db,tenant,'list_candidate','current',None)
  else:
   r=store.get(db,tenant,'list_rule',id)
   if not r or r['version']!=version or r.get('revoked'):raise ValueError('STALE_OR_REVOKED_RULE')
   if action=='pause':r['active']=False
   elif action=='revoke':r.update(active=False,revoked=True)
   elif action=='edit':
    from .ai import validate_list_rule
    if type(updates) is not dict or not set(updates)<= {'max_watchlist','effect'}:raise ValueError('RULE_EDIT_INVALID')
    changed={k:r[k] for k in ('scope','operation','sector','max_watchlist','effect','expires_days')};changed.update(updates)
    validate_list_rule({'interpretable':True,'reason':'Explicit user edit','rule':changed});r.update(changed);r['effective_from']=now()
   elif action=='resume':
    if r['expires']<now():raise ValueError('RULE_EXPIRED')
    r['active']=True;r['effective_from']=now()
   else:raise ValueError('RULE_ACTION_INVALID')
   r['version']+=1
  store.put(db,tenant,'list_rule',id,r);ledger(store,db,tenant,'list rule '+action,r)
 return r

def propose_portfolio_change(store,tenant,event,action,holding=None):
 if action not in ('add_candidate','remove_holding','replace_holding'):raise ValueError('PROPOSAL_ACTION_INVALID')
 with store.transaction() as db:
  p=store.get(db,tenant,'portfolio','main');m=store.get(db,tenant,'mandate','main') or default_mandate()
  e=store.get(db,tenant,'event',event)
  if not e or not e.get('research') or not e.get('challenge'):raise ValueError('VERIFIED_INVESTIGATION_REQUIRED')
  owned={h['symbol'] for h in p['holdings']}
  if action in ('remove_holding','replace_holding') and holding not in owned:raise ValueError('HOLDING_NOT_FOUND')
  if action=='add_candidate' and e['symbol'] in owned:raise ValueError('ALREADY_RECORDED_HOLDING')
  if action=='replace_holding' and (e.get('comparison') or {}).get('holding')!=holding:raise ValueError('COMPARATIVE_RESEARCH_REQUIRED')
  id='portfolio-'+digest({'event':event,'action':action,'holding':holding})[:24]
  existing=store.get(db,tenant,'universe_recommendation',id)
  if existing:return existing
  permitted=action=='remove_holding' or all(e.get('policy',{}).values()) and all(e[k]['decision']=='clear' for k in ('research','challenge'))
  r={'id':id,'event':event,'symbol':e['symbol'],'holding':holding,'action':action,'status':'proposed' if permitted else 'blocked','version':1,'created':now(),
   'portfolio_version':p['version'],'mandate_version':m['version'],'expires':(datetime.now(timezone.utc)+timedelta(days=2)).isoformat(),
   'reason':'Confirm a research recommendation only. Ownership is unchanged. '+e['summary'],'research':e['research'],'challenge':e['challenge'],
   'policy':e['policy'],'evidence':[v['id'] for v in e['evidence']],'ownership_changed':False,'authority':'Prepare and ask',
   'hypothesis':'Later evidence may support or contradict the original rationale. No hypothetical return establishes correctness.','horizons_days':[7,30,90]}
  store.put(db,tenant,'universe_recommendation',id,r);ledger(store,db,tenant,'portfolio recommendation',r,key=id)
  return r

def maintain(store,tenant):
 # Only previously deprioritized, inactive, unpinned candidates can be automatically archived.
 with store.connect() as db:
  m=store.get(db,tenant,'mandate','main') or default_mandate();p=store.get(db,tenant,'portfolio','main');entries=store.all(db,tenant,'universe')
 if not p or not m['automatic']['archive']:return
 for e in entries:
  if e['tier'] in ('watchlist','pool') and not e.get('pinned') and e.get('priority')=='low' and e.get('last_reviewed','9999')<(datetime.now(timezone.utc)-timedelta(days=30)).isoformat():
   try:archive(store,tenant,e['symbol'],p['version']);p['version']+=1
   except ValueError:continue


def exposure_issues(portfolio,mandate=None):
 if not portfolio:return []
 mandate=mandate or default_mandate();issues=[]
 for h in portfolio['holdings']:
  if h['position_pct']>portfolio['max_position_pct']:issues.append(h['symbol']+' exceeds the '+str(portfolio['max_position_pct'])+'% position limit.')
  if h['symbol'] in mandate['excluded_symbols'] or CATALOG[h['symbol']][1] in mandate['excluded_sectors']:issues.append(h['symbol']+' conflicts with an exclusion. Recorded ownership is unchanged.')
 for sector,pct in portfolio['sector_exposure'].items():
  if pct>portfolio['max_sector_pct']:issues.append(sector+' exposure exceeds the '+str(portfolio['max_sector_pct'])+'% sector limit.')
 return issues
