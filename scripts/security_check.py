"""Read-only secret leak check; prints only counts, never credential values or matching lines."""
from pathlib import Path
import subprocess
from backend.config import ROOT
from backend.vendor.provider import NemotronProvider, ProviderError
from backend.market import massive_key

def main():
    values=[]
    try:values.append(NemotronProvider().key)
    except ProviderError:pass
    key=massive_key()
    if key:values.append(key)
    paths=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\x00')
    matches=[]
    for name in filter(None,paths):
        path=ROOT/name
        if path.is_file():
            raw=path.read_bytes()
            if any(value.encode() in raw for value in values):matches.append(name)
    if matches:raise SystemExit('SECRET_LEAK_DETECTED; release blocked')
    print(f'Secret scan passed: {len(list(filter(None,paths)))} tracked files, {len(values)} known access values checked; no values displayed.')
if __name__=='__main__':main()
