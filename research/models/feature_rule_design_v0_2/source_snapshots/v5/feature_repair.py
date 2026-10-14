"""Optional post-evaluation development repair, one per failed input; no gold."""
from feature_contract import *
from prepare_features import load_context
from inference_v3 import load_model,gen_batch
from budget import reserve,complete,audit
import os,subprocess,torch,time
def prepare(ids):
 records={r['id']:r for r in rows(TOP/'data_r04/eval_inputs.jsonl')};prepared=[]
 for id in ids:
  r=records[id];old=read(TOP/'training/responses/feature_adapter'/(id+'.json'));assert old['checks']['JSON_contract'];failed=[x for x in old['checks']['candidates']if x['errors']];assert failed,'only failed candidates';c=load_context((ROOT/r['source_context']['path']).parent);feedback=[]
  for x in failed:
   v=x.get('validation',{});req={'columns':v.get('identity',{}).get('source_columns'),'relationships':v.get('identity',{}).get('relationships'),'inferred_type':v.get('inferred_type'),'inferred_unit':v.get('unit')}
   feedback.append({'failed_candidate':x['candidate'],'errors':x['errors'],'code_inferred_dependencies_and_type':req,'compiler_errors':v.get('errors',[])})
  i=deepcopy(r['input']);i['repair']={'scope':'Only listed failed candidates, preserve purpose and computational target. Any replacement must be disclosed. Do not regenerate unaffected candidates.','feedback':feedback,'not_evaluation_gold':True};messages=[{'role':'system','content':FEATURE_SYSTEM},{'role':'user','content':json.dumps(i,separators=(',',':'))}];prepared.append({'id':id+'_repair','original_id':id,'input':i,'messages':messages,'source_context':r['source_context'],'source_kind':r['source_scope']['source_kind'],'old_response':pin(TOP/'training/responses/feature_adapter'/(id+'.json'))})
 assert len(prepared)<=4;jl(TOP/'development/repair_inputs.jsonl',prepared);write(TOP/'development/repair_freeze.json',{'inputs':pin(TOP/'development/repair_inputs.jsonl'),'max_per_input':1,'frozen_evaluation_scores_unchanged':True,'gold_used_in_feedback':False})
def run():
 rs=rows(TOP/'development/repair_inputs.jsonl');write(TOP/'development/GPU_preflight.json',{'query':subprocess.check_output(['nvidia-smi','--query-gpu=index,name,memory.used,memory.total,utilization.gpu','--format=csv,noheader'],text=True),'CUDA_VISIBLE_DEVICES':os.getenv('CUDA_VISIBLE_DEVICES')});torch.manual_seed(42);adapter=str((TOP/'training/adapter_final').relative_to(ROOT));model,tok=load_model(adapter);items=[]
 for r in rs:
  c=load_context((ROOT/r['source_context']['path']).parent);admission(c,'model_input');key=reserve('development_repair',[r['id']],adapter,2048);g=gen_batch(model,tok,[r['messages']],2048)[0]
  try:o=json.loads(g['raw_output']);checks=evaluate_candidates(c,o)
  except Exception as e:o=None;checks={'JSON_contract':False,'zero':False,'candidates':[],'errors':[str(e)]}
  item={'id':r['id'],'original_id':r['original_id'],'source_kind':r['source_kind'],'prediction':o,'checks':checks,'input_sha':digest(r['input']),'old_response':r['old_response'],**g};write(TOP/'development/responses'/(r['id']+'.json'),item);complete(key,'development_repair',[item]);items.append(item)
 write(TOP/'development/repair_results.json',{'items':items,'frozen_metrics_not_changed':True,'generation_budget':audit()})
if __name__=='__main__':
 import sys
 prepare(sys.argv[2:])if sys.argv[1]=='prepare'else run()
