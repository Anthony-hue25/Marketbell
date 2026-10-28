"""WSGI entry point for the website/API workload.

MARKETBELL_WORKER=0 (the image default) keeps background monitoring out of the web process; it runs
in the separate worker workload (backend.worker_main). Setting MARKETBELL_WORKER=1 runs both in one
process for a single-container deployment; leases still prevent duplicate processing.
"""
import time
from .app import Application
from .telemetry import emit

def _create(attempts=30):
    # Survive a database that is still starting (cold start, failover, restore) instead of crash-looping.
    for attempt in range(attempts):
        try:
            return Application()
        except Exception as error:
            if attempt == attempts - 1 or str(error) not in ('DATABASE_UNAVAILABLE','DATABASE_BUSY','DATABASE_POOL_EXHAUSTED'):
                raise
            emit('api.database_wait', attempt=attempt + 1, code=str(error))
            time.sleep(min(20, 2 + attempt * 2))

application=_create()
