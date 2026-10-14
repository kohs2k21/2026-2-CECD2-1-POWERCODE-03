"""Versioned evidence gateway, rule policy and utility sidecar. No output repair."""
from pilot_io import *
from copy import deepcopy
from collections import Counter
from decimal import Decimal
from jsonschema import Draft202012Validator
from v1_validator import schema, observational_condition
import sys
P25=ROOT/'data/eda/schema_generalization_v25/20261012_sr_review_feature_scope'
sys.path.insert(0,str(P25/'implementation'));sys.path.insert(0,str(ROOT))
from analysis_engine import Engine
from source_gate import guarded
INPUT_VERSION='feature-evidence-bundle-2.0.0';POLICY='rule-grounding-3.0.0';UTILITY='diagnostic-utility-1.0.0'
UNITS={'count','ratio','dimensionless','ms','s','us'}

def semantic_signature(f):
 p=f['plan'];return digest({'expression':p['expression'],'grain':p['grain'],'population':p['population'],'group_by':p['group_by'],'missing_policy':p['missing_policy'],'dtype':f['dtype'],'unit':f['unit'],'calculation':f['calculation_policy']})

def expression_range(ex):
 op=ex['op']
 if op in {'is_missing','eq','lt','lte','gt','gte','and'}:return {'kind':'boolean','lower':False,'upper':True}
 if op in {'child_count','conditional_count','parent_match_count','group_count'}:return {'kind':'number','lower':0,'upper':None}
 if op=='divide':
  a,b=ex['args']
  # Only a genuine subset of the same fully captured child population yields [0,1].
  if a['op']in {'conditional_count','child_count'}and b['op']=='child_count'and a.get('relationship_id')==b.get('relationship_id'):
   return {'kind':'number','lower':0,'upper':1,'proof':'subset_child_count / same_relationship_total_child_count, denominator0 unavailable'}
 return {'kind':'number','lower':None,'upper':None}

def load_capture(path):
 path=Path(path);return {'path':path,'inventory':read(path/'analysis_sample/inventory.json'),'policy':read(path/'analysis_sample/source_policy.json'),'capture':read(path/'capture_context.json'),'sample':read(path/'source_sample/sample_manifest.json'),'audit':read(path/'sample_audit.json')}

def admission(c,stage):return guarded(stage,c['policy'],lambda:None,inventory=c['inventory'])[1]

def engine_for(f,c):
 if f['origin']=='model_discovered_reused':
  import importlib.util
  spec=importlib.util.spec_from_file_location('frozen_calculation_301',P25/'feature_discovery_model_r01/frozen_sources/analysis_engine.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m.Engine(c['inventory'],c['policy'],c['capture'])
 return Engine(c['inventory'],c['policy'],c['capture'])

def verify_feature(f,c):
 admission(c,'measurement');m=engine_for(f,c).measure(f['plan'],retain_values=False)
 assert m['definition_status']=='valid'and m['measurement_status']=='measured',m
 assert m['definition_sha256']==f['definition_sha256'],'definition_SHA_changed'
 assert m['validation']['inferred_type']==f['dtype'] and m['validation']['unit']==f['unit'],'inferred_type_unit_changed'
 assert m['version']==f['calculation_policy'],'calculation_policy_changed'
 assert digest(m)==digest(f['measurement']),'actual_code_measurement_changed'
 assert f['plan']['schema_version']==c['inventory']['schema_version']and f['source_version']==c['inventory']['source_version'],'source_schema_changed'
 return {'passed':True,'code_remeasurement':True,'source_admission':True,'definition_sha':m['definition_sha256'],'measurement_sha':digest(m)}

def bundle(f,c):
 proof=verify_feature(f,c)
 cols={(t['name'],x['name']):x for t in c['inventory']['tables']for x in t['columns']};selected=[]
 for t,col in f['source_columns']:
  m=cols[t,col];selected.append({'table':t,'column':col,'type':m['logical_type'],'nullable':m['nullable'],'evidence_ids':m['evidence_ids'],'missing_tokens':m['missing_tokens']})
 ex=f['plan']['expression'];constraints=[]
 if ex['op']=='is_missing'and ex['args'][0]['op']=='column':
  x=ex['args'][0];m=cols[x['table'],x['column']]
  if m['nullable']is False and not x['column'].startswith('__'):
   constraints=[{'id':'ddl_'+f['id'],'kind':'confirmed_schema_NOT_NULL','target':x,'schema_evidence_ids':m['evidence_ids']}]
 rels=[r for r in c['inventory']['relationships']if r['id']in f['relationships']]
 ts={f['plan']['grain']['table']}|{x['table']for x in selected}
 for r in rels:ts.update([r['from_table'],r['to_table']])
 period={'start_inclusive':c['audit']['selected_windows'][0][0],'end_exclusive':c['audit']['selected_windows'][-1][1],'timezone':c['sample']['approved_period']['timezone']}
 b={'contract':INPUT_VERSION,'feature':f,'calculation_semantics_sha':semantic_signature(f),'source_bound_definition_sha':f['definition_sha256'],'measurement_sha':digest(f['measurement']),'scope':{'period':period,'selection_windows':c['audit']['selected_windows'],'selection':f"{c['audit']['transactions']} original-key distinct transaction families;all linked P/M;no truncated children",'rows':f['measurement']['summary']['row_count'],'filter':f['plan']['population']['filter'],'table_rows':{t:c['audit']['table_rows'][t]for t in sorted(ts)},'source_pins':{t:pin(c['path']/'analysis_sample'/f'{t}.csv')for t in sorted(ts)},'row_proof_pins':{t:pin(c['path']/'analysis_sample'/f'{t}.row_admission.json')for t in sorted(ts)}},'columns':selected,'relationships':rels,'constraints':constraints,'availability':{'mode':'offline_completed_capture','online_window_defined':False,'capture_completeness':'all selected-family original file rows;not verified business lifecycle completion'},'unconfirmed':['business meaning of stored codes and quantities','business duration/common clock','online availability'],'versions':{'source':f['source_version'],'schema':f['schema_version'],'calculation':f['calculation_policy'],'validation':POLICY},'capture_path':str(c['path'].relative_to(ROOT)),'verification':proof}
 b['bundle_sha']=digest(b);return b

def baseline_for(ref_bundle,application):
 rf=ref_bundle['feature'];a=application['feature'];s=rf['measurement']['summary'];n=s['valid_count']
 if a['dtype']=='boolean':return {'state':'insufficient','needed':['numeric_variable']}
 if a['unit']not in UNITS:return {'state':'insufficient','needed':['physical_or_comparable_calculation_unit']}
 if n<20:return {'state':'insufficient','needed':['valid_reference_values'if n==0 else 'minimum_reference_sample']}
 b={'state':'verified','id':'b_'+a['id'],'definition_sha256':rf['definition_sha256'],'calculation_semantics_sha':ref_bundle['calculation_semantics_sha'],'source_version':rf['source_version'],'schema_version':rf['schema_version'],'unit':rf['unit'],'reference_period':ref_bundle['scope']['period'],'application_period':application['scope']['period'],'population':rf['plan']['population'],'group_by':rf['plan']['group_by'],'application_population':a['plan']['population'],'reference_scope_sha':digest(ref_bundle['scope']),'application_scope_sha':digest(application['scope']),'measurement_sha':ref_bundle['measurement_sha'],'n':n,'minimum_n':20,'quantile':0.95,'method':s['quantile_method'],'value':s['p95'],'value_exact':s['exact_decimal']['p95'],'operator':'gt','missing':'exclude from reference and propagate unavailable at comparison','refresh':'new reference on source or definition change;explicit manual review','expires_at':application['scope']['period']['end_exclusive'],'fault_interpretation':False,'reference_bundle_sha':ref_bundle['bundle_sha'],'application_definition_sha':a['definition_sha256'],'comparison_scope':'same-company captured-file population;not confirmed normal or independent samples'}
 b['sha']=digest(b);return b

def check_baseline(b,ref,app):
 assert b['state']=='verified'
 assert b['sha']==digest({k:v for k,v in b.items()if k!='sha'}),'baseline_SHA'
 expected=baseline_for(ref,app);assert b==expected,'baseline_not_code_record'
 assert ref['calculation_semantics_sha']==app['calculation_semantics_sha'],'baseline_calculation_semantics'
 assert ref['feature']['unit']==app['feature']['unit'],'baseline_unit'
 assert ref['feature']['plan']['group_by']==app['feature']['plan']['group_by'],'baseline_group'
 assert ref['scope']['period']['end_exclusive']<=app['scope']['period']['start_inclusive'],'future_or_application_reference_overlap'
 assert b['expires_at']>=app['scope']['period']['end_exclusive'],'expired_baseline'
 assert set(p['sha256']for p in ref['scope']['source_pins'].values()).isdisjoint(p['sha256']for p in app['scope']['source_pins'].values()),'reference_application_same_capture'
 return True

def model_input(b,request,ref=None):
 f=b['feature'];p=f['plan'];base=baseline_for(ref or b,b)
 if base['state']=='verified'and ref is not None:check_baseline(base,ref,b)
 # Compact model judgment view; proof, raw rows and verbose validation remain in bundle artifact.
 i={'input_contract':INPUT_VERSION,'feature':{'id':f['id'],'sha':f['definition_sha256'],'meaning':f['meaning'],'dtype':f['dtype'],'unit':f['unit'],'expression':p['expression'],'grain':p['grain'],'population':p['population'],'group_by':p['group_by'],'missing_policy':p['missing_policy']},'calculation_semantics_sha':b['calculation_semantics_sha'],'measurement_id':'m_'+f['id'],'measurement':{k:f['measurement']['summary'].get(k)for k in ['row_count','valid_count','missing_count','true_count','false_count','variation','min','max','p95','quantile_method']},'measurement_sha':b['measurement_sha'],'scope':{k:b['scope'][k]for k in ['period','selection','rows','filter','table_rows']},'source_scope_sha':digest(b['scope']),'sources':{'columns':b['columns'],'relationships':[{'id':r['id'],'from_table':r['from_table'],'to_table':r['to_table'],'from_columns':r['from_columns'],'to_columns':r['to_columns']}for r in b['relationships']]},'constraints':b['constraints'],'baseline':{k:base[k]for k in (['state','id','sha','source_version','unit','reference_period','application_period','n','minimum_n','quantile','method','value','value_exact','operator','missing','expires_at']if base['state']=='verified'else ['state','needed'])},'version':b['versions'],'availability':b['availability'],'unknown':b['unconfirmed'],'request':request,'reference_policy':{'minimum_n':20,'independence_or_normality_requirement':False,'operator':'gt'},'range_proof':expression_range(p['expression'])}
 if request=='observational_diagnostic':i['diagnostic_focus']='boolean_activation'if f['dtype']=='boolean'else 'physical_parent_absence'if p['expression']['op']=='parent_match_count'else 'negative_coordinate'if p['expression']['op']=='datetime_diff'else 'calculated_output_availability'
 return i

def expected_target(i):
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

def validate_rule(i,t):
 errs=[{'path':'/'+ '/'.join(map(str,e.absolute_path)),'error':e.message}for e in Draft202012Validator(schema()).iter_errors(t)]
 if errs:return {'passed':False,'errors':errs,'policy':POLICY}
 ex=expected_target(i)
 for k in ['feature','sha','role','state','basis','condition','needed']:
  if t[k]!=ex[k]:errs.append({'path':'/'+k,'code':'typed_role_state_or_condition','provided':t[k],'expected':ex[k]})
 if set(t['evidence'])!=set(ex['evidence']):errs.append({'path':'/evidence','code':'confirmed_evidence_reference','provided':t['evidence'],'allowed':ex['evidence']})
 reason=t['reason'].lower();typ=i['feature']['dtype']
 # Deliberately limited factual contradictions, not an automated prose meaning proof.
 if typ!='boolean'and any(q in reason for q in ['boolean feature','missing-indicator observation','missing indicator observation']):errs.append({'path':'/reason','code':'numeric_feature_vs_boolean_condition_confused'})
 if 'independent' in reason and any(q in reason for q in ['independent rows required','must be independent','independence is required']):errs.append({'path':'/reason','code':'invented_reference_requirement'})
 return {'passed':not errs,'errors':errs,'policy':POLICY,'direct_meaning_review_required':True}

def utility(i,t,values=None):
 cond=t.get('condition');bounds=i['range_proof'];impossible=False;threshold=None
 if cond and cond['op']=='gt':threshold=i['baseline']['value']if 'baseline'in cond else cond['value'];impossible=bounds.get('upper')is not None and float(threshold)>=float(bounds['upper'])
 elif cond and cond['op']=='lt':threshold=cond['value'];impossible=bounds.get('lower')is not None and float(threshold)<=float(bounds['lower'])
 states=Counter()
 for raw in values or []:
  v=Decimal(raw['decimal'])if isinstance(raw,dict)and 'decimal'in raw else raw
  if cond is None:x=None
  elif cond['op']=='is_missing':x=v is None
  elif v is None:x=None
  elif cond['op']=='eq':x=v==cond['value']
  elif cond['op']=='gt':x=v>Decimal(str(threshold))
  else:x=v<cond['value']
  states['unavailable'if x is None else 'true'if x else 'false']+=1
 scope_unknown=not i['availability']['online_window_defined']
 return {'version':UTILITY,'definition_sha':i['feature']['sha'],'rule_sha':digest(t),'condition_signature':digest({'definition':i['feature']['sha'],'condition':cond,'population':i['feature']['population']}),'static_range':bounds,'definition_impossible':impossible,'current_condition_counts':dict(states),'current_zero_true_is_not_static_impossible':states['true']==0 and not impossible,'offline_diagnostic_utility':'reject_impossible'if impossible else 'eligible'if t.get('state')=='proposed'else 'not_proposed','operational_conditions_unknown':scope_unknown,'operational_adoption':False,'capture_frequency_not_online':i['feature']['expression']['op']=='group_count'}
