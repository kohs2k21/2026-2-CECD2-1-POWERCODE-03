from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
import re
from jsonschema import Draft202012Validator
from v1_validator import schema, observational_condition
UNITS={'count','ratio','dimensionless','ms','s','us'}
POLICY='rule-grounding-4.1.0'
def base_expected_target(i):
 f=i['feature'];n=i['measurement']['valid_count'];r=i['request'];t={'v':'1','feature':f['id'],'sha':f['sha'],'role':'HR'if r=='confirmed_HR_review'else 'SR','state':'proposed','basis':'observed_diagnostic','condition':None,'evidence':[],'needed':[],'reason':''}
 dummy={'dtype':f['dtype'],'plan':{'expression':f['expression']}};cond=observational_condition(dummy)
 if r=='confirmed_HR_review':
  if i['constraints']:t.update(basis='ddl_not_null',condition={'op':'eq','value':True},evidence=[i['constraints'][0]['id']],reason='The confirmed NOT NULL declaration supports this exact Boolean missing indicator. True means declaration violation. Zero current violations retains the constraint; this is a source projection, not live database enforcement.')
  else:t.update(state='no_applicable',basis='none',reason='This exact calculation has no supplied confirmed DDL or business contract. Its observations do not establish mandatory child presence, count conservation, or business duration.')
 elif r=='observational_diagnostic':
  t.update(condition=cond,evidence=[i['measurement_id']])
  description='Boolean feature activation'if f['dtype']=='boolean'else 'negative signed time-coordinate difference'if f['expression']['op']=='datetime_diff'else 'absent matched parent'if f['expression']['op']=='parent_match_count'else 'unavailable numeric calculated output; the input feature remains numeric and the condition result is Boolean'
  if n==0:t.update(state='deferred',needed=['usable_calculated_observations'],reason=f'Preserve the typed {description} condition. This scope has zero valid feature outputs and {i["measurement"]["missing_count"]} unavailable outputs; defer observation admission without replacing unavailable by false or0 or attributing a source-null cause.')
  else:t['reason']=f'Inspect {description}. Actual valid feature outputs={n}, unavailable={i["measurement"]["missing_count"]}. Valid observations permit this diagnostic including zero activations; source-null causes, business faults and synchronized clocks are not established.'
 else:
  b=i['baseline']
  if f['dtype']=='boolean':t.update(state='no_applicable',basis='none',reason='The verified feature type is Boolean, so the requested numeric p95 comparison does not apply. Its known type is not missing evidence.')
  elif b['state']=='verified':
   t.update(basis='reference_p95',condition={'op':'gt','baseline':b['id']},evidence=[i['measurement_id'],b['id']],reason=f'Use the code-measured p95={b["value_exact"]} {b["unit"]} from {b["n"]} eligible reference outputs, minimum20. Reference/application scope and compatible calculation semantics and unit are explicit. This is an offline coordinate/count comparison, not normal traffic or an incident boundary.')
   bounds=i['range_proof']
   if bounds.get('upper')is not None and Decimal(str(b['value_exact']))>=Decimal(str(bounds['upper'])):t['reason']+=' The strict comparison cannot activate within this definition range; specification validity does not grant diagnostic utility or operational adoption.'
  else:
   need=b['needed'][0];t.update(state='request_evidence',basis='insufficient',needed=[need],reason={'physical_or_comparable_calculation_unit':'The raw arithmetic is defined but its physical or comparable unit is unconfirmed; obtain only that genuinely missing unit evidence.','valid_reference_values':'The numeric unit and diagnostic meaning are known, but no eligible reference values exist. Request valid reference values; preserve the separate known observed condition when requested.','minimum_reference_sample':'The provided minimum is20 eligible reference rows; the measured reference has fewer. Obtain more eligible reference values without adding normality or independence requirements.'}[need])
 return t
old=SimpleNamespace(schema=schema,expected_target=base_expected_target)
GAPS={'usable_calculated_observations': 'Current application feature has zero usable outputs; admit an already known observed condition only after usable outputs exist.', 'valid_reference_values': 'Reference numeric feature has zero eligible outputs; obtain reference outputs for the requested numeric baseline, not current observations.', 'minimum_reference_sample': 'Reference has some eligible numeric outputs but fewer than the supplied minimum_n=20.', 'physical_or_comparable_calculation_unit': 'Physical or comparable calculation unit is unconfirmed; do not assume a raw NUMBER field is count or ms.'}
def output_schema():
 s=deepcopy(old.schema());s['properties']['needed']['items']={'enum':list(GAPS)}
 return s
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
def base_validate_rule(i,t):
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
def validate_rule(i,t):
    result=base_validate_rule(i,t)
    for error in result['errors']:
        if error.get('path')=='/evidence':
            error.update(requirement='baseline SR requires measurement and baseline IDs',
                         confirmed_measurement_id=i['measurement_id'],
                         confirmed_baseline_id=i['baseline'].get('id'))
    return result
