"""Review staged issue80 research paths, syntax, models, and obvious secrets."""
import ast
import json
from pathlib import Path
import re
import subprocess
import sys
import nbformat
from inspect_latest import verified_entries

ROOT=Path(__file__).resolve().parent
REPO=ROOT.parents[1]
TOKEN=re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')


def main():
    paths=subprocess.check_output(['git','diff','--cached','--name-only','--diff-filter=ACM','-z'],cwd=REPO).decode('utf-8').split('\0')
    paths=[p for p in paths if p]
    if not paths:
        raise ValueError('Stage only the reviewed research changes first')
    approved={e['path'] for e in verified_entries()}
    counts={};total=0;notebooks=0
    for rel in paths:
        p=REPO/rel
        if not(rel=='research/README.md'or rel.startswith('research/anomaly_detection/')):
            raise ValueError('Non-research path staged: '+rel)
        if any(s in {'__pycache__','.venv','data','datasets','feature_cache'}or 'node_modules'in s for s in p.parts):
            raise ValueError('Dependency/dataset/cache path staged: '+rel)
        if p.suffix.lower()in{'.csv','.tsv','.sql','.npz','.ndjson','.db','.sqlite','.env'}or p.name.startswith('.env'):
            raise ValueError('Dataset or environment file staged: '+rel)
        if p.suffix=='.joblib'and p.relative_to(ROOT).as_posix()not in approved:
            raise ValueError('Unapproved model/cache staged: '+rel)
        if p.stat().st_size>=90*1024*1024:
            raise ValueError('Oversized Git blob: '+rel)
        if p.suffix in{'.py','.json','.md','.txt','.ipynb','.mjs'}:
            text=p.read_text(encoding='utf-8-sig')
            if TOKEN.search(text):
                raise ValueError('Potential credential found; inspect locally: '+rel)
            if p.suffix=='.py':ast.parse(text,filename=rel)
            if p.suffix=='.json':json.loads(text)
            if p.suffix=='.ipynb':
                nb=nbformat.reads(text,as_version=4);nbformat.validate(nb);notebooks+=1
                if p.parent==ROOT/'notebooks'and any(o.get('output_type')=='error'for c in nb.cells for o in c.get('outputs',[])):
                    raise ValueError('Latest notebook has an error: '+rel)
        counts[p.suffix]=counts.get(p.suffix,0)+1;total+=p.stat().st_size
    result={'staged_files':len(paths),'MiB':round(total/2**20,3),'types':counts,
            'notebooks_validated':notebooks,'four_latest_model_hashes_verified':True,
            'scope':'research only','new_datasets_or_caches':False,'obvious_secret_scan_passed':True}
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
