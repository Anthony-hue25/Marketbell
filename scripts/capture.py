"""Create judge recordings from real inference, never by scripting model verdicts."""
import json
import os
os.environ['MARKETBELL_CAPTURE_PUBLIC_REQUESTS']='1'  # Explicit bounded public-evidence capture only.
from backend.market import acquire
from backend.store import Store, digest
from backend.engine import Engine
from backend.recordings import save, DIRECTORY

def main():
    store=Store()
    engine=Engine(store)
    hashes={}
    tenant='capture-final-2'
    for symbol in ('PSX','VLO','NVDA','JPM'):
        package=acquire(symbol)
        if symbol in ('PSX','VLO'):
            package['trigger']='Upcoming issuer earnings call: investigate whether to prepare a research question note on margins, demand and risks ahead of the announced call. No financial results are assumed. '+package['trigger']
        event=engine.ingest(tenant,package,'live')
        event=engine.investigate(tenant,event['id'])
        name={'PSX':'psx','VLO':'vlo','NVDA':'risk','JPM':'routine'}[symbol]
        hashes[name]=save(name,{'package':package,'traces':{k:event[k+'_trace'] for k in ('research','challenge')},'original_outcome':event['status']})
        print(json.dumps({'symbol':symbol,'status':event['status'],'research':event['research']['decision'],
            'challenge':event['challenge']['decision'],'hash':hashes[name]}),flush=True)
        if symbol=='PSX' and event['status']=='needs':
            text='You may automatically prepare Energy opportunity research consideration notes when both independent reviews clear, all hard policies pass, the proposed position is at most 3%, and resulting sector exposure is at most 20%. This never authorizes any orders.'
            trace=engine.ai.preference(text,event)
            hashes['preference']=save('preference',{'text':text,'trace':trace})
            print(json.dumps({'preference':trace['output']}),flush=True)
    pending=DIRECTORY/'manifest.pending'
    pending.write_text(json.dumps({'hashes':hashes,'status':'Authentic captured Nebius calls over real public evidence and preserved authorized Alpaca IEX bars.',
        'market_data':'Preserved authorized Alpaca download; current session when captured, not a fresh brokerage API request.'},indent=2))
    pending.replace(DIRECTORY/'manifest.json')
    print('Authentic recordings saved.',flush=True)

if __name__=='__main__':main()
