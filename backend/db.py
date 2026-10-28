"""Database dialect boundary: SQLite for local development, PostgreSQL for Azure.

Store code is written against the SQLite dialect it was built and tested on. For PostgreSQL this
module translates the small, closed set of SQLite constructs the code uses and installs
SQLite-compatible helper functions in the target schema.

Write transactions on PostgreSQL take one global advisory transaction lock. That keeps the SQLite
``BEGIN IMMEDIATE`` single-writer semantics that the hash-linked audit chain and the
read-modify-write document updates depend on. MarketBell's write volume is low, so this is
deliberately simple rather than fine-grained.
"""
import json
import os
import re
import sqlite3
import threading
import time
from decimal import Decimal

WRITE_LOCK_KEY = 724100001
# Errors raised by immutability triggers keep the same exception type on both engines.
IntegrityError = sqlite3.IntegrityError
OperationalError = sqlite3.OperationalError
_CODES = ('IMMUTABLE_AUDIT', 'IMMUTABLE_HISTORY', 'IMMUTABLE_EVIDENCE')


def database_url():
    return os.environ.get('MARKETBELL_DATABASE_URL', '').strip()


class Row:
    """sqlite3.Row-compatible: index by position or column name; dict(row) works."""
    __slots__ = ('_keys', '_values', '_index')

    def __init__(self, keys, values):
        self._keys = keys
        self._values = tuple(_plain(v) for v in values)
        self._index = {k: i for i, k in enumerate(keys)}

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return self._values[key]
        return self._values[self._index[key]]

    def keys(self):
        return list(self._keys)

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __repr__(self):
        return 'Row(' + repr(dict(zip(self._keys, self._values))) + ')'


def _plain(value):
    # PostgreSQL numeric aggregates arrive as Decimal; the code and JSON responses expect int/float.
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


_DQ = re.compile(r'"([^"]*)"')
_COALESCE_JSON = re.compile(r"COALESCE\((json_extract\([^()]*\)),\s*([01])\)\s*=\s*([01])\b")
_JSON_CMP = re.compile(r"(json_extract\([^()]*\))\s*=\s*([01])\b")
_INSERT_IGNORE = re.compile(r'^\s*INSERT\s+OR\s+IGNORE\s+INTO', re.I)


def translate(sql, has_params):
    """Translate the SQLite constructs used by MarketBell into PostgreSQL."""
    s = _DQ.sub(lambda m: "'" + m.group(1).replace("'", "''") + "'", sql)
    s = _COALESCE_JSON.sub(r"COALESCE(\1,'\2')='\3'", s)
    s = _JSON_CMP.sub(r"\1='\2'", s)
    s = s.replace('max(attempts-1,0)', 'GREATEST(attempts-1,0)')
    if _INSERT_IGNORE.match(s):
        s = _INSERT_IGNORE.sub('INSERT INTO', s, count=1).rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
    if has_params:
        s = s.replace('%', '%%').replace('?', '%s')
    return s


PG_FUNCTIONS = r'''
CREATE OR REPLACE FUNCTION json_extract(doc text, path text) RETURNS text
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $f$
DECLARE v jsonb;
BEGIN
  IF doc IS NULL THEN RETURN NULL; END IF;
  v := doc::jsonb #> string_to_array(substr(path, 3), '.');
  IF v IS NULL OR jsonb_typeof(v) = 'null' THEN RETURN NULL; END IF;
  IF jsonb_typeof(v) = 'boolean' THEN RETURN CASE WHEN v = 'true'::jsonb THEN '1' ELSE '0' END; END IF;
  IF jsonb_typeof(v) = 'string' THEN RETURN v #>> '{}'; END IF;
  RETURN v::text;
END $f$;
CREATE OR REPLACE FUNCTION mb_bool_sum(state bigint, v boolean) RETURNS bigint
LANGUAGE sql IMMUTABLE AS $f$ SELECT state + CASE WHEN v THEN 1 ELSE 0 END $f$;
CREATE OR REPLACE AGGREGATE sum(boolean) (SFUNC = mb_bool_sum, STYPE = bigint, INITCOND = '0');
CREATE OR REPLACE FUNCTION julianday(t text) RETURNS double precision
LANGUAGE sql STABLE AS $f$ SELECT extract(epoch FROM t::timestamptz) / 86400.0 + 2440587.5 $f$;
CREATE OR REPLACE FUNCTION datetime(t text, modifier text) RETURNS text
LANGUAGE sql STABLE AS $f$ SELECT to_char((t::timestamptz AT TIME ZONE 'UTC') + modifier::interval, 'YYYY-MM-DD HH24:MI:SS') $f$;
CREATE OR REPLACE FUNCTION strftime(fmt text, t text) RETURNS text
LANGUAGE plpgsql IMMUTABLE AS $f$
DECLARE d timestamp; w int;
BEGIN
  IF fmt <> '%Y-W%W' THEN RAISE EXCEPTION 'STRFTIME_FORMAT_UNSUPPORTED'; END IF;
  d := t::timestamp;
  -- SQLite %W: week of year, Monday first; days before the first Monday are week 00.
  w := (extract(doy FROM d)::int - 1 + 7 - ((extract(dow FROM d)::int + 6) % 7)) / 7;
  RETURN to_char(d, 'YYYY') || '-W' || lpad(w::text, 2, '0');
END $f$;
CREATE OR REPLACE FUNCTION mb_immutable() RETURNS trigger
LANGUAGE plpgsql AS $f$ BEGIN RAISE EXCEPTION '%', TG_ARGV[0]; END $f$;
'''


class Cursor:
    def __init__(self, cursor, rows=None):
        self._cursor = cursor
        self.rowcount = cursor.rowcount if cursor is not None else -1
        if cursor is not None and cursor.description is not None:
            keys = [d.name for d in cursor.description]
            self._rows = [Row(keys, r) for r in cursor.fetchall()]
        else:
            self._rows = rows or []
        self._at = 0

    def fetchone(self):
        if self._at >= len(self._rows):
            return None
        self._at += 1
        return self._rows[self._at - 1]

    def fetchall(self):
        rows, self._at = self._rows[self._at:], len(self._rows)
        return rows

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row


class PgConnection:
    """The subset of sqlite3.Connection that Store uses, over a pooled psycopg connection."""

    def __init__(self, pool, raw):
        self._pool, self._raw, self._closed = pool, raw, False
        self.in_transaction = False

    def execute(self, sql, params=()):
        import psycopg
        stripped = sql.strip().rstrip(';').upper()
        if stripped.startswith('PRAGMA'):
            return Cursor(None)
        params = tuple(params) if params else ()
        try:
            if stripped == 'BEGIN READ COMMITTED':
                self._raw.execute('BEGIN')
                self.in_transaction = True
                return Cursor(None)
            if stripped == 'BEGIN IMMEDIATE':
                self._raw.execute('BEGIN')
                self.in_transaction = True
                self._raw.execute('SELECT pg_advisory_xact_lock(%s)', (WRITE_LOCK_KEY,))
                return Cursor(None)
            if stripped == 'BEGIN':
                # A consistent read snapshot; matches SQLite's deferred read transaction.
                self._raw.execute('BEGIN ISOLATION LEVEL REPEATABLE READ')
                self.in_transaction = True
                return Cursor(None)
            cursor = self._raw.execute(translate(sql, bool(params)), params or None)
        except psycopg.errors.RaiseException as error:
            message = str(error).split('\n')[0]
            if message in _CODES:
                raise IntegrityError(message) from None
            raise
        except psycopg.errors.IntegrityError as error:
            raise IntegrityError(str(error).split('\n')[0]) from None
        except psycopg.OperationalError as error:
            if self._raw.closed or getattr(self._raw, 'broken', False):
                self._pool.discard(self._raw)
                self._closed = True
                raise OperationalError('DATABASE_UNAVAILABLE') from error
            # Serialization/deadlock/lock timeouts are transient and the connection is still usable.
            raise OperationalError('DATABASE_BUSY') from error
        return Cursor(cursor)

    def executescript(self, script):
        # Only used for DDL written for this module; statements are separated by blank-line-safe ';\n'.
        for statement in [s.strip() for s in script.split(';\n') if s.strip()]:
            self._raw.execute(statement)

    def commit(self):
        import psycopg
        if self.in_transaction:
            try:
                self._raw.execute('COMMIT')
            except psycopg.OperationalError as error:
                self.in_transaction = False
                self._pool.discard(self._raw)
                self._closed = True
                raise OperationalError('DATABASE_UNAVAILABLE') from error
            self.in_transaction = False

    def rollback(self):
        if self.in_transaction and not self._closed:
            try:
                self._raw.execute('ROLLBACK')
            except Exception:
                self._pool.discard(self._raw)
                self._closed = True
            finally:
                self.in_transaction = False

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self.in_transaction:
                self.rollback()
            self._pool.release(self._raw)
        except Exception:
            self._pool.discard(self._raw)

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        try:
            if kind is None:
                self.commit()
            else:
                self.rollback()
        except Exception:
            pass
        finally:
            self.close()
        return False


def managed_identity_token(resource='https://ossrdbms-aad.database.windows.net', _cache={}):
    """Azure Container Apps managed identity token, without an SDK dependency.

    Uses the platform IDENTITY_ENDPOINT / IDENTITY_HEADER contract. Tokens are cached until five
    minutes before expiry. Raises ValueError with a stable code; never logs the token.
    """
    cached = _cache.get(resource)
    if cached and cached[1] - 300 > time.time():
        return cached[0]
    import requests
    endpoint, header = os.environ.get('IDENTITY_ENDPOINT'), os.environ.get('IDENTITY_HEADER')
    if not endpoint or not header:
        raise ValueError('MANAGED_IDENTITY_UNAVAILABLE')
    params = {'resource': resource, 'api-version': '2019-08-01'}
    if os.environ.get('AZURE_CLIENT_ID'):
        params['client_id'] = os.environ['AZURE_CLIENT_ID']
    try:
        response = requests.get(endpoint, params=params, headers={'X-IDENTITY-HEADER': header}, timeout=(5, 10))
        body = response.json()
        token, expires = body['access_token'], float(body.get('expires_on') or time.time() + 3000)
    except Exception:
        raise ValueError('MANAGED_IDENTITY_TOKEN_FAILED') from None
    _cache[resource] = (token, expires)
    return token


class PgPool:
    """Small thread-safe pool. Broken connections are discarded and recreated on demand."""

    def __init__(self, url, schema=None, size=None):
        self.url, self.schema = url, schema
        self.size = int(size or os.environ.get('MARKETBELL_DB_POOL', '8'))
        self._idle, self._lock = [], threading.Lock()
        self._open = 0
        self._available = threading.Semaphore(self.size)

    def _connect(self):
        import psycopg
        kwargs = {'autocommit': True, 'connect_timeout': int(os.environ.get('MARKETBELL_DB_CONNECT_TIMEOUT', '10'))}
        if os.environ.get('MARKETBELL_DATABASE_AUTH') == 'managed-identity':
            kwargs['password'] = managed_identity_token()
        try:
            raw = psycopg.connect(self.url, **kwargs)
        except psycopg.OperationalError:
            raise OperationalError('DATABASE_UNAVAILABLE') from None
        if self.schema:
            raw.execute('SET search_path TO ' + self.schema + ', public')
        raw.execute("SET TIME ZONE 'UTC'")
        return raw

    def acquire(self):
        if not self._available.acquire(timeout=30):
            raise OperationalError('DATABASE_POOL_EXHAUSTED')
        try:
            with self._lock:
                raw = self._idle.pop() if self._idle else None
            if raw is not None and raw.closed:
                raw = None
            return raw or self._connect()
        except Exception:
            self._available.release()
            raise

    def release(self, raw):
        try:
            if raw.closed or raw.info.transaction_status != 0:  # 0 == IDLE
                raw.close()
            else:
                with self._lock:
                    self._idle.append(raw)
        finally:
            self._available.release()

    def discard(self, raw):
        try:
            raw.close()
        except Exception:
            pass
        finally:
            self._available.release()

    def connection(self):
        return PgConnection(self, self.acquire())

    def close(self):
        with self._lock:
            idle, self._idle = self._idle, []
        for raw in idle:
            try:
                raw.close()
            except Exception:
                pass


_POOLS = {}
_POOLS_LOCK = threading.Lock()


def pool(url, schema=None):
    key = (url, schema)
    with _POOLS_LOCK:
        if key not in _POOLS:
            _POOLS[key] = PgPool(url, schema)
        return _POOLS[key]


def redact_url(url):
    return re.sub(r'//([^:/@]+):[^@]*@', r'//\1:***@', url or '')
