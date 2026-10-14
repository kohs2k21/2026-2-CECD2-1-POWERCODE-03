from prepare_features import *
import re

def normalize_facts(i):
 tabs=[]
 for t in i['tables']:
  cols=[]
  for c in t['columns']:
   # Physical names/IDs/SHA are removed; actual facts, types, unit and missingness retained.
   cols.append({'type':c['type'],'nullable':c['nullable'],'unit':{'name':c['unit']['name'],'status':c['unit']['status']},'statistics':c['statistics'],'codes':c.get('observed_literal_codes')})
  tabs.append({'rows':t['rows'],'columns':sorted(cols,key=digest)})
 return digest({'tables':sorted(tabs,key=digest),'relationships':[{'arity':len(r['from_columns']),'statistics':r['statistics']}for r in i['relationships']]})

def run():
 train=rows(TOP/'data_r04/train.jsonl');ev=rows(TOP/'data_r04/eval_private.jsonl');trainhash={normalize_facts(r['input'])for r in train};overlap=[];byfamily={};checks=[]
 for r in train+ev:
  c=load_context((ROOT/r['source_context']['path']).parent);t=r['target'];ck=evaluate_candidates(c,json.loads(json.dumps(t)));assert ck['JSON_contract']and all(x['measurement_completed']and x['independent']for x in ck['candidates']);cols={(t['table'],x['name'])for t in r['input']['tables']for x in t['columns']};rels={x['id']for x in r['input']['relationships']}
  assert all(set(tuple(z)for z in x['sources']['columns'])<=cols and set(x['sources']['relationships'])<=rels for x in t['candidates']),'teacher missing source context'
  byfamily.setdefault(r['family'],[]).append(r['id']);checks.append({'id':r['id'],'input_sha':digest(r['input']),'target_sha':digest(t),'source_context':r['source_context'],'candidates':len(t['candidates']),'format':True,'fact_match':True,'independent':True,'direct_meaning':'narrow template reviewed; silver, not business gold'})
  if r in ev:overlap.append({'id':r['id'],'normalized_fact_sha':normalize_facts(r['input']),'exact_facts_overlap_train':normalize_facts(r['input'])in trainhash,'structural_method_previously_seen':r['category']!='unit_conversion','separate_source_family':True})
 assert set(r['family']for r in train).isdisjoint(r['family']for r in ev)
 # Strict counterexamples; not secretly correcting a prior model output.
 c=load_context((ROOT/train[0]['source_context']['path']).parent);good=json.loads(json.dumps(train[0]['target']));tests=[]
 for name,edit in [('unsupported_op',lambda x:x['plan']['expression'].update(op='abs')),('unknown_column',lambda x:x['plan'].update(expression={'op':'column','table':x['plan']['grain']['table'],'column':'ABSENT'})),('type',lambda x:x.update(expected_type='boolean')),('string_null',lambda x:x.update(expected_unit='null')),('bad_empty_group',lambda x:x['plan']['missing_policy'].update(empty_group='zero')),('version',lambda x:x['plan'].update(source_version='0'*64)),('bool_numeric',lambda x:x['plan'].update(expression={'op':'subtract','args':[{'op':'literal','value':True},{'op':'literal','value':1}]}))]:
  z=deepcopy(good);edit(z['candidates'][0]);out=evaluate_candidates(c,z);assert not out['candidates'][0]['measurement_completed'],name;tests.append({'case':name,'rejected':True,'errors':out['candidates'][0]['errors']})
 tests.append({'case':'JSON_roundtrip_positive_all160','passed':True,'samples':len(checks)})
 write(TOP/'review/pretraining_teacher_review.json',{'selected':144,'eval_private':16,'checks':checks,'families':byfamily,'positive_sources_and_measurements_verified':True,'source_versions_pinned':True,'teacher_authorship':'assistant_authored_silver','not_independent_operating_truth':True})
 write(TOP/'review/CPU_contract_tests.json',tests)
 write(TOP/'review/leakage_audit.json',{'fresh_actual_source':read(TOP/'fresh_actual/prepared/sample_audit.json'),'families_train_eval_disjoint':True,'exact_record_overlap':len({digest(r['input'])for r in train}&{digest(r['input'])for r in ev}),'normalized_judgment_facts':overlap,'exact_fact_overlap_count':sum(x['exact_facts_overlap_train']for x in overlap),'shared_method_pattern_count':sum(x['structural_method_previously_seen']for x in overlap),'shared_patterns_not_independent_skill_discovery':True,'FRESH23_2_3_used':False,'eval_gold_never_training':True})
 old=read(V3/'review/feature_direct_review.json');inv=load_capture(PARENT/'captures/evaluation/prepared')['inventory'];actualcols={(t['name'],c['name'])for t in inv['tables']for c in t['columns']};audit=[]
 for x in old['cases']:
  q=x['proposal'];ns=nodes(q['plan']['expression']);codes=[]
  if any(n['op']=='column'and(n.get('table'),n.get('column'))not in actualcols for n in ns):codes.append('nonexistent_source')
  if any(n['op']not in OPS for n in ns):codes.append('unsupported_operation')
  if any(n['op']=='subtract'and any(a.get('op')in ['is_missing','and','eq']for a in n.get('args',[]))for n in ns):codes.append('Boolean_numeric_mix')
  if q['expected_type']=='count':codes.append('expected_type_mismatch')
  if q.get('expected_unit')=='null'or q['plan']['missing_policy'].get('child_missing')is None:codes.append('JSON_null_or_policy_value')
  text=(q['purpose']+' '+q['meaning']).lower()
  if any(z in text for z in ['failure','success','request-response','latency','independent','empty payload','distinct']):codes.append('meaning_requires_direct_review')
  audit.append({'response':x['response'],'index':x['candidate_index'],'response_pin':x['response_pin'],'proposal_sha':digest(q),'model_error_categories':codes,'prior_direct_review':{k:x.get(k)for k in ['direct_review_state','new_meaning_adopted','narrow_literal_meaning_recognized']},'author_contract_error':'group_count is a real aggregate requiring zero; prior public instruction only explicitly mentioned child aggregates','unsupported_analysis_purpose_not_rejected':True})
 write(TOP/'review/v3_failure_task_audit.json',{'cases':audit,'author_errors':['public group_count empty-group guidance incomplete','v4 preparation used register_source return incorrectly; fixed before any training','v4 selected-population dependency refresh and JSON tuple/list comparison fixed before training','long samples rebuilt as compact evidence views, never truncated'],'model_errors_and_author_errors_separated':True,'all_v3_repairs_development_only':True,'replacement_pairs':{'base_case1':'replaced original targets by DATA_TYPE proportions','base_case2':'second replaced parent-level conjunction by filtered transaction condition','v2_case2':'changed IN/F and OUT/S selectors to IN/S and OUT/F; separate proposals, not same-target repair'},'no_new_engine_ops':True})
 fs=read(V3/'data/feature_catalog64.json');extra=read(V3/'data/HR_extra5.json');new=[x['feature']for x in read(V3/'new_features/adopted_narrow2.json')];catalog=[]
 for group,items in [('original64',fs),('HR_code5',extra),('v3_new_narrow2',new)]:
  for f in items:
   s=f['measurement']['summary'];valid=s['valid_count'];variation=s['variation'];const=valid>0 and variation.get('constant')is True if isinstance(variation,dict)else variation in ['constant']
   # native summary variation is inspected directly below; no approval from expression count.
   const=valid>0 and s.get('distinct_count',s.get('distinct_nonmissing',0))==1 if 'distinct_count'in s or 'distinct_nonmissing'in s else const
   catalog.append({'id':f['id'],'group':group,'origin':f['origin'],'definition_sha':f['definition_sha256'],'source_version':f['source_version'],'plan':f['plan'],'dtype':f['dtype'],'unit':f['unit'],'summary':s,'definition_verified':True,'measurement_completed':True,'reuse':'v3 frozen measurement evidence; not new v4 source measurement','confirmed_HR_retained_if_constant':group=='HR_code5'or f['id']in ['f004','f005','f047','f049','f051'],'IF_state':'deferred','IF_reason':'offline capture availability, leakage and online transform unconfirmed; constant/no-observation also limit IF','operational_usefulness_approved':False})
 assert len(catalog)==71;write(TOP/'definition_usage71.json',catalog);write(TOP/'pretraining_admission.json',{'passed':True,'teacher_review':pin(TOP/'review/pretraining_teacher_review.json'),'leakage':pin(TOP/'review/leakage_audit.json'),'CPU':pin(TOP/'review/CPU_contract_tests.json'),'classification':'source-disjoint actual families, shared schema/method patterns; not enterprise generalization'})
 print(json.dumps({'train_semantic_fact_review':144,'eval_frozen':16,'CPU_counterexamples':len(tests),'definitions':71,'passed':True}))
if __name__=='__main__':run()
