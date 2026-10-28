"""Regenerate requirements.container.lock (all PyPI file hashes per pinned version) from requirements.lock.

    python -m scripts.lock_hashes
Linux/CPython 3.12 container markers: Windows-only gunicorn exclusion and the <3.13 typing_extensions
marker are resolved for the container platform.
"""
import requests
from pathlib import Path

def main():
    pins = []
    for line in Path('requirements.lock').read_text().splitlines():
        line = line.split('#')[0].strip()
        if not line:
            continue
        spec = line.split(';')[0].strip()
        name, version = spec.split('==')
        pins.append((name, version))
    out = ['# Hash-pinned container dependencies (Linux, CPython 3.12). Generated from requirements.lock with all',
           '# published file hashes from PyPI for each pinned version. Install with: pip install --require-hashes -r requirements.container.lock',
           '# Regenerate after any change to requirements.lock: python -m scripts.lock_hashes']
    for name, version in pins:
        data = requests.get(f'https://pypi.org/pypi/{name}/{version}/json', timeout=30).json()
        hashes = sorted({f['digests']['sha256'] for f in data['urls']})
        out.append(f'{name}=={version} \\')
        out += [f'    --hash=sha256:{h} \\' for h in hashes[:-1]] + [f'    --hash=sha256:{hashes[-1]}']
    Path('requirements.container.lock').write_text('\n'.join(out) + '\n')
    print(len(pins), 'packages hashed')

if __name__ == '__main__':
    main()
