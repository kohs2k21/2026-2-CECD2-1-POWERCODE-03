"""CPU replay of frozen AI -> code -> fixed Rule trace, or unapproved supplied-output validation.
No model calls, training or anomaly inference. Saved model prose is never corrected.
"""
from prepare_v5 import *
from identity_scope_v5 import bind_candidates,scope_check
from inference_v3 import admission_record
from policy_v3 import assess
import argparse

def replay(key=None):
 approved=read(TOP/'review/adopted_new_features.json');actual=[x for x in approved if x['source_kind']=='approved_actual_capture'];item=next((x for x in approved if x['key']==key),None)if key else next(iter(actual or approved),None)
 assert item is not None,'No meaning-approved candidate available; approval gate is not relaxed.'
 rows=[json.loads(s)for s in (DATA/'eval_inputs.jsonl').read_text().splitlines()];r=next(x for x in rows if x['id']==item['case']);c=load_context((ROOT/r['source_context']['path']).parent);assert pin(ROOT/r['source_context']['path'])==r['source_context'];saved=read(ROOT/item['model_output']['path']);assert pin(ROOT/item['model_output']['path'])==item['model_output']
 ch=bind_candidates(c,saved['prediction'],'v5|'+r['id']);x=ch['candidates'][item['index']];assert x['measurement_completed']and x['independent'];assert x['feature']['definition_sha256']==item['checks']['feature']['definition_sha256'];assert x['feature']['id']==item['checks']['feature']['id'];assert x['values']==item['checks']['values']
 rule_examples=[];path=TOP/'rule_link/inputs.jsonl'
 if path.exists():
  for rr in [json.loads(s)for s in path.read_text().splitlines()]:
   if rr['feature_key']!=item['key']:continue
   admission_record(rr);out=read(TOP/'rule_link/responses'/(rr['id']+'.json'));a=assess(rr['input'],out['raw_output'],x['values']);rule_examples.append({'input':rr['input'],'model_raw_output':out['raw_output'],'code_assessment':a,'Rule_adapter':'fixed refinement_v2'})
 return {'mode':'exact_frozen_v5_candidate_replay_not_new_discovery','feature_input':r['input'],'feature_model_raw_output':saved['raw_output'],'feature_index':item['index'],'code_identity_mapping':x['identity_mapping'],'fresh_CPU_measurement':x['feature']['measurement'],'independent_calculation':x['independent_checks'],'feature_direct_meaning_review':item['Root_review'],'Rule_examples':rule_examples,'new_candidate_creation':False,'fresh_GPU_generation':False,'operational_IF_or_fault_detection_approval':False,'source_and_definition_changes_blocked':True}

def main():
 p=argparse.ArgumentParser();p.add_argument('--key');p.add_argument('--output',required=True);p.add_argument('--provided-output');p.add_argument('--context');args=p.parse_args()
 if args.provided_output:
  assert args.context;context=load_context(Path(args.context));o=read(args.provided_output);ch=bind_candidates(context,o,'provided-output|'+digest(o));obj={'mode':'new_supplied_candidate_code_validation_only','raw_candidate':o,'checks':ch,'meaning_review':'pending; successful math is not meaning approval','Rule_forwarded':False,'model_generation_performed':False}
 else:obj=replay(args.key)
 write(Path(args.output),obj);print(json.dumps({'mode':obj['mode'],'output':args.output,'GPU_calls':0}))
if __name__=='__main__':main()
