"""Aggregate compiler facts and separately supplied Root meaning judgments."""
from prepare_v5 import *
from semantic_identity_v5 import key as definition_meaning_key

def known_catalog():
 old=ROOT/'data/eda/feature_rule_refinement_v3/20261013T175142+0900'
 fs=read(old/'data/feature_catalog64.json')
 extra=read(old/'data/HR_extra5.json');extra=extra.get('features',extra)if isinstance(extra,dict)else extra
 narrow=read(old/'new_features/adopted_narrow2.json')
 for x in extra:
  if 'feature'in x:x=x['feature']
  if 'plan'in x:fs.append(x)
 for x in narrow:
  f=x.get('feature')or x.get('measured_feature')or x.get('checks',{}).get('feature')
  if f:fs.append(f)
 for x in read(V4/'review/adopted_new_features.json'):
  if x['source_kind']=='approved_actual_capture':fs.append(x['checks']['feature'])
 return fs

def run():
 private={r['id']:r for r in [json.loads(s)for s in (DATA/'eval_private.jsonl').read_text().splitlines()]}
 decisions=read(TOP/'review/root_evaluation_review.json');by={(d['phase'],d['id']):d for d in decisions['inputs']};old=known_catalog();known={definition_meaning_key(f):f['id']for f in old};comparison={};allrecords=[];adopt=[]
 for phase in ['v4','v5']:
  observations=[];rows=[];seen=set(known)
  for path in sorted((TOP/'training/responses'/phase).glob('*.json')):
   o=read(path);r=private[o['id']];root=by[phase,o['id']];group='limits'if o['id'].startswith('eval_zero')else 'discovery'if r['task_kind']=='discovery'else 'specified'
   checks=o['checks'];zero=checks.get('zero',False);candstats=[];local_signatures=set()
   for i,c in enumerate(checks.get('candidates',[])):
    j=root['candidates'][i];f=c.get('feature');sig=definition_meaning_key(f)if f else None;duplicate_batch=sig in seen if sig else False;dup=(sig in known or sig in local_signatures)if sig else False
    # Same method can be requested in a specified task; it is still not a new discovery.
    duplicate_id=known.get(sig)
    measured=c.get('measurement_completed',False)and c.get('independent',False)
    eligible=measured and c.get('definition_valid',False)and c.get('visible_sources',False)and not c.get('raw_reexpression',False)and j['meaning']and j['unknown_information']and (group=='discovery'or root['request_satisfied'])
    fresh=eligible and not duplicate_batch
    item={'index':i,'definition':c.get('definition_valid',False),'measurement':measured,'visible_source':c.get('visible_sources',False),'meaning':j['meaning'],'unknown_information':j['unknown_information'],'duplicate':dup,'duplicate_of':duplicate_id,'same_calculation_in_another_frozen_input':duplicate_batch and not dup,'new_meaning_accepted':fresh,'review_reasons':j['reasons'],'checks':c}
    if sig:seen.add(sig);local_signatures.add(sig)
    candstats.append(item)
    if fresh and phase=='v5':adopt.append({'key':o['id']+'_'+str(i),'case':o['id'],'index':i,'source_kind':o['source_kind'],'task_kind':r['task_kind'],'direct_meaning_approved':True,'checks':c,'Root_review':j,'model_output':pin(path),'input':pin(DATA/'eval_inputs.jsonl'),'operational_usefulness_approved':False,'IF_status':'excluded_current_constant'if f['measurement']['summary'].get('variation')=='constant'and f['measurement']['summary'].get('valid_count',0)>0 else 'deferred','IF_reason':'sample variability is not sufficient for availability/leakage/unit/operational approval'})
   mathpass=checks.get('JSON_contract',False)and all(x['definition']and x['measurement']and x['visible_source']for x in candstats)
   meaningful=all(x['meaning']and x['unknown_information']for x in candstats)if not zero else root['zero_reason_correct']
   requestpass=root['request_satisfied'];dupbad=group=='discovery'and any(x['duplicate']for x in candstats)
   full=mathpass and meaningful and requestpass and not dupbad
   row={'id':o['id'],'group':group,'source_kind':o['source_kind'],'JSON':checks.get('JSON_contract',False),'candidate_count':len(candstats),'zero':zero,'zero_correct':root['zero_reason_correct']if zero else None,'request_satisfied':requestpass,'AST_type_unit_source_and_measurement':mathpass,'meaning':meaningful,'duplicate_in_discovery':dupbad,'full_pass':full,'root_reasons':root['reasons'],'candidates':candstats};rows.append(row);allrecords.append({'phase':phase,**row})
  comparison[phase]={'inputs':len(rows),'candidate_count':sum(x['candidate_count']for x in rows),'zero_count':sum(x['zero']for x in rows),'JSON':sum(x['JSON']for x in rows),'definition_valid_candidates':sum(c['definition']for x in rows for c in x['candidates']),'measured_candidates':sum(c['measurement']for x in rows for c in x['candidates']),'code_measurement_inputs':sum(x['AST_type_unit_source_and_measurement']for x in rows),'meaning_inputs':sum(x['meaning']for x in rows),'full_pass':sum(x['full_pass']for x in rows),'by_group':{g:{'inputs':sum(x['group']==g for x in rows),'full_pass':sum(x['full_pass']and x['group']==g for x in rows),'request_satisfied':sum(x['request_satisfied']and x['group']==g for x in rows),'candidate_count':sum(x['candidate_count']for x in rows if x['group']==g),'correct_zero':sum(x['zero']and x['zero_correct']for x in rows if x['group']==g),'supported_analysis_abandoned':sum(x['zero']and private[x['id']]['private_request_check'].get('supported',True)for x in rows if x['group']==g),'wrong_or_vague_zero_reasons':sum(x['zero']and not x['zero_correct']for x in rows if x['group']==g)}for g in ['specified','discovery','limits']},'rows':rows,'loss':read(TOP/'training'/(phase+'_evaluation.json'))['loss']}
 write(TOP/'comparison_r02.json',{'conditions':read(TOP/'evaluation_policy.json'),'results':comparison,'direct_meaning_review':pin(TOP/'review/root_evaluation_review.json'),'no_score_relaxation':True,'same_company_controlled_synthetic_not_enterprise_generalization':True})
 write(TOP/'review/adopted_new_features_r02.json',adopt)
 write(TOP/'review/definition_and_sample_duplicates_r02.json',{'existing_catalog':len(old),'decisions':allrecords,'definition_key_ignores_id_and_source_to_detect_reexpression':True,'sample_value_equal_definitions_not_deleted':True})
 print(json.dumps({'comparison':{p:{k:v for k,v in z.items()if k not in ['rows']}for p,z in comparison.items()},'accepted_actual':sum(x['source_kind']=='approved_actual_capture'for x in adopt),'accepted_synthetic':sum(x['source_kind']=='controlled_synthetic'for x in adopt)}))
if __name__=='__main__':run()
