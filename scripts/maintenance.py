"""Local-only operations; no maintenance endpoints are exposed to judges."""
import argparse
import json
from pathlib import Path
from backend.store import Store
from backend.config import DATA

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['verify','backup'])
    parser.add_argument('--output')
    args=parser.parse_args()
    store=Store()
    if args.command=='backup':
        path=Path(args.output) if args.output else DATA/'backup.sqlite3'
        store.backup(path)
        print('Backup written; verify before restoring.')
    print(json.dumps(store.verify()))
if __name__=='__main__':main()
