"""Live representative1feature+3Rule bounded reproduction; no answer correction."""
from feature_contract import *
from prepare_features import load_context
from inference_v3 import load_model,gen_batch,admission_record
from budget import reserve,complete,audit
import torch,gc,subprocess,os,argparse
def run(case,out):
 out=Path(out);out.mkdir(parents=True,exist_ok=False);records={r['id']:r for r in rows(TOP/'data_r04/eval_inputs.jsonl')};r=records[case];c=load_context((ROOT/r['source_context']['path']).parent);admission(c,'model_input');write(out/'GPU_preflight.json',{'query':subprocess.check_output(['nvidia-smi','--query-gpu=index,name,memory.used,memory.total,utilization.gpu','--format=csv,noheader'],text=True),'CUDA_VISIBLE_DEVICES':os.getenv('CUDA_VISIBLE_DEVICES')});torch.manual_seed(42);adapter=str((TOP/'training/adapter_final').relative_to(ROOT));model,tok=load_model(adapter);key=reserve('representative_replay',[case+'_live_feature'],adapter,2048);g=gen_batch(model,tok,[r['messages']],2048)[0];g['id']=case+'_live_feature';complete(key,'representative_replay',[g]);write(out/'feature_raw.json',g)
 obj=json.loads(g['raw_output']);checks=evaluate_candidates(c,obj);approved=[x for x in read(TOP/'review/adopted_new_features.json')if x['source_case']==case];selected=[]
 for check in checks['candidates']:
  old=next((x for x in approved if digest(x['checks']['candidate'])==digest(check['candidate'])),None)
  if old and check['measurement_completed']and check['independent']:selected.append({'key':old['key'],'checks':check,'direct_meaning_review_reused_exact_proposal':old['review']})
 write(out/'feature_checks.json',{'checks':checks,'selected':selected,'unmatched_proposal_meaning_held':len(selected)!=len(checks['candidates'])})
 del model;gc.collect();torch.cuda.empty_cache();rs=[x for x in rows(TOP/'rule_link/inputs.jsonl')if x['feature_key']in [s['key']for s in selected]][:3];outputs=[]
 if rs:
  ruleadapter=str((PARENT/'training/adapter_final').relative_to(ROOT));model,tok=load_model(ruleadapter)
  for x in rs:
   admission_record(x);key=reserve('representative_replay',[x['id']+'_live'],ruleadapter,512);z=gen_batch(model,tok,[x['messages'][:2]],512)[0];z['id']=x['id']+'_live';complete(key,'representative_replay',[z]);result={'input':x['input'],**z,**assess(x['input'],z['raw_output'],x['values']),'direct_meaning':'pending_root_review'};write(out/(x['id']+'.json'),result);outputs.append(result)
 write(out/'result.json',{'case':case,'schema_profile_input':r['input'],'feature_generation':g,'feature_checks':checks,'exact_previously_reviewed_features':selected,'Rule_generation':outputs,'execution_mode':'actual live saved-feature-adapter inference -> guarded measurement and independent replay -> exact-proposal meaning gate -> fixed Rule adapter inference -> validation JSON','generation_budget':audit(),'new_SFT':False,'anomaly_detection':False});print(json.dumps({'case':case,'features':len(selected),'Rule_responses':len(outputs),'output':str(out)}))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--case',required=True);p.add_argument('--output-dir',required=True);a=p.parse_args();run(a.case,a.output_dir)
