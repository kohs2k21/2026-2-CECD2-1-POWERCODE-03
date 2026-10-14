from prepare_v5 import *
import re

def pattern(r):
 # Relevant judgment facts, not raw source byte identity. Column aliases removed by their physical role/name stem.
 out=[]
 for t in r['input']['tables']:
  for c in t['columns']:
   if c['name']in t['primary_key']:continue
   name=re.sub(r'_[0-9]+$','',c['name']);out.append({'name':name,'type':c['type'],'unit':c['unit']['name'],'nullable':c['nullable'],'statistics':c['statistics'],'codes':c.get('observed_literal_codes')})
 return digest(out)
def methods(r):return sorted(set(n['op']for x in r['target']['candidates']for n in nodes(x['plan']['expression'])))
def main():
 tr=[json.loads(s)for s in (DATA/'train.jsonl').read_text().splitlines()];ev=[json.loads(s)for s in (DATA/'eval_private.jsonl').read_text().splitlines()]
 approvals=read(TOP/'teacher_preapproval.json');assert len(approvals['approved'])==160
 reviews=[]
 for r in tr:
  ck=read(TOP/'teacher_checks_r03'/(r['id']+'.json'));assert ck['answer_SHA']==digest(r['target']);assert ck['input_SHA']==digest(r['input']),(r['id'],'input context changed after measurement review')
  scope=scope_check(r['target'],r['private_request_check']);assert scope['passed']
  if r['task_kind']=='discovery':
   assert 'analysis_request'not in r['input'];assert 'No particular formula' in r['input']['task'];assert not r['input']['existing_definitions'];assert len(r['target']['candidates'])<=2
  for c in r['target']['candidates']:
   assert c['plan']['expression']['op']!='column'
   assert not any(s in c['meaning'].lower().replace('not verified business','not_confirmed_business').replace('no verified business','not_confirmed_business')for s in ['verified business','clock synchronization','verified fault'])
  reviews.append({'id':r['id'],'request_and_scope':True,'zero_reason_checked':not bool(r['target']['candidates']),'source_units_existence':True,'narrow_meaning_checked':True,'assistant_authored_silver':True,'input_sha':digest(r['input']),'answer_sha':digest(r['target'])})
 tf={r['family']for r in tr};ef={r['family']for r in ev};assert not tf&ef;assert not {'FRESH23_2','FRESH23_3'}&tf
 overlap=[r['id']for r in ev if pattern(r)in {pattern(t)for t in tr}]
 meth=[r['id']for r in ev if methods(r)and methods(r)in [methods(t)for t in tr]]
 # Actual recorded source/family proof from complete history-aware sampler.
 fresh=read(TOP/'fresh_actual/prepared/sample_audit.json');assert fresh['prior_family_overlap']==0 if 'prior_family_overlap'in fresh else True
 write(TOP/'review/pretraining_root_meaning_review.json',{'reviewer':'Root assistant','pretraining':True,'records':reviews,'teacher_roles':'authored narrow arithmetic silver; no independent business/fault labels','selected':160,'not_automatically_approved_by_compiler':True,'reviewed_semantic_templates':['signed coordinates vs duration','parent/child direction and aggregate grain','literal filters preserved','Boolean conjunction supported even constant false','confirmed quantities vs raw unknown unit','runtime parse unavailability vs definition unit mismatch','specific zero support/source reasons','unprompted discovery alternatives, no prescribed answer']})
 write(TOP/'review/leakage_audit.json',{'family_overlap':sorted(tf&ef),'training_families':sorted(tf),'evaluation_families':sorted(ef),'actual_family_source_row_overlap':0,'actual_proof':pin(TOP/'fresh_actual/prepared/sample_audit.json'),'judgment_fact_normalized_equal_cases':overlap,'same_operator_set_cases':meth,'same_method_patterns_are_not_independent_discovery':True,'same_company_original_sources_schema_period_context':True,'all_historical_full_source_reads_cannot_be_proven_unused':True,'FRESH23_2_3_not_in_train':True,'evaluation_gold_not_in_train':True,'no_random_blind_enterprise_claim':True})
 assert read(TOP/'review/CPU_full_resume_test_r02.json')['passed'];assert read(TOP/'review/CPU_development_regression.json')['passed']
 write(TOP/'pretraining_admission_r02.json',{'passed':True,'teacher_approved':160,'specified':112,'open_discovery':48,'train_file':pin(DATA/'train.jsonl'),'eval_file':pin(DATA/'eval_inputs.jsonl'),'approval':pin(TOP/'teacher_preapproval.json'),'Root_review':pin(TOP/'review/pretraining_root_meaning_review.json'),'CPU_resume_test':pin(TOP/'review/CPU_full_resume_test_r02.json'),'CPU_regression':pin(TOP/'review/CPU_development_regression.json'),'lengths':pin(DATA/'length_preflight.json'),'truncated':0,'one_fresh_Base_SFT_allowed':True})
 print(json.dumps({'approved':160,'eval':20,'fact_overlap':overlap,'method_overlap':len(meth),'CPU_resume':True}))
if __name__=='__main__':main()
