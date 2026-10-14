"""Root judgments are supplied explicitly; code never rewrites model answers."""
from feature_contract import *
from collections import Counter
def expression_identity(ex):
 ex=deepcopy(ex)
 if not isinstance(ex,dict):return ex
 if ex['op']=='convert_unit'and ex['from_unit']==ex['to_unit']:return expression_identity(ex['args'][0])
 for k in ['args']:
  if k in ex:ex[k]=[expression_identity(a)for a in ex[k]]
 for k in ['where','value']:
  if isinstance(ex.get(k),dict):ex[k]=expression_identity(ex[k])
 if ex['op']=='and':ex['args']=sorted(ex['args'],key=digest)
 if ex['op']=='group_count':ex['keys']=sorted(ex['keys'])
 return ex
def definition_meaning_key(f):
 p=f['plan'];return digest({'expression':expression_identity(p['expression']),'grain':p['grain'],'population':{'table':p['population']['table'],'filter':expression_identity(p['population']['filter'])},'group_by':p['group_by'],'dtype':f['dtype'],'unit':f['unit']})
def run():
 review=read(TOP/'review/root_feature_meaning_review.json');judge={(x['phase'],x['id']):x for x in review['items']};old=read(TOP/'definition_usage71_review.json')['definitions'];existing={definition_meaning_key({'plan':x['plan'],'dtype':x['dtype'],'unit':x['unit']})for x in old};priv={x['id']:x for x in rows(TOP/'data_r04/eval_private.jsonl')};fact=read(TOP/'review/judgment_fact_overlap_r02.json');fo={x['id']:bool(x['matching_train_ids'])for x in fact['items']};summary={};items=[];adopted=[];seen=set();duplicates=[]
 for phase in ['base','feature_adapter']:
  rows_phase=[]
  for out in sorted((TOP/'training/responses'/phase).glob('*.json')):
   x=read(out);j=judge[phase,x['id']];p=priv[x['id']];cs=x['checks']['candidates'];row={'phase':phase,'id':x['id'],'source_kind':x['source_kind'],'exact_judgment_facts_overlap':fo[x['id']],'JSON_contract':x['checks']['JSON_contract'],'AST':all(c.get('validation',{}).get('passed',False)for c in cs),'source_exists':all(c['source_exists']and c.get('visible_sources',True)for c in cs),'dtype_unit':all(c.get('validation',{}).get('passed')and c['candidate'].get('expected_type')==c['validation'].get('inferred_type')and c['candidate'].get('expected_unit')==c['validation'].get('unit')for c in cs),'source_dependencies_complete':all('sources dependency mismatch'not in c['errors']for c in cs),'measurement':all(c['measurement_completed']and c['independent']for c in cs),'meaning':j['meaning_pass'],'required_analysis_coverage':j['coverage_pass'],'unknown_handling':j['unknown_pass'],'correct_zero':j.get('correct_zero',False),'zero':x['checks']['zero'],'candidate_count':len(cs),'length_limit_reached':x['length_limit_reached'],'root_review':j,'response':pin(out)}
   zero_ok=not cs and row['correct_zero'];row['pipeline_pass']=row['JSON_contract']and(row['measurement']or zero_ok)and row['meaning']and row['required_analysis_coverage']and row['unknown_handling']
   if not cs and not zero_ok:row['AST']=row['source_exists']=row['dtype_unit']=row['measurement']=False
   for c in cs:
    f=c.get('feature');key=x['id']+'_c'+str(c['index']);cj=next(z for z in j['candidates']if z['index']==c['index']);sig=definition_meaning_key(f)if f else None;dup=sig in existing if sig else False
    if sig and dup:duplicates.append({'phase':phase,'key':key,'kind':'existing71_definition_or_identity','meaning_key':sig})
    eligible=c['measurement_completed']and c['independent']and c.get('visible_sources',True)and cj['meaning_pass']and cj['unknown_pass']and j['coverage_pass']and not dup and not c.get('raw_reexpression')
    if eligible:
     stamp=(x['source_kind'],sig);repeated=stamp in seen if phase=='feature_adapter'else False
     if phase=='feature_adapter'and not repeated:
      seen.add(stamp);adopted.append({'key':key,'source_case':x['id'],'source_kind':x['source_kind'],'phase':phase,'checks':c,'review':cj,'direct_meaning_approved':True,'operational_usefulness_approved':False,'IF_approved':False,'same_judgment_fact_overlap':fo[x['id']],'response':pin(out),'meaning_key':sig})
     row.setdefault('new_narrow_candidates',0);row['new_narrow_candidates']+=1
   rows_phase.append(row);items.append(row)
  counts={'inputs':len(rows_phase),'proposals':sum(x['candidate_count']for x in rows_phase),'zero_responses':sum(x['zero']for x in rows_phase),'correct_zero':sum(x['correct_zero']for x in rows_phase),'supported_analysis_abandoned':sum(x['zero']and not x['correct_zero']for x in rows_phase),'candidate_compiler_valid':sum(c.get('validation',{}).get('passed',False)for out in (TOP/'training/responses'/phase).glob('*.json')for c in read(out)['checks']['candidates']),'candidate_definition_type_unit_source_valid':sum(c['definition_valid']for out in (TOP/'training/responses'/phase).glob('*.json')for c in read(out)['checks']['candidates']),'candidate_measured':sum(c['measurement_completed']for out in (TOP/'training/responses'/phase).glob('*.json')for c in read(out)['checks']['candidates'])}
  for field in ['JSON_contract','AST','source_exists','dtype_unit','source_dependencies_complete','measurement','meaning','required_analysis_coverage','unknown_handling','pipeline_pass','length_limit_reached']:counts[field]=sum(x[field]for x in rows_phase)
  counts['groups']={k:{'n':len(s),'pipeline_pass':sum(x['pipeline_pass']for x in s),'meaning':sum(x['meaning']for x in s),'measured_responses':sum(x['measurement']and not x['zero']for x in s),'correct_zero':sum(x['correct_zero']for x in s)}for k,s in [('actual',[x for x in rows_phase if x['source_kind']=='approved_actual_capture']),('synthetic_fact_overlap',[x for x in rows_phase if x['source_kind']=='controlled_synthetic'and x['exact_judgment_facts_overlap']]),('synthetic_nonoverlap',[x for x in rows_phase if x['source_kind']=='controlled_synthetic'and not x['exact_judgment_facts_overlap']])]}
  summary[phase]=counts
 write(TOP/'review/adopted_new_features.json',adopted);write(TOP/'review/definition_duplicates.json',duplicates);write(TOP/'comparison.json',{'policy':pin(TOP/'evaluation_policy.json'),'private_criteria':pin(TOP/'frozen_judgment_criteria.json'),'root_review':pin(TOP/'review/root_feature_meaning_review.json'),'summary':summary,'inputs':items,'adopted_actual':sum(x['source_kind']=='approved_actual_capture'for x in adopted),'adopted_synthetic':sum(x['source_kind']=='controlled_synthetic'for x in adopted),'operational_usefulness_approved':0,'IF_approved':0,'frozen_repairs':0,'not_independent_enterprise_generalization':True})
 print(json.dumps({'summary':summary,'adopted':len(adopted)}))
if __name__=='__main__':run()
