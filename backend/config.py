"""Server-only configuration. No secret values are returned to the browser."""
import os
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env', override=False)
DATA = Path(os.environ.get('MARKETBELL_DATA_DIR', str(ROOT / 'work' / 'runtime')))
ENVIRONMENT = os.environ.get('MARKETBELL_ENV', 'development')
# Staging is production-like: HTTPS origin, secure cookies, live inference off by default,
# no developer-machine credential or data paths.
PRODUCTION = ENVIRONMENT in ('production', 'staging')

def developer_path(variable, windows_default):
    """Optional local path from configuration. Developer-machine defaults apply only outside
    production-like environments so a deployed container never reads another project's files."""
    value = os.environ.get(variable)
    if value:
        return Path(value)
    return None if PRODUCTION else Path(windows_default)
MODEL = os.environ.get('NEBIUS_MODEL', 'nvidia/nemotron-3-super-120b-a12b')
SYMBOLS = {'PSX': ('Phillips 66', 'Energy'), 'VLO': ('Valero Energy', 'Energy'), 'NVDA': ('NVIDIA', 'Technology'), 'JPM': ('JPMorgan Chase', 'Financials')}

def now():
    return datetime.now(timezone.utc).isoformat()

def timestamp(value):
    t = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if t.tzinfo is None:
        raise ValueError('TIMEZONE_REQUIRED')
    return t


def market_day():
    """Calendar day in New York, the exchange's day. Daily counts ("today I read...") follow it, so they
    do not reset at 8 pm New York / Port of Spain time, which is midnight UTC."""
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo
    return _dt.now(ZoneInfo('America/New_York')).date().isoformat()
