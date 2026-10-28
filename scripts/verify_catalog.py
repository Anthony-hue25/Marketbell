"""Read-only exchange-directory verification for the bounded curated identity catalog."""
import requests,json,io,csv
from backend.universe import CATALOG
from backend.config import now
from backend.store import digest
from pathlib import Path
sources=['https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt','https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt'];found={};evidence=[]
for i,url in enumerate(sources):
 r=requests.get(url,timeout=(8,20));r.raise_for_status();evidence.append({'url':url,'retrieved_at':now(),'sha256':digest(r.text)})
 for row in csv.DictReader(io.StringIO(r.text),delimiter='|'):
  s=row.get('Symbol') if i==0 else row.get('ACT Symbol')
  if s:found[s]='XNAS' if i==0 else 'XNYS' if row.get('Exchange')=='N' else row.get('Exchange')
results=[{'symbol':s,'expected_exchange':v[2],'directory_exchange':found.get(s),'match':found.get(s)==v[2]} for s,v in CATALOG.items()]
Path('docs/CATALOG_VERIFICATION.json').write_text(json.dumps({'at':now(),'sources':evidence,'results':results,'passed':sum(r['match'] for r in results),'limitations':'Curated sector assignments are product metadata, not an authoritative GICS classification. Directory connectivity does not establish redistribution rights.'},indent=2))
print('Exchange identities checked:',len(results),'matches:',sum(r['match'] for r in results))
