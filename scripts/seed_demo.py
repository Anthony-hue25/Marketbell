"""Prepare the public, read-only illustrative dataset once; no model calls."""
from backend.store import Store
from backend.demo_history import seed
if __name__=='__main__':
 print(seed(Store(),'illustrative-shared-v3'))
