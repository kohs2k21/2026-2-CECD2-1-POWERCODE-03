"""Bounded fixed-v2 demo. Never repairs a decision in code or reads gold answers."""
from pilot_io import *
import argparse,os,sys,time,subprocess
from collections import Counter,defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
from jsonschema import Draft202012Validator
from v1_validator import schema,validate_output
from teacher_semantics import semantic_check

PKG=Path(__file__).resolve().parent.parent

GUIDANCE='''Public contract semantics (unchanged pilot validation policy):
v is the STRING "1". basis is exactly ddl_not_null, observed_diagnostic, reference_p95, insufficient, or none; supporting IDs go only in evidence.
proposed: supported rule definition, not whether an event currently fires; condition required, needed must be [].
deferred: condition is known and retained, but usable calculated observations are unavailable; needed=["usable_calculated_observations"].
request_evidence: the comparison/contract cannot yet be specified because an actual required fact is missing; condition=null, basis=insufficient, explain the real gap.
no_applicable: no confirmed constraint/current requested policy applies; condition=null, basis=none, needed=[]. Never fabricate a rule to fill a quota.
The outer feature and sha identify the already verified variable; do NOT put measurement IDs, arguments, event fields, or missing policies into condition.
Exact condition forms ONLY: {"op":"is_missing"}; {"op":"eq","value":true}; numeric {"op":"eq","value":0}; numeric {"op":"lt","value":0}; {"op":"gt","baseline":"a_supplied_verified_baseline_id"}. Other schema-permitted literal comparisons still require the supplied diagnostic purpose; no arbitrary numerical cutoffs.
For HR review, a supplied confirmed NOT NULL declaration on an exact Boolean source-missing indicator supports eq true/ddl_not_null; zero observed violations does not remove the constraint. If no confirmed constraint applies, no_applicable. Never create a business HR from a baseline or arithmetic definition.
For the supplied observational purpose: Boolean activation uses eq true; physical parent absence uses numeric eq0; signed parsed end-start diagnosis uses numeric lt0; calculated-output availability uses is_missing on the output, without asserting a source-null cause.
valid_count=0 means observations unavailable: keep the typed known condition and defer. valid_count>0 with true_count=0 means usable observations with zero activations: it is NOT absence of observations. Observation SR does not require historical p95 or 20 rows.
Numeric reference comparison: use the supplied VERIFIED baseline ID, reference_p95, gt, and both measurement_id and baseline.id in evidence. Confirmed count/count ratio is comparable. Unconfirmed raw quantity units stay unconfirmed. If the baseline is insufficient, request only the genuinely missing fact already described by the input; do not invent baseline IDs/values. A known Boolean type makes numeric p95 policy no_applicable, not unknown-type evidence.
Only input.measurement_id, input.constraints[*].id, and VERIFIED input.baseline.id are allowed output evidence IDs. Schema/source IDs preserve context but are not executable evidence aliases. Copy the exact current feature ID/SHA. Do not change expression, type, unit, population, period, source version or missing policy.
Do not invent independence, normality, normal-traffic, mandatory-child, live-enforcement, fault, elapsed-business-time, common-clock, future-frequency, or operational approval requirements. State facts consistently with the supplied statistics.
Return just one JSON object. No markdown, no thinking text, no extra fields.
'''

def rows(p):return [json.loads(s)for s in Path(p).read_text().splitlines()if s.strip()]
def save(p,obj):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n');tmp.replace(p)
def append(p,obj):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('a')as f:f.write(json.dumps(obj,ensure_ascii=False,allow_nan=False)+'\n')
def receipt(out,kind,**payload):append(Path(out)/'call_ledger.jsonl',{'kind':kind,'at_kst':datetime.now(ZoneInfo('Asia/Seoul')).isoformat(),**payload})
def system_text():return (PKG/'v2_system.txt').read_text().strip()+'\n\n'+GUIDANCE+'\nFull public JSON Schema:\n'+json.dumps(schema(),ensure_ascii=False,separators=(',',':'))
def package_data():
 manifest=read(PKG/'model_manifest.json')
 for name,expected in manifest.get('pinned_package_assets',{}).items():
  if sha(ROOT/name)!=expected:raise ValueError('Fixed package asset changed: '+name)
 return {f['id']:f for f in read(EVAL/'feature_catalog.json')},rows(EVAL/'example_inputs.jsonl'),manifest

def source_gate(manifest):
 """Verify published aggregate snapshots; never read customer CSVs."""
 pin=manifest['publication_source_context']
 if sha(ROOT/pin['path'])!=pin['sha256']:raise ValueError('Published source context changed')
 snapshot=read(ROOT/pin['path'])
 if snapshot['body_excluded']is not True or snapshot['held_lineage_excluded']is not True:raise ValueError('Excluded source scope is not confirmed')
 if snapshot['raw_records_included']is not False:raise ValueError('Raw customer rows are prohibited')
 features,trusted,_=package_data()
 forbidden={'body','response_body','request_body','response_message','message_body','payload','message_payload'}
 for feature in features.values():
  if feature['source_version']!=snapshot['source_version']:raise ValueError('Source version mismatch')
  for table,column in feature['source_columns']:
   if column.lower()in forbidden:raise ValueError('Body source is excluded')
 for row in trusted:
  period=row['input']['scope']['period']
  if period!=snapshot['approved_period']:raise ValueError('Changed or held data period')
 return {'passed':True,'scope':'published_frozen_aggregate_snapshot_only','raw_source_revalidation':False}

def bind_input(value,features,trusted):
 i=value.get('input',value);matches=[r for r in trusted if digest(r['input'])==digest(i)]
 if not matches:raise ValueError('v0.1 accepts the32 pinned demo input contexts only. Changed statistics/scope/constraints/baseline are unverified; no context is silently substituted.')
 sid=value.get('id',matches[0]['id'])
 if sid!=matches[0]['id']:raise ValueError('Input ID must match its pinned context')
 fid=i['feature']['id'];f=features[fid]
 if i['feature']['sha']!=f['definition_sha256']or i['measurement']!=f['measurement']['summary']or i['measurement_sha']!=digest(f['measurement']):raise ValueError('Definition or measurement binding failed')
 return {'id':value.get('id',matches[0]['id']),'input':i,'input_sha':digest(i),'trusted_input_id':matches[0]['id']}

def assess(i,p,f):
 errors=[]
 if not isinstance(p,dict):return {'passed':False,'errors':[{'path':'/','code':'object_required','error':'One JSON object required'}]}
 errors=[{'path':'/'+ '/'.join(map(str,e.absolute_path)),'code':'output_contract','error':e.message}for e in Draft202012Validator(schema()).iter_errors(p)]
 if errors:return {'passed':False,'errors':errors,'format_passed':False,'semantic_policy_passed':False}
 code=validate_output(i,p,f);sem=semantic_check(i,p,f)
 return {'passed':code['passed']and sem['passed'],'format_passed':code['passed'],'semantic_policy_passed':sem['passed'],'errors':sem['errors'],'validator_policy':sem.get('policy')}

ERROR_PATHS={'feature_definition_reference':'/sha','requested_role':'/role','input_source_definition_version':'/sha','unprovided_evidence':'/evidence','executable_or_held_condition_missing':'/condition','conditionless_review_or_nonproposal_required':'/condition','boolean_comparison_value':'/condition/value','numeric_comparison_value':'/condition/value','confirmed_DDL_direction_or_scope':'/condition','DDL_evidence_missing':'/evidence','unsupported_observational_condition':'/condition','zero_valid_observations':'/state','unjustified_observation_hold':'/state','measured_observation_evidence_missing':'/evidence','verified_baseline_reference':'/condition','baseline_evidence_missing':'/evidence','condition_basis':'/basis','additional_information_missing':'/needed','unconfirmed_business_HR':'/basis','nonproposal_structure':'/state','proposed_with_unresolved_requirements':'/needed','hold_only_actual_observation_gap':'/needed','confirmed_HR_retention_or_nonapplication':'/state','usable_observations_vs_zero_true':'/state','typed_diagnostic_preserved':'/condition','known_boolean_is_not_missing_type_evidence':'/state','available_baseline_not_recovered':'/state','request_only_actually_missing_fact':'/needed'}
def pointer(obj,path):
 try:
  for k in path.strip('/').split('/'):obj=obj[int(k)]if isinstance(obj,list)else obj[k]
  return obj
 except (KeyError,IndexError,TypeError,ValueError):return {'missing':True}
def feedback(record,context,human=None):
 p=record.get('prediction');errors=[]
 for e in record['validation']['errors']+(human.get('errors',[])if human else []):
  path=e.get('path',ERROR_PATHS.get(e.get('code'),'/'));allowed=schema()['properties'].get(path.split('/')[1],{}).get('enum')if path!='/'else None
  errors.append({**e,'path':path,'submitted_value':pointer(p,path),'allowed_values':allowed})
 i=context['input']
 return {'scope':'Repair only this one failed decision; unchanged verified input below. Do not replace successful inputs or invent facts. No gold answer is provided.','failed_output_raw':record['raw_output'],'errors':errors,'verified_facts':{'feature_id':i['feature']['id'],'definition_SHA':i['feature']['sha'],'dtype':i['feature']['dtype'],'unit':i['feature']['unit'],'missing_policy':i['feature']['missing_policy'],'valid_count':i['measurement']['valid_count'],'true_count':i['measurement'].get('true_count'),'measurement_id':i['measurement_id'],'confirmed_constraint_IDs':[c['id']for c in i['constraints']],'baseline':i['baseline'],'reference_policy':i.get('reference_policy')},'format_normalization_only':'Whitespace/JSON object key order may vary. No condition, state, numeric evidence, expression or reason is changed by code.'}

def human_reviews(out):return read(Path(out)/'human_reviews.json')if(Path(out)/'human_reviews.json').exists()else []
def latest_records(out):
 latest={}
 for r in rows(Path(out)/'model_outputs.jsonl')if(Path(out)/'model_outputs.jsonl').exists()else []:latest[r['id']]=r
 return latest
def review_for(out,r):return next((x for x in reversed(human_reviews(out))if x['id']==r['id']and x['stage']==r['stage']and x['raw_sha256']==r['raw_sha256']),None)
def import_reviews(out,p):
 incoming=read(p);incoming=incoming['reviews']if isinstance(incoming,dict)else incoming;records=rows(Path(out)/'model_outputs.jsonl');existing=human_reviews(out)
 for x in incoming:
  if not any(r['id']==x['id']and r['stage']==x['stage']and r['raw_sha256']==x['raw_sha256']for r in records):raise ValueError('Human review does not bind an actual preserved response')
  existing=[v for v in existing if not(v['id']==x['id']and v['stage']==x['stage'])]+[x]
 save(Path(out)/'human_reviews.json',existing)

class Generator:
 def __init__(self,out,manifest):
  import torch
  from transformers import AutoTokenizer,BitsAndBytesConfig,Qwen3_5ForConditionalGeneration
  from peft import PeftModel
  self.torch=torch;self.manifest=manifest;self.out=out;self.started=time.monotonic()
  gpu=subprocess.check_output(['nvidia-smi','--query-gpu=index,name,memory.total,memory.used,utilization.gpu','--format=csv,noheader'],text=True)
  if not torch.cuda.is_available()or torch.cuda.device_count()!=1:raise RuntimeError('Expose exactly one assigned GPU through CUDA_VISIBLE_DEVICES for this demo')
  if not torch.cuda.is_bf16_supported():raise RuntimeError('Current fixed inference plan requires supported bf16; no environment upgrade attempted')
  save(Path(out)/('GPU_'+datetime.now().strftime('%H%M%S')+'.json'),{'query':gpu,'CUDA_VISIBLE_DEVICES':os.getenv('CUDA_VISIBLE_DEVICES'),'visible_count':torch.cuda.device_count()})
  for p in [manifest['adapter']['weights'],manifest['adapter']['config']]:
   if sha(ROOT/p['path'])!=p['sha256']:raise ValueError('Fixed model identity changed')
  base_path=os.environ.get('RULE_MODEL_BASE_PATH')
  if not base_path:raise RuntimeError('Set RULE_MODEL_BASE_PATH to the local pinned Qwen snapshot; no automatic download')
  base_path=Path(base_path).expanduser().resolve()
  for name,expected in manifest['base']['metadata_sha256'].items():
   if sha(base_path/name)!=expected:raise ValueError('Pinned base metadata mismatch: '+name)
  torch.manual_seed(42);self.tokenizer=AutoTokenizer.from_pretrained(str(base_path),local_files_only=True)
  quant=BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_quant_type='nf4',bnb_4bit_use_double_quant=True,bnb_4bit_compute_dtype=torch.bfloat16)
  base=Qwen3_5ForConditionalGeneration.from_pretrained(str(base_path),local_files_only=True,quantization_config=quant,dtype=torch.bfloat16,device_map={'':0},attn_implementation='sdpa',low_cpu_mem_usage=True)
  for name,p in base.named_parameters():
   p.requires_grad_(False)
   if p.ndim==1 and 'norm'in name:p.data=p.data.to(torch.float32)
  self.model=PeftModel.from_pretrained(base,str(ROOT/manifest['adapter']['path']),is_trainable=False)
  for p in self.model.parameters():p.requires_grad_(False)
  assert not any(p.requires_grad for p in self.model.parameters());self.model.eval();self.model.config.use_cache=True;self.model.config.text_config.use_cache=True
  receipt(out,'fixed_v2_loaded',actual_class=type(base).__name__,trainable_parameters=0,weight_updates=0,grammar_forcing=False)
 def generate(self,contexts,stage,previous=None,human=None):
  prompts=[]
  for c in contexts:
   content=json.dumps(c['input'],ensure_ascii=False,separators=(',',':'))
   if stage=='repair':content=json.dumps({'verified_input':c['input'],'repair':feedback(previous[c['id']],c,human.get(c['id']))},ensure_ascii=False,separators=(',',':'))
   messages=[{'role':'system','content':system_text()},{'role':'user','content':content}]
   prompt=self.tokenizer.apply_chat_template(messages,tokenize=False,add_generation_prompt=True,enable_thinking=False);ids=self.tokenizer(prompt,add_special_tokens=False)['input_ids']
   if len(ids)>self.manifest['generation']['maximum_input_tokens']:raise ValueError('Input length exceeds explicit demo budget; no input/answer is truncated')
   prompts.append(ids);save(Path(self.out)/'requests'/stage/(c['id']+'.json'),{'messages':messages,'input_tokens':len(ids),'input_sha':c['input_sha'],'gold_in_input':False})
  starts=[x for x in rows(Path(self.out)/'call_ledger.jsonl')if x['kind']=='generation_start']if(Path(self.out)/'call_ledger.jsonl').exists()else []
  used=sum(len(x['ids'])for x in starts if x['stage']==stage);cap=32 if stage=='initial'else 16
  if used+len(contexts)>cap:raise ValueError('Response budget exhausted; no additional model call')
  used_ids=[sid for x in starts if x['stage']==stage for sid in x['ids']]
  if any(c['id']in used_ids for c in contexts):raise ValueError('At most one initial and one repair attempt per input')
  source_gate(self.manifest);receipt(self.out,'generation_start',stage=stage,ids=[c['id']for c in contexts],used_responses_after=used+len(contexts),cap=cap,max_new_tokens=512,thinking=False,greedy=True)
  t=self.torch;width=max(map(len,prompts));ids=t.tensor([[self.tokenizer.eos_token_id]*(width-len(p))+p for p in prompts],device='cuda:0');mask=t.tensor([[0]*(width-len(p))+[1]*len(p)for p in prompts],device='cuda:0');start=time.monotonic()
  with t.no_grad():generated=self.model.generate(input_ids=ids,attention_mask=mask,max_new_tokens=512,do_sample=False,use_cache=True,logits_to_keep=1,pad_token_id=self.tokenizer.eos_token_id)
  features,_,_=package_data()
  for c,s in zip(contexts,generated):
   tail=s[width:];raw=self.tokenizer.decode(tail,skip_special_tokens=True);eos=(tail==self.tokenizer.eos_token_id).nonzero().flatten();n=int(eos[0])+1 if len(eos)else len(tail);p=None
   try:p=json.loads(raw)
   except ValueError:pass
   checked=assess(c['input'],p,features[c['input']['feature']['id']]);record={'id':c['id'],'stage':stage,'origin':'model_generated','feature':c['input']['feature']['id'],'input_sha':c['input_sha'],'raw_output':raw,'raw_sha256':__import__('hashlib').sha256(raw.encode()).hexdigest(),'prediction':p,'validation':checked,'generated_tokens':n,'length_limit_reached':not len(eos)and n==512,'batch_seconds':round(time.monotonic()-start,3),'normalization':'JSON parsing only; raw preserved; no fences removed or semantic fields rewritten','prior_raw_sha256':previous[c['id']]['raw_sha256']if previous else None,'human_semantic_review':'pending'}
   append(Path(self.out)/'model_outputs.jsonl',record);save(Path(self.out)/'responses'/stage/(c['id']+'.json'),record);receipt(self.out,'generation_complete',stage=stage,id=c['id'],code_policy_passed=checked['passed'],tokens=n);print(json.dumps({'id':c['id'],'stage':stage,'code_policy_passed':checked['passed'],'tokens':n}),flush=True)
 def close(self):
  t=self.torch;receipt(self.out,'GPU_phase_complete',seconds=round(time.monotonic()-self.started,3),peak_allocated_MiB=round(t.cuda.max_memory_allocated()/2**20,1),peak_reserved_MiB=round(t.cuda.max_memory_reserved()/2**20,1),training_steps=0)

def repair_selection(contexts,latest,out,limit):
 failed=[c for c in contexts if c['id']in latest and latest[c['id']]['stage']=='initial'and(not latest[c['id']]['validation']['passed']or review_for(out,latest[c['id']])and not review_for(out,latest[c['id']])['passed'])]
 pools=defaultdict(list)
 for c in failed:
  i=c['input'];r=i['request'];key='HR_confirmed'if r=='confirmed_HR_review'and i['constraints']else 'HR_without_contract'if r=='confirmed_HR_review'else 'observed_hold'if r=='observational_diagnostic'and i['measurement']['valid_count']==0 else 'observed_'+i['feature']['dtype']if r=='observational_diagnostic'else 'baseline_verified'if i['baseline']['state']=='verified'else 'baseline_gap';pools[key].append(c)
 selected=[]
 for roundno in range(limit):
  for k in ['HR_confirmed','observed_boolean','baseline_verified','observed_number','observed_hold','baseline_gap','HR_without_contract','observed_integer']:
   if pools[k]and len(selected)<limit:selected.append(pools[k].pop(0))
  if len(selected)>=limit or not any(pools.values()):break
 return selected

def collect(out):
 out=Path(out);latest=latest_records(out);context=rows(out/'run_inputs.jsonl')if(out/'run_inputs.jsonl').exists()else [];ctx={r['id']:r for r in context};validated=[];pending=[];failed=[];provisional=[];results=[]
 for sid,r in latest.items():
  h=review_for(out,r);p=r['prediction'];passed=r['validation']['passed']and h is not None and h['passed'];status='failed'if not r['validation']['passed']or h and not h['passed']else 'awaiting_meaning_review'if h is None else 'validated'if p['state']=='proposed'else p['state']
  i=ctx[sid]['input'];text=f"{sid} · {p.get('role','?')if isinstance(p,dict)else '?'} · {status} · 유효 관측 {i['measurement']['valid_count']}/{i['measurement']['row_count']} · 단위 {i['feature']['unit']}"
  item={'id':sid,'origin':'model_generated','stage':r['stage'],'status':status,'decision':p,'input':i,'code_validation':r['validation'],'human_review':h,'raw_output':r['raw_output'],'readable_summary':text,'model_reason_original':p.get('reason')if isinstance(p,dict)else None,'output_semantically_rewritten':False};results.append(item)
  if passed and p['state']=='proposed':validated.append(item)
  elif passed:pending.append(item)
  elif status=='awaiting_meaning_review':provisional.append(item)
  else:failed.append(item)
 save(out/'validated_rules.json',validated);save(out/'pending_decisions.json',pending);save(out/'provisional_decisions.json',provisional);save(out/'demo_results.json',results)
 (out/'failed_outputs.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n'for x in failed))
 alloutputs=rows(out/'model_outputs.jsonl')if(out/'model_outputs.jsonl').exists()else []
 summary={'features':64,'demo_inputs':len(ctx),'initial_responses':sum(x['stage']=='initial'for x in alloutputs),'repair_responses':sum(x['stage']=='repair'for x in alloutputs),'validated_model_HR':sum(x['decision']['role']=='HR'for x in validated),'validated_model_SR':sum(x['decision']['role']=='SR'for x in validated),'deferred':sum(x['decision']['state']=='deferred'for x in pending),'request_evidence':sum(x['decision']['state']=='request_evidence'for x in pending),'no_applicable':sum(x['decision']['state']=='no_applicable'for x in pending),'failed':len(failed),'awaiting_meaning_review':len(provisional),'reviewed_manual':len(read(out/'reviewed_manual_examples.json'))if(out/'reviewed_manual_examples.json').exists()else 0,'model_success_excludes_manual':True,'training_steps':0,'independent_evaluation':False,'limits':['Existing32 evaluation cases reused as development demo','Offline captured sample;not real-time anomaly detection','Same original features/measurements; no new discovery or SFT']};save(out/'summary.json',summary)
 return summary
