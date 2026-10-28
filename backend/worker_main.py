"""Background worker process: news monitoring, Nemotron investigations, follow-ups.

Runs independently of the website/API and of any browser session. All state is durable in the
database; leases make exactly one replica active at a time (others stay on standby), and a job
whose owner disappears is requeued once its lease expires.

    python -m backend.worker_main

A small internal HTTP server answers container probes on MARKETBELL_HEALTH_PORT (default 8081):
  /livez   process and threads alive
  /healthz ready: database reachable and loops progressing (standby counts as healthy)
It has no ingress in Azure and exposes no tenant data.
"""
import json
import os
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault('MARKETBELL_ROLE', 'worker')

from .store import Store  # noqa: E402
from .engine import Engine  # noqa: E402
from .worker import Worker  # noqa: E402
from .telemetry import emit  # noqa: E402


def health(worker, store):
    loops_ok = all(time.monotonic() - worker.last_loop[name] < 180 for name in ('worker',)) and worker.alive()
    try:
        with store.connect() as db:
            db.execute('SELECT 1').fetchone()
        database = True
    except Exception:
        database = False
    return {'alive': worker.alive(), 'database': database, 'loops': loops_ok, 'roles': dict(worker.roles),
            'standby': not worker.roles['worker'], 'active_jobs': len(worker.active_jobs), 'concurrency': worker.concurrency}


def serve(worker, store, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/livez':
                ok, body = worker.alive(), {'alive': worker.alive()}
            elif self.path in ('/healthz', '/readyz'):
                body = health(worker, store)
                ok = body['alive'] and body['database'] and body['loops']
            else:
                self.send_response(404); self.end_headers(); return
            raw = json.dumps(body).encode()
            self.send_response(200 if ok else 503)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    # Probes reach the container on its own address; the worker app has no ingress in Azure.
    server = ThreadingHTTPServer((os.environ.get('MARKETBELL_HEALTH_BIND', '0.0.0.0'), port), Handler)  # nosec B104
    threading.Thread(target=server.serve_forever, daemon=True, name='MarketBell-health').start()
    return server


def main():
    import sys
    once = '--once' in sys.argv
    store = None
    for attempt in range(30):
        # Survive a database that is still starting (cold start, restore, failover).
        try:
            store = Store()
            break
        except Exception as error:
            emit('worker.database_wait', attempt=attempt + 1, code=type(error).__name__)
            time.sleep(min(30, 2 + attempt * 2))
    if store is None:
        raise SystemExit('DATABASE_UNAVAILABLE')
    worker = Worker(store, Engine(store))
    if once:
        # Scheduled-job mode: one bounded pass, then exit (Container Apps Job, cron).
        budget = int(os.environ.get('MARKETBELL_RUN_ONCE_SECONDS', '240'))
        result = worker.run_once(budget)
        raise SystemExit(0 if result['status'] in ('ran', 'standby') else 1)
    worker.start()
    port = int(os.environ.get('MARKETBELL_HEALTH_PORT', '8081'))
    server = serve(worker, store, port)
    emit('worker.started', owner=worker.owner, database=store.dialect, roles=dict(worker.roles), health_port=port)
    stopping = threading.Event()

    def terminate(signum, frame):
        stopping.set()

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    while not stopping.wait(1):
        if not worker.alive():
            emit('worker.thread_died', owner=worker.owner)
            break  # Exit non-zero so the platform restarts the replica.
    worker.shutdown(int(os.environ.get('MARKETBELL_SHUTDOWN_GRACE_SECONDS', '20')))
    server.shutdown()
    raise SystemExit(0 if stopping.is_set() else 1)


if __name__ == '__main__':
    main()
