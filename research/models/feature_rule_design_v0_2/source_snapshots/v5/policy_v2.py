"""Bounded rule grounding 4.0: explicit gap IDs, typed short silver answers.

No prediction is repaired here. Reason semantics require a separate direct review.
"""
from evidence_gateway_v2 import *
import contracts as old
import re

INPUT_VERSION='feature-evidence-bundle-3.0.0'
POLICY='rule-grounding-4.0.0'
OUTPUT_VERSION='bounded-rule-1.1.0'
GAPS={
 'usable_calculated_observations':'Current application feature has zero usable outputs; admit an already known observed condition only after usable outputs exist.',
 'valid_reference_values':'Reference numeric feature has zero eligible outputs; obtain reference outputs for the requested numeric baseline, not current observations.',
 'minimum_reference_sample':'Reference has some eligible numeric outputs but fewer than the supplied minimum_n=20.',
 'physical_or_comparable_calculation_unit':'Physical or comparable calculation unit is unconfirmed; do not assume a raw NUMBER field is count or ms.',
}

def output_schema():
 s=deepcopy(old.schema());s['properties']['needed']['items']={'enum':list(GAPS)}
 return s

SYSTEM='''You generate one bounded HR/SR decision from verified feature, measurement and evidence input. Return JSON only, no thinking. Required exact keys: v (string "1"), feature, sha, role, state, basis, condition, evidence, needed, reason. role is HR or SR. state: proposed (supported executable rule), deferred (known typed condition retained but no usable current observations), request_evidence (condition cannot yet be grounded; condition=null), no_applicable (no applicable rule for the requested purpose; condition=null). basis allowed values: ddl_not_null, observed_diagnostic, reference_p95, insufficient, none. Evidence contains only supplied confirmed constraint IDs, measurement ID, verified baseline ID. needed contains only the supplied needed_evidence_catalog ID(s) corresponding to the actual gap; never invent an alias. Condition AST exact forms: {"op":"eq","value":true} for Boolean activation, {"op":"eq","value":0} for absent matched parent, {"op":"lt","value":0} for signed end-minus-start coordinates, {"op":"is_missing"} for unavailable numeric calculated output, {"op":"gt","baseline":"supplied verified baseline ID"}, or null. Numeric input remains numeric when is_missing yields a Boolean condition. HR true means declaration violation; SR true means diagnostic activation. Confirmed NOT NULL retains HR even with zero current violations. No confirmed constraint means no HR. Observed SR needs valid_count>0, NOT reference minimum20. valid_count>0 with zero activations is still observed. valid_count=0 preserves the known typed condition as deferred with usable_calculated_observations. Numeric reference comparison needs a verified baseline or requests its exact supplied gap ID; valid_reference_values refers to reference, not current observations. Verified count/count ratio has ratio unit; unknown raw quantity is not verified count. Do not change the feature, unit, population, SHA, calculations or supplied threshold. Explain briefly: relevant measured facts, what the condition/state means, and only the limitation relevant to this case. A time-coordinate difference is not count/count, a numeric sum is not a Boolean feature. Do not copy unrelated clock/fault/count/independence boilerplate. No gold answers are supplied. Raw missing tokens are CSV source projections, not proof of live DB SQL NULL enforcement. Supplied offline captured frequencies are not future/live lookups.'''

def model_input(b,request,ref=None):
 i=old.model_input(b,request,ref)
 i['input_contract']=INPUT_VERSION;i['version']['validation']=POLICY
 i['output_contract_version']=OUTPUT_VERSION
 i['needed_evidence_catalog']=GAPS
 i['allowed_missing_evidence_ids']=(['usable_calculated_observations'] if request=='observational_diagnostic'and i['measurement']['valid_count']==0 else i['baseline'].get('needed',[])if request=='numeric_reference_comparison'and i['feature']['dtype']!='boolean' else [])
 i['reason_guidance']={'fact_scope':'Original feature valid/missing counts; condition result Boolean is separate','style':'Brief case-specific measured fact, decision, relevant limitation only'}
 return i

def expected_target(i):
 t=old.expected_target(i);f=i['feature'];s=i['measurement'];r=i['request'];n=s['valid_count'];m=s['missing_count']
 if r=='confirmed_HR_review':
  if t['state']=='proposed':t['reason']=f'Confirmed NOT NULL for the exact source column. The Boolean missing indicator has {s["true_count"]} violations among {s["row_count"]} captured rows; true violates the declaration. Zero violations do not remove it.'
  else:t['reason']='No confirmed DDL or business constraint is supplied for this calculation; observations alone do not establish an HR.'
 elif r=='observational_diagnostic':
  typ=('Boolean feature activation'if f['dtype']=='boolean'else 'signed time-coordinate difference '+str(f['unit'])if f['expression']['op']=='datetime_diff'else 'matched-parent count'if f['expression']['op']=='parent_match_count'else 'numeric calculated output '+str(f['unit']))
  t['reason']=f'{typ}: {n} valid and {m} unavailable feature outputs. '+('Keep the known typed condition deferred until usable current outputs exist.'if n==0 else 'Propose this observed diagnostic; no reference minimum applies.')
  if t['condition']['op']=='is_missing':t['reason']+=' It checks calculated unavailability, without asserting a source-null cause.'
 elif t['state']=='proposed':
  b=i['baseline'];t['reason']=f'Compare this {f["dtype"]} {f["unit"]} feature with the code-measured reference p95 {b["value_exact"]} {b["unit"]}, n={b["n"]}, minimum={b["minimum_n"]}; strictly greater activates.'
  upper=i['range_proof'].get('upper')
  if upper is not None and Decimal(str(b['value_exact']))>=Decimal(str(upper)):t['reason']+=' The supplied definition range makes activation impossible; this is not a useful diagnostic.'
 elif t['state']=='request_evidence':
  gap=t['needed'][0];t['reason']={'valid_reference_values':'The reference contains zero eligible numeric values. Request reference values to construct the requested baseline; no comparison condition is yet grounded.','minimum_reference_sample':f'The reference has fewer than the supplied {i["reference_policy"]["minimum_n"]} eligible values. Request enough reference values; no extra normality or independence requirement applies.','physical_or_comparable_calculation_unit':'The raw arithmetic is defined but its physical or comparable unit is unconfirmed; request this unit evidence before a numerical baseline comparison.'}[gap]
 else:t['reason']='The feature is Boolean, so a numerical p95 comparison does not apply; its type is already known.'
 return t

def constraint_binding_errors(i):
 f=i['feature'];ex=f['expression'];err=[]
 for c in i['constraints']:
  target=c.get('target');col=next((x for x in i['sources']['columns']if target and (x['table'],x['column'])==(target.get('table'),target.get('column'))),None)
  if not(ex.get('op')=='is_missing'and ex.get('args')==[target]and f['dtype']=='boolean'and c.get('kind')=='confirmed_schema_NOT_NULL'and col and col['nullable']is False and set(c.get('schema_evidence_ids',[]))==set(col['evidence_ids'])):err.append({'path':'/constraints','code':'exact_NOT_NULL_source_projection_binding'})
 return err

def validate_rule(i,t):
 errs=[{'path':'/'+'/'.join(map(str,e.absolute_path)),'code':'output_schema','error':e.message}for e in Draft202012Validator(output_schema()).iter_errors(t)]
 schema_pass=not errs
 if errs:return {'passed':False,'schema_passed':False,'errors':errs,'policy':POLICY}
 errs+=constraint_binding_errors(i)
 ex=expected_target(i)
 cond=t.get('condition')
 if cond and 'value'in cond:
  value=cond['value']
  if (i['feature']['dtype']=='boolean'and type(value)is not bool)or(i['feature']['dtype']!='boolean'and type(value)not in {int,float}):errs.append({'path':'/condition/value','code':'condition_literal_type'})
 for k in ['feature','sha','role','state','basis','condition','needed']:
  if t[k]!=ex[k]:errs.append({'path':'/'+k,'code':'typed_role_state_condition_or_gap','provided':t[k],'allowed':ex[k]})
 if set(t['evidence'])!=set(ex['evidence']):errs.append({'path':'/evidence','code':'confirmed_reference','provided':t['evidence'],'allowed':ex['evidence']})
 return {'passed':not errs,'schema_passed':schema_pass,'errors':errs,'policy':POLICY,'direct_meaning_review_required':True}

def factual_review(i,t):
 """Narrow reproducible fact contradictions; NEVER a general prose approval."""
 errs=[]
 if not isinstance(t,dict):return {'passed':False,'errors':['non_object'],'full_meaning_approval':False}
 reason=t.get('reason','').lower();s=i['measurement'];f=i['feature']
 if t.get('state')=='proposed'and re.search(r'\bdefer(?:red)?\b',reason) and not re.search(r'no need to defer|not deferred|rather than defer',reason):errs.append('proposed_reason_defer_conflict_requires_direct_review')
 if i['request']=='observational_diagnostic'and s['valid_count']>0 and re.search(r'\b(zero|no|0) (valid|usable|eligible) (feature |calculated )?(outputs|observations|values)',reason):errs.append('invented_zero_valid_count')
 if f['dtype']!='boolean'and re.search(r'(boolean (feature|variable)|missing.indicator (feature|variable))',reason):errs.append('numeric_input_called_boolean_feature')
 if f['expression']['op']=='datetime_diff'and 'count/count'in reason:errs.append('coordinate_explanation_replaced_by_ratio')
 return {'passed':not errs,'errors':errs,'full_meaning_approval':False,'method':'Limited measured-fact contradictions; separate case-specific direct review required'}

def assess(i,raw,values=None):
 try:p=json.loads(raw)
 except (ValueError,TypeError):return {'prediction':None,'JSON_valid':False,'validation':{'passed':False,'schema_passed':False,'errors':[{'path':'/','code':'JSON_output'}]},'fact_review':{'passed':False,'errors':['JSON_output']},'utility':None}
 v=validate_rule(i,p);fact=factual_review(i,p)
 u=old.utility(i,p,values)if v['passed']and p['condition']is not None else {'condition_evaluation':'not_evaluated','operational_adoption':False}if v['passed']else None
 return {'prediction':p,'JSON_valid':isinstance(p,dict),'validation':v,'fact_review':fact,'utility':u}

def record(i,sid,family,kind,proof=None):
 t=expected_target(i);assert validate_rule(i,t)['passed'];assert factual_review(i,t)['passed']
 cat='HR'if t['role']=='HR'and t['state']=='proposed'else t['state']if t['state']!='proposed'else 'baseline_SR'if t['basis']=='reference_p95'else 'observed_SR'
 return {'id':sid,'family':family,'source_kind':kind,'label_origin':'assistant_authored_silver','actual_incident_label':False,'input':i,'target':t,'category':cat,'messages':[{'role':'system','content':SYSTEM},{'role':'user','content':json.dumps(i,ensure_ascii=False,separators=(',',':'))},{'role':'assistant','content':json.dumps(t,ensure_ascii=False,separators=(',',':'))}],'evidence':proof,'versions':{'input':INPUT_VERSION,'output':OUTPUT_VERSION,'grounding':POLICY,'teacher':'focused-silver-2.0.0'}}
