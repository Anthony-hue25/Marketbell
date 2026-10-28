"""Restricted diagnostics and structured operational events.

emit() writes one JSON object per line to stdout. On Azure Container Apps stdout is collected by
Log Analytics (ContainerAppConsoleLogs_CL), where alert rules query these events. Events carry
operational facts only: no secrets, tokens, request bodies, portfolio contents or news text.
"""
import json,os,sys,threading,time
from datetime import datetime,timezone

_lock=threading.Lock()
_FORBIDDEN=('token','secret','password','key','authorization','cookie','text','body','payload','portfolio','holdings')

def emit(event,**fields):
 if os.environ.get('MARKETBELL_STRUCTURED_LOGS','1' if os.environ.get('MARKETBELL_ENV') in ('production','staging') else '0')!='1':return
 clean={}
 for k,v in fields.items():
  if any(f in k.lower() for f in _FORBIDDEN):continue  # Defence in depth: never log sensitive field names.
  clean[k]=v if isinstance(v,(int,float,bool,type(None))) else v if isinstance(v,(dict,list)) and len(json.dumps(v,default=str))<2000 else str(v)[:300]
 record={'at':datetime.now(timezone.utc).isoformat(),'service':'marketbell','role':os.environ.get('MARKETBELL_ROLE','all'),'event':event,**clean}
 line=json.dumps(record,default=str,separators=(',',':'))
 with _lock:
  try:sys.stdout.write(line+'\n');sys.stdout.flush()
  except (OSError,ValueError):pass

def resource_usage():
 result={'pid':os.getpid(),'cpu_seconds':time.process_time()}
 if os.name=='nt':
  import ctypes
  from ctypes import wintypes
  class Counters(ctypes.Structure):
   _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
  values=Counters();values.cb=ctypes.sizeof(values)
  kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.GetCurrentProcess.restype=wintypes.HANDLE
  api=ctypes.WinDLL('psapi',use_last_error=True);api.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
  if api.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(values),values.cb):result.update(rss_bytes=values.WorkingSetSize,peak_rss_bytes=values.PeakWorkingSetSize)
 else:
  import resource
  result['peak_rss_kib']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
 return result
