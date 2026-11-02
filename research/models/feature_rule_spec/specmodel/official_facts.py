"""Compiler-authoritative facts are distinct from unmodified AI prose."""
import copy,re
from .io import digest
from .semantic_facts import feature_facts
from .engine import nodes
def feature_semantics(compilation,dataset=None):
 f=compilation['compiled_feature'];out=feature_facts(compilation);out.update(author='compiler',contract='official-computation-facts1.0',not_AI_generated=True,expression=f['plan']['expression'],selected_result_use=f.get('result_view_compilation',{}).get('result_use','value'),output_excludes_filtered_rows=True,exceptions='Invalid sources/types/execution failures are errors, not unavailable values.',complete_natural_language_proof=False)
 expr=f['plan']['expression'];aggregates=[n for n in nodes(expr)if n['op']in ['child_count','conditional_count','child_sum']]
 if aggregates and dataset is not None:
  def substitute(n):
   if isinstance(n,dict):
    if n.get('op')in ['child_count','conditional_count','child_sum']:return {'op':'literal','value':0}
    return {k:substitute(v)for k,v in n.items()}
   return [substitute(x)for x in n]if isinstance(n,list)else n
  zero=substitute(expr)
  if not any(n['op']in ['column','parent_match_count','group_count']for n in nodes(zero)):
   value=dataset.independent.value(zero,{},f['plan']['grain']['table'],f['plan'])
   out['known_empty_child_group']={'result':value,'result_state':'unavailable'if value is None else'available','hypothetical_not_measured_count':True,'assumption':'Admitted output row, nonmissing parent keys, complete known-empty linked child set. Each child count/sum is0; the full expression may differ.'}
 out['facts_sha']=digest(out);return out
def feature_description_conflicts(proposal,compilation,dataset):
 f=compilation['compiled_feature'];facts=feature_semantics(compilation,dataset);w=facts.get('known_empty_child_group');out=[]
 if w and w['result'] is None and f['dtype']in ['integer','number']:
  text=proposal['meaning'];pattern=r'(?:no|zero|without|empty)[^.;]{0,30}(?:child|children)[^.;]{0,45}(?:means?|returns?|yields?|produces?|is|equals?)\s*(?:a\s+)?(?:numeric\s+)?(?:0|zero)\b'
  hit=re.search(pattern,text,re.I)
  if hit and not re.search(r'not|never|does not',hit.group(0),re.I) and not re.search(r'(?:count|sum|denominator)\s+(?:is|equals?|becomes?|returns?)\s*(?:0|zero)\b',hit.group(0),re.I):out.append({'path':'/candidates/0/meaning','code':'empty_child_result_claim_conflict','actual':hit.group(0),'allowed':{'computed_empty_case':w,'preserve':'Keep the valid expression, result_use, grain/population/source. Correct only the unsupported result assertion.'},'evidence':facts})
 return out
def rule_semantics(record,decision):
 from .rule_logic import counts
 i=record['input'];c=decision.get('condition');condition_error=None
 try:stats=counts(record['compilation']['values'],c)if c else None
 except (TypeError,ValueError,KeyError)as e:stats=None;condition_error=str(e)
 return {'author':'compiler','contract':'official-condition-facts1.0','not_AI_generated':True,'condition':c,'condition_valid':condition_error is None,'condition_error':condition_error,'statistics':stats,'valid_feature_observations':i['measurement']['valid_count'],'numeric_current':i['measurement']['valid_count']if i.get('feature',{}).get('dtype')in ['number','integer']else None,'retained_current':i['measurement']['row_count'],'reference_valid_n':i['baseline'].get('reference_valid_n'),'is_missing_checks_retained_unavailable_values':bool(c and c['op']=='is_missing'),'result_unavailable_is_not_itself_condition_unassessed':bool(c and c['op']=='is_missing'),'source_or_incident_cause_not_inferred':True,'full_natural_language_proof':False}
def rule_description_conflicts(record,pred):
 out=[]
 for j,d in enumerate(pred.get('decisions',[])):
  facts=rule_semantics(record,d);c=d.get('condition')
  if not facts['condition_valid']:continue
  if c:
   for field in ['reason','unavailable_reason']:
    text=d.get(field,'')
    for hit in re.finditer(r'\bunder\s+(is_missing)\s+(?:the|this)\s+condition\s+(?:is|would be|will be)\s+unassessed\b',text,re.I):
     if c['op']!=hit.group(1).lower() and facts['retained_current']>0 and not re.search(r'no retained|no selected|empty population|no rows',text,re.I):
      out.append({'path':f'/decisions/{j}/{field}','code':'condition_operator_prose_conflict','actual':hit.group(0),'allowed':{'actual_condition':c,'condition_facts':facts,'preserve':'Keep the correct role/state/condition/evidence. Describe this condition, not a different operator.'}})
  proof=record['input']['feature'].get('operator_state_proof',{})
  if c and c['op']=='eq' and proof.get('unavailable_possible') is False:
   text=d.get('unavailable_reason','')
   hit=re.search(r'(?:may|can|could|would)\s+(?:be\s+)?(?:unavailable|unassessed)[^.;]{0,65}(?:future values|future input values)',text,re.I)
   if hit and not re.search(r'not|never',hit.group(0),re.I):
    out.append({'path':f'/decisions/{j}/unavailable_reason','code':'complete_output_future_value_unavailability_conflict','actual':hit.group(0),'allowed':{'operator_state_proof':proof,'retained_rows':'Complete Boolean on each retained typed row, including unavailable intermediate values.','no_selected_rows':'No condition observations; not a per-row unavailable value.','execution_failure':'Invalid source/type/execution is an error, not a normal unavailable output.','preserve':'Keep the verified role/state/condition/evidence and other explanation fields.'}})
  if c and c['op']=='is_missing':
   for field in ['unavailable_reason','reason']:
    text=d[field];hit=re.search(r'(?:unassessed|unevaluable|cannot evaluate|prevent\w* evaluat)[^.;]{0,90}(?:calculated|feature|numeric|numerical) (?:value|result)[^.;]{0,40}(?:unavailable|cannot be computed|cannot be calculated|cannot be obtained)|(?:calculated|feature|numeric|numerical) (?:value|result)[^.;]{0,50}(?:unavailable|cannot be computed)[^.;]{0,60}(?:unassessed|unevaluable|prevent\w* evaluat)',text,re.I)
    if hit and not re.search(r'(?:does not|never|not itself|is not)',hit.group(0),re.I):out.append({'path':f'/decisions/{j}/{field}','code':'calculated_unavailable_not_missing_test_unassessed','actual':hit.group(0),'allowed':facts})
 return out
