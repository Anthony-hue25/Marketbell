"""Explicit provider selection. Never fall back from Nebius to mock."""
import json
import os
from pathlib import Path
from typing import Protocol
import requests
from dotenv import dotenv_values
from ..config import ROOT

class ProviderError(ValueError):
    """str(error) stays a stable code (e.g. NEBIUS_HTTP_400); .detail carries the provider's redacted error text."""
    def __init__(self, code, detail=None):
        super().__init__(code); self.detail = detail


def _redact(text, key):
    import re
    text = str(text or '')
    if key: text = text.replace(key, '[REDACTED]')
    text = re.sub(r'(?i)bearer\s+[A-Za-z0-9._\-]+', 'Bearer [REDACTED]', text)
    return re.sub(r'\b(sk|key|api)[-_][A-Za-z0-9._\-]{12,}', '[REDACTED]', text)[:800]


def _request_shape(body):
    """Content-free description of a request, safe to log."""
    msgs = body.get('messages') or []
    return dict(model=body.get('model'), messages=len(msgs), roles=[m.get('role') for m in msgs],
                assistant_tool_calls=sum(1 for m in msgs if m.get('tool_calls')), tools_field='tools' in body,
                tools=len(body.get('tools') or []), chars=len(json.dumps(msgs)))


def _log_provider_error(code, detail, shape):
    try:
        from datetime import datetime, timezone
        log = ROOT/'logs'/'provider_errors.log'; log.parent.mkdir(exist_ok=True)
        with log.open('a', encoding='utf-8') as f:
            f.write(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(), code=code, detail=detail, request=shape)) + '\n')
    except OSError:
        pass

DEFAULT_ENDPOINT = 'https://api.tokenfactory.us-central1.nebius.com/v1/chat/completions'

def endpoint():
    """Fixed Nebius endpoint in production-like environments. A local fault-injection endpoint is
    accepted only in development/test, so configuration cannot redirect production inference."""
    from ..config import PRODUCTION
    override = os.environ.get('MARKETBELL_TEST_PROVIDER_URL')
    if override and not PRODUCTION and override.startswith(('http://127.0.0.1', 'http://localhost', 'http://mock-nebius')):
        return override
    return DEFAULT_ENDPOINT

class ResearchModelProvider(Protocol):
    name: str
    def complete(self, messages: list, tools: list) -> dict: ...

class NemotronProvider:
    name = 'NEBIUS_NEMOTRON'
    def __init__(self):
        # Optional explicit read-only credential location; never copied to the repo.
        path = Path(os.environ.get('NEBIUS_ENV_FILE',str(ROOT/'.env')))
        values = dotenv_values(path) if path.exists() else {}
        self.key = os.environ.get('NEBIUS_API_KEY') or values.get('NEBIUS_API_KEY')
        from ..config import developer_path
        key_file = Path(values['NEBIUS_KEY_FILE']) if values.get('NEBIUS_KEY_FILE') and not os.environ.get('NEBIUS_KEY_FILE') else developer_path('NEBIUS_KEY_FILE', 'C:/Users/antho/adaptive-universe-lab/NEBIUS API  KEY.txt')
        if not self.key and key_file and key_file.is_file():
            self.key = key_file.read_text(encoding='utf-8-sig').strip().strip(chr(34)).strip(chr(39))
            if '\n' in self.key or '\r' in self.key: raise ProviderError('KEY_FILE_MUST_CONTAIN_ONLY_KEY')
        self.model = os.environ.get('NEBIUS_MODEL') or values.get('NEBIUS_MODEL') or 'nvidia/nemotron-3-super-120b-a12b'
        if not self.key: raise ProviderError('NEBIUS_KEY_NOT_CONFIGURED')
        if not self.model.lower().startswith('nvidia/') or 'nemotron' not in self.model.lower():
            raise ProviderError('NVIDIA_NEMOTRON_MODEL_REQUIRED')

    def complete(self,messages,tools):
        try:
            # OpenAI-compatible APIs reject an empty "tools" array: omit the field when there are no tools.
            # Requests that carry tools (every V1 call) are byte-for-byte the same body as before.
            body={'model':self.model,'messages':messages,'max_tokens':4096,'temperature':0,'response_format':{'type':'json_object'}}
            if tools: body['tools']=tools
            response = requests.post(endpoint(),
                headers={'Authorization':'Bearer '+self.key},json=body,timeout=(10,90),allow_redirects=False)
        except requests.RequestException:
            raise ProviderError('NEBIUS_TRANSPORT_FAILURE') from None
        if response.status_code != 200:
            code='NEBIUS_HTTP_'+str(response.status_code)
            try:
                err=response.json().get('error') or response.json()
                detail=_redact(err.get('message') if isinstance(err,dict) else err,self.key)
            except (ValueError,AttributeError):
                detail=_redact(getattr(response,'text',''),self.key)
            pass  # No remote error details persisted.
            raise ProviderError(code)
        try:
            msg = response.json()['choices'][0]['message']
            # Only public decision output; no internal reasoning_content in audit.
            result = {k:v for k,v in msg.items() if k in ('role','content','tool_calls')}
            payload = response.json()
            result['api_request_id'] = payload.get('id')
            result['usage'] = payload.get('usage')
            return result
        except (ValueError,KeyError,IndexError,TypeError):
            raise ProviderError('NEBIUS_RESPONSE_INVALID') from None

