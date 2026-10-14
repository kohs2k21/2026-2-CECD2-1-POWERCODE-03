"""CPU evidence replay of a recorded actual AI feature -> fixed Rule execution."""
from feature_contract import *
from prepare_features import load_context
from inference_v3 import admission_record
import argparse
def replay(case,output):
 records={r['id']:r for r in rows(TOP/'data_r04/eval_inputs.jsonl')};r=records[case];saved=read(TOP/'training/responses/feature_adapter'/(case+'.json'));assert saved['input_sha']==digest(r['input']);c=load_context((ROOT/r['source_context']['path']).parent);obj=json.loads(saved['raw_output']);check=evaluate_candidates(c,obj);approved={x['key']:x for x in read(TOP/'review/adopted_new_features.json')};rr=rows(TOP/'rule_link/inputs.jsonl');rule_results=read(TOP/'rule_link/results.json')['responses'];lookup={x['id']:x for x in rule_results};features=[]
 blocked=[]
 for x in check['candidates']:
  key=case+'_c'+str(x['index'])
  if key not in approved or not approved[key]['direct_meaning_approved']:blocked.append({'key':key,'checks':x,'reason':'Feature meaning gate has not approved this candidate.'});continue
  rules=[]
  for inp in rr:
   if inp['feature_key']!=key:continue
   admission_record(inp);out=lookup[inp['id']];assessment=assess(inp['input'],out['raw_output'],inp['values']);rules.append({'input':inp['input'],'raw_model_output':out['raw_output'],'assessment':assessment,'Rule_adapter':str((PARENT/'training/adapter_final').relative_to(ROOT))})
  features.append({'key':key,'feature':x['feature'],'measurement':x['feature']['measurement'],'independent':x['independent_checks'],'direct_review':approved[key]['review'],'Rule_decisions':rules})
 result={'case':case,'schema_profile_input':r['input'],'actual_AI_feature_response':saved['raw_output'],'feature_adapter':str((TOP/'training/adapter_final').relative_to(ROOT)),'features':features,'blocked':blocked,'execution_mode':'CPU recomputation plus recorded real feature/Rule model responses; no new GPU generation','original_outputs_unchanged':True,'not_anomaly_detection':True};write(Path(output),result);print(json.dumps({'replayed':case,'features':len(features),'Rule_decisions':sum(len(x['Rule_decisions'])for x in features),'output':output}))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--case',required=True);p.add_argument('--output',required=True);a=p.parse_args();replay(a.case,a.output)
