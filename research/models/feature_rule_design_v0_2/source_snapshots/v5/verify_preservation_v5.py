"""Read-only hash audit of existing immutable v4 and earlier research outputs."""
from pilot_io import *
from collections import defaultdict

def collect(o):
 found={}
 if isinstance(o,dict):
  if isinstance(o.get('path'),str)and isinstance(o.get('sha256'),str)and len(o['sha256'])==64:
   p=ROOT/o['path']
   found[o['path']]=o['sha256']
  for v in o.values():found.update(collect(v))
 elif isinstance(o,list):
  for v in o:found.update(collect(v))
 return found

def run():
 before=read(TOP/'preservation_before.json');old=ROOT/before['prior_run'];pins=collect(before['prior_artifacts']);pins.update(collect(read(old/'preservation_before.json')));pins[str((old/'artifact_manifest.json').relative_to(ROOT))]=before['prior_manifest_sha256']
 failed=[]
 for i,(p,expected)in enumerate(pins.items()):
  if sha(ROOT/p)!=expected:failed.append(p)
 assert not failed,failed
 write(TOP/'preservation_after_r02.json',{'passed':True,'existing_files_checked':len(pins),'changed_existing_files':failed,'checked_sha256':pins,'v4_and_preceding_manifest_pins_preserved':True,'no_existing_checkpoint_dataset_output_removed':True});print(json.dumps({'prior_files_SHA_unchanged':len(pins)}))
if __name__=='__main__':run()
