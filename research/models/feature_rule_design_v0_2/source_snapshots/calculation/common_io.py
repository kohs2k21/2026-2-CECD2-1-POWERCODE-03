"""Project-local, append-only experiment utilities."""
from pathlib import Path
import hashlib,json,sys
ROOT=Path.cwd().resolve()
sys.path.insert(0,str(ROOT))
P25=Path(__file__).resolve().parent.parent
P24=ROOT/'data/eda/schema_generalization_v24/20261012_method_bound_reason'
P23=ROOT/'data/eda/schema_generalization_v23/20261012_feature_repair_expansion'
def read(path):return json.loads(Path(path).read_text())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
def ref(path):
    path=Path(path).resolve();path.relative_to(ROOT)
    return {'path':str(path.relative_to(ROOT)),'sha256':sha(path)}
