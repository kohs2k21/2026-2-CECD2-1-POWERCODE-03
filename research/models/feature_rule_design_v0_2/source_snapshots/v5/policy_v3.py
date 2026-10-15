"""Capture context 4.0 and explicit dual-reference rule contract; inference only."""
from policy_v2 import *
import policy_v2 as p2
from functools import lru_cache
import csv
from source_hash_cache import install
install()

PARENT=ROOT/'data/eda/feature_rule_refinement_v2/20261013T151148+0900'
V3=ROOT/'data/eda/feature_rule_refinement_v3/20261013T175142+0900'
INPUT_VERSION='feature-evidence-bundle-4.0.0'
POLICY='rule-grounding-4.1.0'
OUTPUT_VERSION='bounded-rule-1.2.0'
CONTEXT='capture-context-4.0.0'
SYSTEM=p2.SYSTEM+''' Evidence binding is mandatory: a reference_p95 rule evidence array MUST contain BOTH the supplied measurement_id and the verified baseline.id; a baseline ID alone is incomplete. An observed_diagnostic proposed or deferred decision requires measurement_id. A ddl_not_null HR requires the exact confirmed constraint.id, not its kind as an evidence ID. No_applicable/request_evidence use no unsupported evidence IDs. basis is a kind, never an evidence ID. The provided global capture describes transaction families separately from feature population rows and T/P/M rows; a message feature can have more rows than transaction families. Use only supplied periods, facts, units and scope. Never add independence, normality, business-mandatory, time synchronization or live completion requirements.'''

def rows(p):return [json.loads(x)for x in Path(p).read_text().splitlines()if x.strip()]
def jl(p,rs):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x')as out:
  for r in rs:out.write(json.dumps(r,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')

@lru_cache(maxsize=12)
def capture_proof(path):
 c=load_capture(Path(path));admission(c,'model_input');m=c['sample'];a=c['audit'];sp=m['selection_plan'];tt=sp['transaction_table'];keys=sp['transaction_columns'];counts={};sets={};sourcepins={};rowpins={}
 assert m['body_values_copied']is False and a['children_truncated']is False and a['all_related_original_file_rows_included']is True
 assert pin(c['path']/'source_sample/sample_manifest.json')==a['source_manifest'],'capture_manifest_SHA'
 for t in c['inventory']['tables']:
  name=t['name'];path=ROOT/t['csv_source']['path'];sourcepins[name]=pin(path)
  # Admission was checked before source reread; Body is never selected into statistics.
  with path.open(encoding='utf-8-sig',newline='')as stream:
   reader=csv.DictReader(stream);selected=[tuple(row[k]for k in keys[name])for row in reader]
  assert all(all(k!=''for k in key)for key in selected),'missing_transaction_family_key'
  counts[name]=len(selected);sets[name]=set(selected)
  assert counts[name]==a['table_rows'][name]==m['table_coverage'][name]['selected_rows']==t['row_count'],'capture_table_row_count'
  rp=c['path']/'analysis_sample'/f'{name}.row_admission.json';proof=read(rp);assert proof['row_count']==counts[name]and len(proof['rows'])==counts[name];rowpins[name]=pin(rp)
 families=sets[tt];n=len(families)
 assert n==m['transaction_keys_selected']==a['transactions'],'manifest_original_unique_key_family_count'
 selected_families=read(c['path']/'selected_families.json');assert families=={(x['transaction_id'],)for x in selected_families},'selected_family_key_set'
 assert all(s<=families for s in sets.values()),'child_outside_captured_family'
 rel=[{k:r['value'].get(k)for k in ['id','parent_table','child_table','parent_columns','child_columns','comparison','matched_rows','missing_reference_rows','unmatched_parent_rows','ambiguous_parent_rows','complete_for_selected_transaction_approved_file_scope','business_mandatory_child_contract_verified']}for r in c['capture'].values()]
 result={'policy':CONTEXT,'transaction_families':n,'original_family_key_columns':keys[tt],'table_rows':counts,'selection_windows':a['selected_windows'],'approved_period':{k:m['approved_period'][k]for k in ['start_inclusive','end_exclusive','timezone']},'relationship_coverage':rel,'complete_for_selected_original_file_families':True,'business_lifecycle_completion_verified':False,'manifest':a['source_manifest'],'audit':pin(c['path']/'sample_audit.json'),'selected_families':pin(c['path']/'selected_families.json'),'source_pins':sourcepins,'row_proof_pins':rowpins,'source_version':c['inventory']['source_version'],'schema_version':c['inventory']['schema_version'],'original_family_keys_sha':digest(sorted(families))}
 result['sha']=digest(result);return result

def bundle(f,c):
 ctx=capture_proof(str(c['path'].resolve()));b=p2.bundle(f,c)
 b['scope']['selection']=f"{ctx['transaction_families']} original-key distinct transaction families;all linked P/M within approved original-file scope;no truncated children"
 b['scope']['transaction_families']=ctx['transaction_families'];b['scope']['global_capture']=ctx
 b['context_policy']=CONTEXT;b['contract']=INPUT_VERSION;b['versions']['validation']=POLICY
 b['bundle_sha']=digest({k:v for k,v in b.items()if k!='bundle_sha'});return b

def model_input(b,request,ref=None):
 i=p2.model_input(deepcopy(b),request,deepcopy(ref)if ref else None);i['input_contract']=INPUT_VERSION;i['output_contract_version']=OUTPUT_VERSION;i['version']['validation']=POLICY
 i['evidence_contract']={'ddl_not_null':'exact constraint.id','observed_diagnostic':'measurement_id, including deferred','reference_p95':'BOTH measurement_id AND verified baseline.id','request_evidence_or_no_applicable':'no unsupported IDs'}
 if 'global_capture'in b['scope']:
  ctx=b['scope']['global_capture'];i['scope'].update(transaction_families=ctx['transaction_families'],capture_table_rows=ctx['table_rows'],selection_windows=ctx['selection_windows']);i['capture_context']={k:ctx[k]for k in ['policy','sha','transaction_families','table_rows','selection_windows','manifest','complete_for_selected_original_file_families','business_lifecycle_completion_verified']}
 i['bundle_reference']={'sha':b['bundle_sha'],'reference_sha':ref['bundle_sha']if ref else None}
 return i

def admit_input(i,b,ref=None):
 errors=[]
 try:
  if 'capture_path'in b:
   c=load_capture(ROOT/b['capture_path']);ctx=capture_proof(str(c['path'].resolve()));fresh=bundle(b['feature'],c)
   assert digest(b)==digest(fresh),'application_bundle_source_or_definition_changed'
   if ref:
    rc=load_capture(ROOT/ref['capture_path']);assert digest(ref)==digest(bundle(ref['feature'],rc)),'reference_bundle_source_changed'
  else:
   fixture=b['feature']['fixture'];assert pin(ROOT/fixture['path'])==fixture,'controlled_fixture_changed';assert b['bundle_sha']==digest({k:v for k,v in b.items()if k!='bundle_sha'}),'controlled_bundle_SHA'
  
  if 'global_capture'in b['scope']:
   ctx=b['scope']['global_capture']
   for p in [ctx['manifest'],ctx['audit'],ctx['selected_families'],*ctx['source_pins'].values(),*ctx['row_proof_pins'].values()]:assert pin(ROOT/p['path'])==p,'capture_evidence_pin_changed'
  expected=model_input(b,i['request'],ref)
  assert digest(i)==digest(expected),'input_context_measurement_scope_baseline_binding_mismatch'
  assert constraint_binding_errors(i)==[],'constraint_binding'
 except (AssertionError,ValueError,KeyError)as e:errors.append({'path':'/input','code':'source_context_admission','detail':str(e)})
 return {'passed':not errors,'errors':errors,'policy':CONTEXT,'model_called':False}

def expected_target(i):return p2.expected_target(i)
def output_schema():
 s=p2.output_schema();s['properties']['evidence']['description']='For reference_p95, BOTH measurement_id and verified baseline.id are required. IDs must be supplied confirmed evidence.';return s
def validate_rule(i,t):
 v=p2.validate_rule(i,t);v['policy']=POLICY
 for e in v['errors']:
  if e.get('path')=='/evidence':e.update(requirement='reference_p95 needs both measurement_id and baseline.id; observed_diagnostic needs measurement_id; HR needs exact constraint.id',confirmed_measurement_id=i['measurement_id'],confirmed_baseline_id=i['baseline'].get('id'))
 return v
def assess(i,raw,values=None):
 a=p2.assess(i,raw,values)
 if a['prediction']is not None:a['validation']=validate_rule(i,a['prediction'])
 return a
def record(i,sid,family,kind,proof=None,target=None):
 t=deepcopy(target)if target else expected_target(i);assert validate_rule(i,t)['passed']
 r=p2.record(i,sid,family,kind,proof);r['target']=t;r['messages']=[{'role':'system','content':SYSTEM},{'role':'user','content':json.dumps(i,ensure_ascii=False,separators=(',',':'))},{'role':'assistant','content':json.dumps(t,ensure_ascii=False,separators=(',',':'))}];r['versions'].update(input=INPUT_VERSION,output=OUTPUT_VERSION,grounding=POLICY,teacher='context-corrected-silver-v3-not-trained');return r
