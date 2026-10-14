"""Only root-approved measured features enter immutable refinement-v2 Rule model."""
from feature_contract import *
from inference_v3 import load_model,gen_batch,admission_record
from budget import reserve,complete,audit
import torch,time,subprocess,os,traceback
OUT=TOP/'rule_link'
def prepare():
 approved=read(TOP/'review/adopted_new_features.json');actual=[x for x in approved if x['source_kind']=='approved_actual_capture'];items=[];methods=set()
 for x in actual:
  method=x['checks']['feature']['plan']['expression']['op']
  if method not in methods and len(items)<4:items.append(x);methods.add(method)
 for x in actual:
  if x not in items and len(items)<4:items.append(x)
 records=[];c=load_capture(TOP/'fresh_actual/prepared');ref=load_capture(PARENT/'captures/reference/prepared')
 for x in items:
  assert x['direct_meaning_approved'] and x['checks']['measurement_completed']and x['checks']['independent'];f=x['checks']['feature'];b=bundle(f,c);bp=OUT/'bundles/application'/(x['key']+'.json');write(bp,b)
  rf=deepcopy(f);rf['plan']['source_version']=ref['inventory']['source_version'];rf['plan']['schema_version']=ref['inventory']['schema_version'];m=Engine(ref['inventory'],ref['policy'],ref['capture']).measure(rf['plan'],retain_values=True);vals=m.pop('_values');m.pop('_keys');assert m['measurement_status']=='measured';rf.update(source_version=ref['inventory']['source_version'],schema_version=ref['inventory']['schema_version'],definition_sha256=m['definition_sha256'],measurement=m);rb=bundle(rf,ref);rp=OUT/'bundles/reference'/(x['key']+'.json');write(rp,rb)
  verify_candidates(ref,[{'feature':rf,'values':[{'decimal':str(v)}if isinstance(v,Decimal)else v for v in vals]}])
  for request in ['confirmed_HR_review','observational_diagnostic','numeric_reference_comparison']:
   i=model_input(b,request,rb);r=record(i,x['key']+'_'+request,'v4_new_feature_rule_development','same_company_new_capture_development',{'application_bundle':pin(bp),'reference_bundle':pin(rp),'feature_review':pin(TOP/'review/adopted_new_features.json')});r['values']=x['checks']['values'];r['feature_key']=x['key'];admission_record(r);records.append(r)
 jl(OUT/'inputs.jsonl',records);write(OUT/'freeze.json',{'inputs':pin(OUT/'inputs.jsonl'),'features':len(items),'responses':len(records),'feature_gate':pin(TOP/'review/adopted_new_features.json'),'fixed_Rule_adapter':pin(PARENT/'training/adapter_final/adapter_model.safetensors'),'source_scope':capture_proof(str(c['path'].resolve())),'reference_scope':capture_proof(str(ref['path'].resolve()))});print(json.dumps({'features':len(items),'planned_responses':len(records)}))
def run():
 records=rows(OUT/'inputs.jsonl');freeze=read(OUT/'freeze.json');assert len(records)<=12
 if not records:write(OUT/'results.json',{'completed':True,'responses':[],'reason':'No approved actual new feature; Rule gate not relaxed.'});return
 gpu=subprocess.check_output(['nvidia-smi','--query-gpu=index,name,memory.used,memory.total,utilization.gpu','--format=csv,noheader'],text=True);write(OUT/'GPU_preflight.json',{'visible':gpu,'CUDA_VISIBLE_DEVICES':os.getenv('CUDA_VISIBLE_DEVICES')});torch.manual_seed(42);adapter=str((PARENT/'training/adapter_final').relative_to(ROOT));model,tok=load_model(adapter);results=[];start=time.monotonic()
 for r in records:
  admission_record(r);key=reserve('rule_link',[r['id']],adapter,512);g=gen_batch(model,tok,[r['messages'][:2]],512)[0];item={'id':r['id'],'feature_key':r['feature_key'],'input_sha':digest(r['input']),**g,**assess(r['input'],g['raw_output'],r['values']),'direct_meaning':'pending_root_review'};write(OUT/'responses'/(r['id']+'.json'),item);results.append(item);complete(key,'rule_link',[item]);print(json.dumps({'id':r['id'],'code_pass':item['validation']['passed']}),flush=True)
 assert pin(PARENT/'training/adapter_final/adapter_model.safetensors')==freeze['fixed_Rule_adapter'];write(OUT/'results.json',{'completed':True,'responses':results,'seconds':time.monotonic()-start,'Rule_retrained':False,'generation_budget':audit()})
if __name__=='__main__':
 import sys
 try:prepare()if sys.argv[1]=='prepare'else run()
 except Exception as e:write(OUT/('failure_'+sys.argv[1]+'.json'),{'error':str(e),'traceback':traceback.format_exc()});raise
