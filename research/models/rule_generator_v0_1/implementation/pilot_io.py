"""I/O for the portable, frozen research demo."""
from pathlib import Path
import json,hashlib
PKG=Path(__file__).resolve().parent.parent
ROOT=PKG.parents[2]
EVAL=ROOT/'research/evaluation/rule_generator_v0_1'
def read(p):return json.loads(Path(p).read_text())
def digest(obj):return hashlib.sha256(json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb')as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
