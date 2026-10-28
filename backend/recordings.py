"""Authentic run export and verified replay; no fixture verdicts in public demo."""
import json
from pathlib import Path
from .config import ROOT, now
from .store import digest
from .ai import validate_analysis, validate_rule

DIRECTORY=ROOT/'recordings'

def save(name,payload):
    DIRECTORY.mkdir(exist_ok=True)
    body={'format':'marketbell-authentic-v1','captured_at':now(),**payload}
    identity=digest(body)
    objects=DIRECTORY/'objects'
    objects.mkdir(exist_ok=True)
    target=objects/(identity+'.json')
    if not target.exists():
        with target.open('x',encoding='utf-8') as f:
            f.write(json.dumps(body,indent=2))
    return identity

def load(name):
    if name not in ('psx','vlo','risk','routine','challenge','preference'):
        raise ValueError('RECORDING_NOT_ALLOWED')
    manifest=json.loads((DIRECTORY/'manifest.json').read_text())
    identity=manifest['hashes'].get(name)
    obj=DIRECTORY/'objects'/(str(identity)+'.json')
    body=json.loads((obj if obj.is_file() else DIRECTORY/(name+'.json')).read_text())
    if digest(body)!=manifest['hashes'].get(name) or body.get('format')!='marketbell-authentic-v1':
        raise ValueError('RECORDING_HASH_INVALID')
    if name=='preference':
        validate_rule(body['trace']['output'])
    else:
        for stage in ('research','challenge'):
            trace=body['traces'][stage]
            validate_analysis(trace['output'],body['package']['evidence'])
            if trace.get('provider')!='Nebius Token Factory' or 'nemotron' not in trace.get('model','').lower():
                raise ValueError('RECORDING_PROVIDER_INVALID')
        if 'RESEARCH' in body['traces']['challenge']['request'][1]['content']:
            raise ValueError('CHALLENGE_NOT_BLIND')
    return body

def available():
    try:
        return json.loads((DIRECTORY/'manifest.json').read_text())
    except FileNotFoundError:
        return {'hashes':{},'status':'No authentic runs recorded yet'}
