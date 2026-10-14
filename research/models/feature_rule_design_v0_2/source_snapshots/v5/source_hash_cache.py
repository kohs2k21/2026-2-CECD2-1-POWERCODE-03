"""Process-local SHA reuse; changed inode/size/mtime/ctime forces actual rehash.

No source admission conditions are bypassed and no source files are modified.
"""
from pathlib import Path
import generalized_source_policy_v21 as source_policy
ORIGINAL=source_policy.digest
CACHE={}
def checked_sha(path):
 p=Path(path).resolve();s=p.stat();key=(str(p),s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
 if key not in CACHE:CACHE[key]=ORIGINAL(p)
 return CACHE[key]
def install():source_policy.digest=checked_sha
