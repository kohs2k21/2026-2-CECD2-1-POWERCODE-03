#!/usr/bin/env python3
"""Use the project .venv. inspect/validate are CPU-only; run/repair use fixed v2."""
from pathlib import Path
import os,sys,json,argparse
PROJECT=Path(__file__).resolve().parents[3];PKG=Path(__file__).resolve().parent
os.chdir(PROJECT);sys.path.insert(0,str(PKG/'implementation'))
from demo_runtime import *

def main():
 p=argparse.ArgumentParser(description=__doc__);commands=p.add_subparsers(dest='command',required=True)
 commands.add_parser('inspect',help='Show packaged results without loading a GPU model')
 for name in ['run','validate']:
  c=commands.add_parser(name);g=c.add_mutually_exclusive_group(required=True);g.add_argument('--input-json');g.add_argument('--inputs')
  if name=='run':c.add_argument('--output-dir');c.add_argument('--max-repairs',type=int,default=0)
  else:c.add_argument('--response',required=True)
 c=commands.add_parser('repair');c.add_argument('--output-dir',required=True);c.add_argument('--max-repairs',type=int,default=16);c.add_argument('--review-file')
 c=commands.add_parser('review');c.add_argument('--output-dir',required=True);c.add_argument('--review-file',required=True)
 c=commands.add_parser('render');c.add_argument('--output-dir',required=True)
 args=p.parse_args();features,trusted,manifest=package_data()
 if args.command=='inspect':
  print(json.dumps(read(EVAL/'summary.json')if(EVAL/'summary.json').exists()else {'features':64,'inputs':32,'execution':'not yet completed'},ensure_ascii=False,indent=2));print('demo.html:',EVAL/'demo.html');return
 if args.command in ['run','validate']:
  values=rows(args.inputs)if args.inputs else [read(args.input_json)];contexts=[bind_input(x,features,trusted)for x in values]
  if len(contexts)>32 or len({x['id']for x in contexts})!=len(contexts):raise ValueError('At most32 distinct demo inputs')
  source_gate(manifest)
  if args.command=='validate':
   if len(contexts)!=1:raise ValueError('validate expects one input');
   checked=assess(contexts[0]['input'],read(args.response),features[contexts[0]['input']['feature']['id']])
   print(json.dumps(checked,ensure_ascii=False,indent=2))
   if not checked['passed']:raise SystemExit(2)
   return
  if not 0<=args.max_repairs<=16:raise ValueError('Max16 repair responses')
  out=Path(args.output_dir).resolve()if args.output_dir else PKG/'runs'/datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y%m%dT%H%M%S%z')
  out.relative_to(PKG)
  if(out/'model_outputs.jsonl').exists()or(out/'artifact_manifest.json').exists():raise FileExistsError('Choose a new output directory; previous results are preserved')
  out.mkdir(parents=True,exist_ok=True)
  for c in contexts:append(out/'run_inputs.jsonl',c)
  save(out/'public_output_contract.json',schema());save(out/'execution_manifest.json',{'model':manifest,'SYSTEM':system_text(),'catalog_SHA':sha(EVAL/'feature_catalog.json'),'validator_SHA':{n:sha(PKG/'implementation'/n)for n in ['v1_validator.py','teacher_semantics.py']},'initial_response_cap':32,'repair_response_cap':16,'maximum_repairs_per_input':1,'no_gold_read':True})
  gen=Generator(out,manifest)
  for off in range(0,len(contexts),2):gen.generate(contexts[off:off+2],'initial')
  if args.max_repairs:
   latest=latest_records(out);chosen=repair_selection(contexts,latest,out,args.max_repairs)
   for c in chosen:gen.generate([c],'repair',previous=latest,human={})
  gen.close()
 elif args.command=='repair':
  out=Path(args.output_dir).resolve();out.relative_to(PKG)
  if(out/'artifact_manifest.json').exists():raise ValueError('Completed package is sealed; use a new run')
  if not 0<=args.max_repairs<=16:raise ValueError('Max16 repair responses')
  if args.review_file:import_reviews(out,args.review_file)
  contexts=rows(out/'run_inputs.jsonl');latest=latest_records(out);chosen=repair_selection(contexts,latest,out,args.max_repairs);save(out/'repair_selection.json',{'selected':[c['id']for c in chosen],'maximum':16,'rule':'one per failed input;balanced public request/type groups; no gold criteria'})
  if chosen:
   gen=Generator(out,manifest);humans={sid:review_for(out,r)for sid,r in latest.items()}
   for c in chosen:gen.generate([c],'repair',previous=latest,human=humans)
   gen.close()
 elif args.command=='review':
  out=Path(args.output_dir).resolve();out.relative_to(PKG);import_reviews(out,args.review_file)
 else:out=Path(args.output_dir).resolve();out.relative_to(PKG)
 summary=collect(out)
 from render_demo import render
 render(out);print(json.dumps({'output_dir':str(out),'summary':summary,'HTML':str(out/'demo.html')},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
