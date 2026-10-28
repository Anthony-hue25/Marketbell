"""Fault-injection RSS feed in the Google News shape (local tests only).

Control file: {"status": 200, "items": {"NVIDIA": [{"title": "...", "publisher": "Reuters", "age_seconds": 60, "site": "https://www.reuters.com"}]}}
The publisher site decides the source tier (sources.tier); default https://www.<publisher>.com.
Matches the company name at the start of the q= parameter. Requests are logged to <control>.log.
"""
import json, sys, time, hashlib
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from xml.sax.saxutils import escape

CONTROL = sys.argv[2] if len(sys.argv) > 2 else 'mock-news.json'

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            c = json.load(open(CONTROL))
        except (OSError, ValueError):
            c = {}
        q = parse_qs(urlparse(self.path).query).get('q', [''])[0]
        status = int(c.get('status', 200))
        items = []
        for name, entries in (c.get('items') or {}).items():
            if q.startswith(name + ' '):
                items += entries
        xml = ['<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>mock</title>']
        for e in items[:8]:
            pub = formatdate(time.time() - e.get('age_seconds', 60), usegmt=True)
            link = 'https://news.google.com/rss/articles/' + hashlib.sha256(e['title'].encode()).hexdigest()[:24]
            xml.append('<item><title>%s - %s</title><link>%s</link><pubDate>%s</pubDate><source url="%s">%s</source></item>'
                       % (escape(e['title']), escape(e.get('publisher', 'Reuters')), link, pub,
                          escape(e.get('site') or 'https://www.%s.com' % e.get('publisher', 'reuters').lower().replace(' ', '')), escape(e.get('publisher', 'Reuters'))))
        xml.append('</channel></rss>')
        raw = ''.join(xml).encode()
        self.send_response(status); self.send_header('Content-Type', 'application/rss+xml')
        self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
        with open(CONTROL + '.log', 'a') as log:
            log.write(json.dumps({'at': time.time(), 'q': q[:40], 'status': status, 'items': len(items)}) + '\n')

    def log_message(self, *a):
        pass

if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), Handler).serve_forever()
