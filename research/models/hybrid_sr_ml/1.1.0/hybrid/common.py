from pathlib import Path
import csv,gzip,hashlib,json,os,subprocess,sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
WARNING='warning';NONWARNING='nonwarning';UNAVAILABLE='unavailable'
STATES={WARNING,NONWARNING,UNAVAILABLE}
def read(path):return json.loads(Path(path).read_text())
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    def default(v):
        if isinstance(v,np.generic):return v.item()
        if isinstance(v,Path):return str(v)
        if isinstance(v,np.ndarray):return v.tolist()
        raise TypeError(type(v).__name__)
    with path.open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False,default=default);f.write('\n')
def combine(states):
    states=list(states)
    if any(s not in STATES for s in states):raise ValueError('Unknown decision state')
    if WARNING in states:return WARNING
    return NONWARNING if states and all(s==NONWARNING for s in states) else UNAVAILABLE
def named(value):return UNAVAILABLE if value is None or int(value)<0 else WARNING if int(value)==1 else NONWARNING
def canonical(value):
    if value=='unassessable':return UNAVAILABLE
    if value not in STATES:raise ValueError('Unknown component state: '+str(value))
    return value
def csv_rows(path):
    p=Path(path);opener=gzip.open if p.suffix=='.gz' else open
    with opener(p,'rt',newline='') as f:yield from csv.DictReader(f)
def seal(folder):
    folder=Path(folder)
    write(folder/'manifest.json',dict(contract='hybrid-run-artifacts1.0',files=[dict(path=str(p.relative_to(folder)),sha256=sha(p)) for p in sorted(folder.rglob('*')) if p.is_file() and p.name!='manifest.json']))
def verify_bundle():
    m=read(ROOT/'bundle_manifest.json')
    for r in m['files']:
        p=(ROOT/r['path']).resolve()
        if not p.is_relative_to(ROOT.resolve()) or not p.is_file() or sha(p)!=r['sha256']:raise ValueError('Changed bundle artifact: '+r['path'])
    return m
def run_worker(command,output,pythonpath=None):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1'
    if pythonpath:env['PYTHONPATH']=str(pythonpath)
    with (output/'stdout.log').open('x') as out,(output/'stderr.log').open('x') as err:
        done=subprocess.run(command,env=env,stdin=subprocess.DEVNULL,stdout=out,stderr=err)
    return dict(command=command,exit_code=done.returncode,logs=str(output))
