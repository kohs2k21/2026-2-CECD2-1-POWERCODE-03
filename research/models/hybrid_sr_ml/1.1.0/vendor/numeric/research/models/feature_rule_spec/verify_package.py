"""Verify local package bytes before use; no Model/GPU/network or installation."""
from pathlib import Path
import json,hashlib,sys
P=Path(__file__).resolve().parent
a=json.loads((P/'artifact_manifest.json').read_text());bad=[]
for x in a['files']:
 f=P/x['path']
 if not f.is_file()or hashlib.sha256(f.read_bytes()).hexdigest()!=x['sha256']:bad.append(x['path'])
print(json.dumps({'passed':not bad,'files_checked':len(a['files']),'mismatches':bad,'Model_calls':0}))
raise SystemExit(bool(bad))
