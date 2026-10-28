"""Fault-injection stand-in for the Nebius OpenAI-compatible endpoint (local tests only).

Never used in production: backend.vendor.provider only accepts a loopback override outside
production-like environments. Behaviour is read per request from a JSON control file:
  {"delay": 0, "fail_status": null, "fail_remaining": 0, "hang_seconds": 0,
   "research": "clear", "challenge": "clear"}
Every request is appended (stage, timing, outcome; no content) to <control>.log.
"""
import json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONTROL = sys.argv[2] if len(sys.argv) > 2 else 'mock-nemotron.json'
LOCK = threading.Lock()

def control():
    try:
        return json.load(open(CONTROL))
    except (OSError, ValueError):
        return {}

def stage_of(system):
    if 'skeptical challenger' in system: return 'CHALLENGE'
    if 'PROPOSED standing rule' in system: return 'PREFERENCE'
    if 'list-management' in system or 'research-list authority' in system: return 'LIST_PREFERENCE'
    if '"outcome"' in system: return 'FOLLOWUP'
    return 'RESEARCH'

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        started = time.time()
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        messages = body['messages']
        stage = stage_of(messages[0]['content'])
        payload = json.loads(messages[1]['content'])
        c = control()
        outcome = 'ok'
        with LOCK:
            if c.get('fail_remaining', 0) > 0:
                c['fail_remaining'] -= 1
                json.dump(c, open(CONTROL, 'w'))
                outcome = 'fail'
        time.sleep(c.get('hang_seconds', 0) or c.get('delay', 0))
        if outcome == 'fail':
            self.reply(int(c.get('fail_status') or 503), {'error': {'message': 'injected failure'}})
        else:
            evidence = payload.get('evidence') or payload.get('later_evidence') or []
            item = max(evidence, key=lambda e: len(e.get('text', ''))) if evidence else {'id': 'E1', 'text': ''}
            quote = item['text'][:60]
            if stage in ('RESEARCH', 'CHALLENGE'):
                trigger = payload.get('trigger', '').lower()
                kind = 'Risk' if any(w in trigger for w in ('restriction', 'recall', 'probe', 'lawsuit')) else 'Opportunity'
                out = {'decision': c.get(stage.lower(), 'clear'), 'classification': kind, 'material': True,
                       'summary': 'MOCK INFERENCE (local fault-injection test): cautious note on the cited development.',
                       'claims': [{'evidence_id': item['id'], 'quote': quote}]}
            elif stage == 'FOLLOWUP':
                out = {'outcome': 'mixed', 'summary': 'MOCK INFERENCE follow-up summary for testing.', 'claims': [{'evidence_id': item['id'], 'quote': quote}]}
            else:
                out = {'interpretable': False, 'reason': 'Mock interpreter declines', 'rule': None}
            self.reply(200, {'id': 'mock-' + str(int(started * 1000)), 'choices': [{'message': {'role': 'assistant', 'content': json.dumps(out)}}],
                             'usage': {'prompt_tokens': len(json.dumps(messages)) // 4, 'completion_tokens': 120}})
        with LOCK, open(CONTROL + '.log', 'a') as log:
            log.write(json.dumps({'at': started, 'stage': stage, 'outcome': outcome, 'seconds': round(time.time() - started, 3)}) + '\n')

    def reply(self, status, obj):
        raw = json.dumps(obj).encode()
        try:
            self.send_response(status); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *a):
        pass

if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), Handler).serve_forever()
