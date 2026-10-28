"""Audit-only process helpers. Never targets production or unrelated applications."""
import os,sys
from pathlib import Path

def audit_python():return getattr(sys,'_base_executable',sys.executable)
def audit_env(env):
 site=Path(__file__).resolve().parents[1]/'.venv'/'Lib'/'site-packages'
 return {**env,'PYTHONPATH':str(site)+os.pathsep+str(Path(__file__).resolve().parents[1])}

