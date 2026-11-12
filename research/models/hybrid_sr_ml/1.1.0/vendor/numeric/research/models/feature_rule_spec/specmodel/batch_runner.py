"""Official portable batch entry. Each source/request gets an actual Model pipeline, never a replay fallback."""
from pathlib import Path
import argparse,json,time,traceback,copy
from .io import *
from .dataset import Dataset
from .local_models import LocalModels
from .pipeline import run_pipeline
from .cli import replay

class ScopedBudget:
 def __init__(self,root,out,cap,shared,phase):
  self.root=root;self.cap=cap;self.local=Budget(out,4,shared,phase)
 def reserve(self,kind,call):
  p=self.root/'model_calls.jsonl';old=p.read_text().splitlines()if p.exists()else[]
  if len(old)>=self.cap:raise ModelFailure('Batch Model response cap exhausted; no new IDs or automatic expansion')
  receipt=self.local.reserve(kind,call)
  with p.open('a')as f:f.write(json.dumps({'batch_sequence':len(old)+1,**receipt})+'\n')
  return receipt

def resolve(suite,value):
 v=value['path']if isinstance(value,dict)else value;p=Path(v);p=p.resolve()if p.is_absolute()else(suite.parent/p).resolve()
 if isinstance(value,dict)and value.get('sha256')and sha(p)!=value['sha256']:raise InputFailure('Suite input receipt SHA changed: '+str(p))
 return p

def run(argv):
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--suite',type=Path,required=True);parser.add_argument('--base-path',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--max-model-calls',type=int,default=80);parser.add_argument('--shared-experiment-plan',type=Path);parser.add_argument('--phase',default='frozen_final_delivery');a=parser.parse_args(argv);out=None
 try:
  out=new_output(a.output);suite=a.suite.resolve();cfg=read(suite);cases=cfg['cases'];names=[x['id']for x in cases]
  if len(names)!=len(set(names))or any(not n or '/'in n or'\\'in n or n in ['.','..']for n in names):raise InputFailure('Suite case IDs must be unique local folder names')
  write(out/'suite_pin.json',{'source_sha':sha(suite),'cases':cases,'new_Model_generation':True,'no_past_response_or_template_fallback':True,'Model_cap':a.max_model_calls});cache={};model=LocalModels(a.base_path);results=[];grain_groups={}
  for case in cases:
   dest=out/'cases'/case['id'];new_output(dest);item={'id':case['id'],'actual_Model_generation':True,'runtime_human_answer_edits':False};start=time.time()
   try:
    src=resolve(suite,case['input_manifest']);req=resolve(suite,case['request']);plan=resolve(suite,case['rule_plan']);ref=resolve(suite,case['reference_manifest'])if case.get('reference_manifest')else None
    for file in [src,ref]:
     if file is not None and str(file)not in cache:cache[str(file)]=Dataset(file)
    ds=cache[str(src)];reference=cache[str(ref)]if ref else None;request=read(req);rp=read(plan);write(dest/'public_request.json',request);write(dest/'public_rule_plan.json',rp);write(dest/'profile.json',ds.profile());b=ScopedBudget(out,dest,a.max_model_calls,a.shared_experiment_plan,a.phase+'_'+case['id']);counts=run_pipeline(ds,reference,request,rp,model,b,dest,arm='selected');item['counts']=counts;item['Feature_first_pass']=read(dest/'feature_attempts.json')[0]['passed'];item['Rule_first_pass']=read(dest/'rule_attempts.json')[0]['passed']if(dest/'rule_attempts.json').exists()else None
    item['CPU_saved_output_replay']=replay(dest,src,req,ref);item['passed']=counts['Feature_status']=='feature_verified'and counts['Rule_status']=='rule_verified' or counts['Feature_status']=='no_candidate_review';item['positive_generation']=counts.get('positive_feature_generated',False)
    if counts['Feature_status']=='feature_verified':
     f=read(dest/'feature_bundle.json')['compilation']['compiled_feature'];v=read(dest/'feature_values.json');key=(f['source_version'],f['plan']['grain']['table'],tuple(f['plan']['grain']['keys']));g=grain_groups.setdefault(key,{'source_version':key[0],'table':key[1],'row_keys':list(key[2]),'all_source_row_keys':[[r[k]for k in key[2]]for r in ds.rows[key[1]]],'columns':[]});g['columns'].append({'feature_id':f['id'],'definition_sha':f['definition_sha'],'population':f['plan']['population'],'case':case['id'],'selected':{json.dumps(k,sort_keys=True):x for k,x in zip(v['source_row_keys'],v['values'])}})
   except BaseException as e:
    item.update(passed=False,software_failure={'type':type(e).__name__,'error':str(e),'traceback':traceback.format_exc()});write(dest/'failure.json',item['software_failure'])
   item['seconds']=time.time()-start;results.append(item);write(out/'validation_report.json',results);print('PIPELINE',case['id'],'pass',item.get('passed'),'firstFeature',item.get('Feature_first_pass'),'firstRule',item.get('Rule_first_pass'),flush=True)
  for (source,table,keys),g in grain_groups.items():
   rows=[]
   for key in g['all_source_row_keys']:
    k=json.dumps(key,sort_keys=True);rows.append({'source_row_key':key,'features':{c['feature_id']:({'state':'unavailable','value':None}if c['selected'][k]is None else{'state':'value','value':c['selected'][k]})if k in c['selected']else{'state':'not_selected'}for c in g['columns']}})
   write(out/'feature_values_by_grain'/(table+'_'+source[:12]+'.json'),{'source_version':source,'table':table,'row_keys':list(keys),'column_definitions':[{k:v for k,v in c.items()if k!='selected'}for c in g['columns']],'rows':rows,'not_selected_is_not_unavailable_or_false':True})
  write(out/'selected_models.json',read(Path(__file__).resolve().parents[1]/'model_manifest.json'));write(out/'counts.json',{'cases':len(results),'program_complete':sum(x.get('passed',False)for x in results),'positive_feature_generation':sum(x.get('positive_generation',False)for x in results),'holds_separate':sum((x.get('counts')or{}).get('Feature_status')=='no_candidate_review'for x in results),'new_Model_calls':len((out/'model_calls.jsonl').read_text().splitlines())if(out/'model_calls.jsonl').exists()else 0,'official_structured_facts_author':'compiler','not_operational_detection':True});
  from .reporting import render_batch
  render_batch(out)
  write(out/'manifest.json',{'files':[{'file':str(f.relative_to(out)),'sha256':sha(f)}for f in sorted(out.rglob('*'))if f.is_file()and f.name!='manifest.json'],'policy':'specification-delivery1.0','Model_calls_after_completion':0});return 0 if all(x.get('passed')for x in results)else 2
 except BaseException as e:
  if out:write(out/'failure.json',{'type':type(e).__name__,'error':str(e),'no_fallback':True})
  print(type(e).__name__,str(e),flush=True);return3=3;return return3


def replay_batch(folder,suite):
 folder=Path(folder);suite=Path(suite).resolve();m=read(folder/'manifest.json')
 for f in m['files']:
  if sha(folder/f['file'])!=f['sha256']:raise InputFailure('Batch artifact changed: '+f['file'])
 if sha(suite)!=read(folder/'suite_pin.json')['source_sha']:raise InputFailure('Batch suite differs from its execution pin')
 results=[]
 for c in read(suite)['cases']:
  src=resolve(suite,c['input_manifest']);req=resolve(suite,c['request']);ref=resolve(suite,c['reference_manifest'])if c.get('reference_manifest')else None
  results.append({'id':c['id'],'result':replay(folder/'cases'/c['id'],src,req,ref)})
 return {'batch_files_pinned':True,'cases':results,'new_Model_calls':0,'source_recomputed':True}
