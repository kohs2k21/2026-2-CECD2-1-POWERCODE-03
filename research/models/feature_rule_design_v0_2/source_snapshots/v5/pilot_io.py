from pathlib import Path
import json,hashlib
from datetime import datetime
from zoneinfo import ZoneInfo
import os
ROOT=Path.cwd().resolve();TOP=Path(__file__).resolve().parent.parent;RUN=Path(os.environ.get("REFINEMENT_CAPTURE_RUN",str(TOP)))
V1=ROOT/'data/eda/rule_sft_pilot_v1/20261012T203550+0900'
def read(p):return json.loads(Path(p).read_text())
def digest(obj):return hashlib.sha256(json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb')as f:
  for b in iter(lambda:f.read(4194304),b''):h.update(b)
 return h.hexdigest()
def pin(p):return {'path':str(Path(p).resolve().relative_to(ROOT)),'sha256':sha(p)}
def write(p,obj):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 if p.exists():
  assert digest(read(p))==digest(obj),('existing_v4_artifact_differs',str(p));return
 with p.open('x')as f:json.dump(obj,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
def ledger(kind,payload):
 d=RUN/'ledger';n=len(list(d.glob('*.json')))+1
 write(d/f'{n:04d}_{kind}.json',{'number':n,'kind':kind,'at_kst':datetime.now(ZoneInfo('Asia/Seoul')).isoformat(),**payload})
