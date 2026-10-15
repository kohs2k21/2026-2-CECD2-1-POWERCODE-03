"""Silver generation/selection, request-preserving checks, separate open discovery.
Answers authored here, not sampled from a model or claimed as business ground truth.
"""
from prepare_features import *
from identity_scope_v5 import SYSTEM,CONTRACT_VERSION,scope_check,bind_candidates,evidence_ref_compatible
from collections import Counter
from copy import deepcopy
V4=ROOT/'data/eda/feature_rule_refinement_v4/20261013T191647+0900'
DATA=TOP/'data_r03';T='EC_TRANSACTION_MONITORING';P='EC_PROCESS_MONITORING';M='EC_MESSAGE_MONITORING'
def jl(p,rs):
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x')as f:
  for r in rs:f.write(json.dumps(r,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
def enrich_projection(c,inp):
 inp['contract']=CONTRACT_VERSION
 for t in inp['tables']:
  original=next(z for z in c['inventory']['tables']if z['name']==t['table'])
  for colx in t['columns']:
   o=next(z for z in original['columns']if z['name']==colx['name'])
   colx['description']=o.get('description')or'Physical metadata; business meaning unverified'
 inp['confirmed_evidence']=[{k:e[k]for k in ['id','kind','target','value','source']if k in e}for e in c['inventory'].get('evidence',[]) if e.get('kind')=='unit_contract']
 return inp

def create(id,family,category,c,task,cs,request,*,stop='Requested narrow calculation only; operational usefulness unconfirmed.',existing=None,feedback=None,subset=None):
 inp=enrich_projection(c,projection(c,task,existing,feedback));inp['task_kind']=request['kind']
 if subset is None and request['kind']=='specified' and (cs or c['scope']['source_kind']=='approved_actual_capture'):
  if cs:
   subset={}
   for candidate_obj in cs:
    for table,column in candidate_obj['sources']['columns']:subset.setdefault(table,[]).append(column)
    subset.setdefault(candidate_obj['plan']['grain']['table'],[])
  elif 'parent STATUS=S' in stop:
   subset=actual_subset(c,{T:['STATUS'],M:['DIRECTION','DATA_TYPE','DATA_NAME']},['physical_M_to_T'])
  elif category=='unknown_units':subset={T:['PROCESS_TIME']}
  else:subset={P:['START_TIME','END_TIME']}

 if request['kind']=='specified':inp['analysis_request']={k:v for k,v in request.items()if k not in ['zero_reason_terms','supported']}
 if subset:
  inp['tables']=[t for t in inp['tables']if t['table']in subset]
  for t in inp['tables']:t['columns']=[z for z in t['columns']if z['name']in set(subset[t['table']])|set(t['primary_key'])]
  visible={(t['table'],z['name'])for t in inp['tables']for z in t['columns']}
  inp['relationships']=[r for r in inp['relationships']if all((r['from_table'],z)in visible for z in r['from_columns'])and all((r['to_table'],z)in visible for z in r['to_columns'])]
  inp['capture']={k:v for k,v in inp['capture'].items()if k in {r['id']for r in inp['relationships']}}
  inp['confirmed_evidence']=[e for e in inp['confirmed_evidence']if (e.get('target',{}).get('table'),e.get('target',{}).get('column'))in visible]
 target={'candidates':cs,'stop_reason':stop};checked=bind_candidates(c,target,id)
 assert checked['JSON_contract']and all(x['measurement_completed']and x['independent']for x in checked['candidates']),checked
 scope=scope_check(target,request);assert scope['passed'],(id,scope)
 visible={(t['table'],z['name'])for t in inp['tables']for z in t['columns']};vrels={r['id']for r in inp['relationships']}
 for x in cs:
  assert set(tuple(z)for z in x['sources']['columns'])<=visible and set(x['sources']['relationships'])<=vrels,(id,'answer sources not provided')
 admission(c,'teacher')
 rec={'id':id,'family':family,'category':category,'task_kind':request['kind'],'teacher_source':'assistant_authored_silver','development_status':'development_silver','input':inp,'target':target,'source_context':pin(c['path']/'context.json'),'source_scope':c['scope'],'private_request_check':request,'review':{'format':True,'code_measurement':True,'independent_calculation':True,'request_scope':scope,'direct_meaning':'Root reviewed narrow calculation and supplied request; not business/fault gold'},'messages':[{'role':'system','content':SYSTEM},{'role':'user','content':json.dumps(inp,separators=(',',':'),ensure_ascii=False)},{'role':'assistant','content':json.dumps(target,separators=(',',':'),ensure_ascii=False)}]}
 write(TOP/'teacher_checks_r03'/(id+'.json'),{'id':id,'input_SHA':digest(inp),'answer_SHA':digest(target),'actual_measurements':checked,'scope_check':scope,'authorship':rec['teacher_source']});return rec

def real_request(k,cs,filtered=False):
 if k in [6,7]:return {'kind':'specified','supported':False,'zero_reason_terms':['PROCESS_TIME','unit']if k==6 else ['abs']}
 req={'kind':'specified','supported':True,'grain_table':cs[0]['plan']['grain']['table'],'required_operations':[],'required_columns':[]}
 if k==2:req.update(relationship='physical_P_to_T',required_operations=['child_sum','datetime_diff'],required_columns=[[P,'START_TIME'],[P,'END_TIME']])
 if k==5:req.update(required_operations=['and','is_missing'],required_columns=[[M,'DATA_NAME'],[M,'DIRECTION']])
 if filtered:req['population_filters']=[{'table':T,'column':'STATUS','value':'S'}]
 return req

def synthetic_request(k,cs,r,partial=False):
 req={'kind':'specified','supported':True,'grain_table':cs[0]['plan']['grain']['table']}
 if k==3:req.update(relationship=r,required_operations=['child_sum'])
 if k==4 and len(cs)>1:req['multi_targets']=['metadata_missingness','child_parent_matching']
 return req

def sharpen_zero(k,c,a=None,n=None):
 if k==0:return f'Physical count ratio requires confirmed units for {a}.{n["total"]} and {a}.{n["part"]}; both units are unknown. Raw arithmetic does not establish the requested count ratio.'
 if k==1:return 'The requested abs operator is unsupported by this engine; signed difference or a population split would replace the requested all-row absolute calculation.'
 if k==2:return 'ABSENT_TABLE.absent_qty is not present in the supplied physical inventory. The requested source cannot be replaced.'
 return 'The requested child_count on the declared relationship and population is already registered. Changing only its ID does not create a new definition.'

def build_training():
 old=[json.loads(s)for s in (V4/'data_r04/train.jsonl').read_text().splitlines()];train=[];reaudit=[]
 # Audit all old144, including the138 previously selected; source-scope is independent from numeric correctness.
 for oldr in old:
  cs=oldr['target']['candidates'];errors=[]
  if oldr['id']in ['train_actual_12','train_actual_13']:errors=['parent STATUS=S scope missing; no parent-attribute lookup in child-grain AST']
  elif oldr['id']in ['train_actual_6','train_actual_7','train_actual_14','train_actual_15']:errors=['zero reason fails to identify exact missing unit or unsupported abs']
  elif oldr['category']in ['child_counts','verified_quantities','missing_parent']and len(cs)<2:errors=['plural requested analyses but only one returned; regenerate complete target list']
  reaudit.append({'id':oldr['id'],'input_sha':digest(oldr['input']),'answer_sha':digest(oldr['target']),'source_context':oldr['source_context'],'request_reaudit':'needs_correction'if errors else 'narrow_template_rechecked','errors':errors,'numeric_checks_reused_from_v4':True,'old_selection_138_not_automatic_approval':True})
 # 80 specified synthetic/repair cases in ten old physical fixture families, newly checked against request.
 for f in range(10):
  family='train_synthetic_'+str(f);c=load_context(V4/'fixtures'/family);a='parent_'+family;b='child_'+family;r='link_'+family;seed=41+f;n={'pk':'row_'+str(seed),'key':'ref_'+str(seed),'tenant':'tenant_'+str(seed),'start':'begin_'+str(seed),'end':'finish_'+str(seed),'total':'quantity_'+str(seed),'part':'portion_'+str(seed),'state':'code_'+str(seed),'meta':'tag_'+str(seed),'ms':'coord_ms_'+str(seed),'sec':'coord_s_'+str(seed)}
  for k in range(8):
   id=f'train_A_syn_{f:02d}_{k:02d}';cat,task,cs,existing=synthetic_task(c,a,b,r,n,k,id+'01');feedback=None
   oldr=next(z for z in old if z['id']==f'train_syn_{f:02d}_{k:02d}')
   if oldr['input'].get('repair'):
    cs=cs[:1];feedback=deepcopy(oldr['input']['repair']);task+=' Repair only the supplied failed candidate; unchanged accepted candidates are outside this partial response.';cat='repair_'+cat
   request=synthetic_request(k,cs,r,bool(feedback));rec=create(id,family,cat,c,task,cs,request,existing=existing,feedback=feedback);rec['previous_record']={'id':oldr['id'],'input_sha':digest(oldr['input']),'answer_sha':digest(oldr['target']),'reason':'recheck request and source; restore omitted required second analysis where applicable'};train.append(rec)
 rc=actual_context(PARENT/'captures/evaluation/prepared',TOP/'contexts/training_actual')
 for j in range(16):
  k=j%8;id='train_A_actual_'+str(j);cat,task,cs=real_task(rc,k,id);request=real_request(k,cs);stop='Requested narrow calculation only; no business interpretation.'
  if k==6:stop='EC_TRANSACTION_MONITORING.PROCESS_TIME has unknown physical unit and unverified business duration meaning. The requested millisecond latency cannot be established; raw arithmetic is not a substitute.'
  if k==7:stop='abs is not supported. Signed END_TIME minus START_TIME on EC_PROCESS_MONITORING would not preserve the requested all-row absolute value.'
  if j>=8 and cs:
   task+=' Restrict to parents whose stored STATUS literal equals S.'
   if cs[0]['plan']['grain']['table']!=T:
    cs=[];request={'kind':'specified','supported':False,'zero_reason_terms':['STATUS','parent','lookup']};stop='The requested parent STATUS=S filter cannot be applied on message-grain rows with the supported AST: parent attribute lookup is unavailable. Removing STATUS or moving aggregation to transactions would replace the request.'
   else:
    cs=[candidate(rc,z['id'],z['plan']['expression'],T,z['meaning']+' Restricted parent STATUS=S.',where=binary('eq',col(T,'STATUS'),lit('S')))for z in cs];request=real_request(k,cs,True)
  rec=create(id,'known_actual_v2_128',cat,rc,task,cs,request,stop=stop)
  if cs:rec=trim_actual(rec);rec['messages'][0]['content']=SYSTEM
  oldr=next(z for z in old if z['id']=='train_actual_'+str(j));rec['previous_record']={'id':oldr['id'],'input_sha':digest(oldr['input']),'answer_sha':digest(oldr['target']),'reason':'precise unsupported parent filter or explicit zero reason; preserve supported analyses'};train.append(rec)
 for j in range(16):
  oldr=next(z for z in old if z['id']=='train_zero_'+str(j));c=load_context((ROOT/oldr['source_context']['path']).parent);kind=j%4;a='parent_negative_'+str(j);seed=101+j;n={'total':'quantity_'+str(seed),'part':'portion_'+str(seed)}
  req={'kind':'specified','supported':False,'zero_reason_terms':[['unit'],['abs'],['ABSENT_TABLE','absent_qty'],['registered']][kind]};train.append(create('train_A_zero_'+str(j),oldr['family'],oldr['category'],c,oldr['input']['task'],[],req,stop=sharpen_zero(kind,c,a,n),existing=oldr['input']['existing_definitions']))
 # 48 genuine open-discovery teacher problems: no target formula in public task/input.
 for f in range(6):
  c,a,b,r,n=fixture('discovery_train_'+str(f),401+f,unknown=f==5)
  for k in range(8):
   id=f'train_B_{f:02d}_{k:02d}';schema_subsets=[{a:[n['start'],n['end']]},{a:[n['key'],n['tenant']],b:[n['key'],n['tenant'],n['meta']]},{a:[n['total'],n['part']]},{a:[n['key'],n['tenant']],b:[n['key'],n['tenant'],n['state'],n['part']]},{b:[n['meta'],n['state']]},{a:[n['key'],n['tenant']],b:[n['key'],n['tenant'],n['meta']]},{a:[n['ms'],n['sec']]},{a:[n['pk']]}]
   subset=schema_subsets[k];req={'kind':'discovery','supported':k!=7};task='Inspect the supplied physical metadata, profile and confirmed evidence. Propose up to two useful, nonduplicate derived metadata diagnostics with narrow meaning. No particular formula, column target or analysis method is prescribed. If unavailable, identify exact source/evidence/support limitations.';cs=[];stop='Narrow computed metadata candidates; business and online usefulness remain unconfirmed.'
   if k==0:
    d={'op':'datetime_diff','args':[col(a,n['end']),col(a,n['start'])],'unit':'ms'};cs=[candidate(c,id+'_c0',d,a,'Signed finish minus begin timestamp coordinates in ms; raw missingness, parsing failure or timezone incompatibility makes result unavailable, not verified business duration.'),candidate(c,id+'_c1',missing(d),a,'Boolean diagnostic for unavailable calculated timestamp coordinate difference; cause is not asserted to be raw input missingness.')]
   elif k==1:cs=[candidate(c,id+'_c0',cond(r,missing(col(b,n['meta']))),a,'Count of captured child rows with unavailable tag for each parent; complete physical capture only.')]
   elif k==2 and f!=5:cs=[candidate(c,id+'_c0',binary('subtract',col(a,n['total']),col(a,n['part'])),a,'Verified count quantity minus verified count portion; no consistency contract.'),candidate(c,id+'_c1',binary('divide',col(a,n['part']),col(a,n['total'])),a,'Verified count portion divided by verified count quantity; zero denominator unavailable.')]
   elif k==2:cs=[candidate(c,id+'_c0',missing(col(a,n['total'])),a,'Boolean diagnosis of unavailable raw numeric quantity; unknown physical unit is not required for this missingness calculation.')]
   elif k==3:cs=[candidate(c,id+'_c0',{'op':'child_sum','relationship_id':r,'where':binary('eq',col(b,n['state']),lit('A')),'value':col(b,n['part'])},a,'Sum of raw child numeric portion for literal code A; unit '+('unknown'if f==5 else 'verified count')+', missing selected numeric value propagates and empty group is zero.')]
   elif k==4:cs=[candidate(c,id+'_c0',binary('and',missing(col(b,n['meta'])),missing(col(b,n['state']))),b,'Boolean conjunction of unavailable child tag and unavailable stored code; false observations remain valid.')]
   elif k==5:cs=[candidate(c,id+'_c0',{'op':'parent_match_count','relationship_id':r},b,'Number of lexical composite-key physical parent matches per child, without mandatory-parent contract.'),candidate(c,id+'_c1',binary('divide',cond(r,missing(col(b,n['meta']))),count(r)),a,'Captured child tag-unavailability fraction per parent; zero children gives unavailable ratio.')]
   elif k==6 and f!=5:cs=[candidate(c,id+'_c0',binary('subtract',col(a,n['ms']),{'op':'convert_unit','args':[col(a,n['sec'])],'from_unit':'s','to_unit':'ms'}),a,'Verified numeric ms coordinate minus explicitly converted seconds coordinate; not synchronized business duration.')]
   elif k==6:cs=[candidate(c,id+'_c0',missing(col(a,n['ms'])),a,'Boolean unavailability of raw numeric coordinate; physical time unit remains unknown.')]
   else:stop=f'Only unique nonmissing row identifier {a}.{n["pk"]} is supplied; no time, numeric metadata or declared child relation is available. Raw identifier reading or relabeling its constant missingness offers no useful new metadata calculation.'
   rec=create(id,'discovery_train_'+str(f),'open_'+str(k),c,task,cs,req,stop=stop,subset=subset);train.append(rec)
 assert len(train)==160 and sum(r['task_kind']=='discovery'for r in train)==48
 write(TOP/'review/v4_teacher_full_reaudit.json',{'records':reaudit,'old144_rechecked':True,'previous_selected138_rechecked':True,'old_data_unmodified':True,'omitted_old_synthetic_families':[10,11,12,13],'new_train_count':160})
 return train

def actual_subset(c,cols,rel_ids=[]):
 inv=c['inventory'];wanted={table:set(columns)for table,columns in cols.items()}
 for t in inv['tables']:
  if t['name']in wanted:wanted[t['name']]|=set(t['primary_key'])
 for r in inv['relationships']:
  if r['id']in rel_ids:
   wanted.setdefault(r['from_table'],set()).update(r['from_columns']);wanted.setdefault(r['to_table'],set()).update(r['to_columns'])
 return {k:list(v)for k,v in wanted.items()}

def build_eval():
 c=actual_context(TOP/'fresh_actual/prepared',TOP/'contexts/evaluation_actual');rs=[]
 # Specified8: four real distinct scopes, four new physical controlled schemas.
 for k in range(4):
  id='eval_A_actual_'+str(k)
  if k==0:cat,task,cs=real_task(c,2,id);req=real_request(2,cs)
  elif k==1:
   task='For every captured message row, calculate lexical composite-key process parent match count on physical_M_to_P_retry; do not replace by process child volume.';cs=[candidate(c,id+'_c0',{'op':'parent_match_count','relationship_id':'physical_M_to_P_retry'},M,'Physical process parent matches per captured message using the supplied retry-linked relationship; no mandatory-parent business contract.')];req={'kind':'specified','supported':True,'grain_table':M,'relationship':'physical_M_to_P_retry','required_operations':['parent_match_count']};cat='child_parent_match'
  elif k==2:cat,task,cs=real_task(c,5,id);req=real_request(5,cs)
  else:
   cat,task,cs=real_task(c,0,id);task+=' Restrict transaction population to literal STATUS=S.';cs=[candidate(c,id+'_c0',cs[0]['plan']['expression'],T,cs[0]['meaning']+' Restricted parent STATUS=S.',where=binary('eq',col(T,'STATUS'),lit('S')))];req=real_request(0,cs,True)
  rrec=create(id,'fresh_v5_actual_128',cat,c,task,cs,req);rrec=trim_actual(rrec);rrec['messages'][0]['content']=SYSTEM;rs.append(rrec)
 for k in range(4):
  sc,a,b,r,n=fixture('v5_eval_A_'+str(k),701+k);id='eval_A_synthetic_'+str(k)
  if k==0:
   ex=binary('subtract',col(a,n['ms']),{'op':'convert_unit','args':[col(a,n['sec'])],'from_unit':'s','to_unit':'ms'});task='At each parent row subtract explicitly converted seconds coordinate from millisecond coordinate; preserve ms output and do not infer a business duration.';cs=[candidate(sc,id+'_c0',ex,a,'Verified numeric millisecond coordinate minus verified seconds coordinate explicitly converted to milliseconds; clock/business meaning unconfirmed.')];req={'kind':'specified','supported':True,'grain_table':a,'required_operations':['subtract','convert_unit']}
  elif k==1:
   cat,task,cs,_=synthetic_task(sc,a,b,r,n,3,id+'01');req=synthetic_request(3,cs,r)
  elif k==2:
   where=binary('eq',col(a,n['tenant']),lit('site1'));ex=cond(r,binary('eq',col(b,n['state']),lit('A')));task=f'Count captured child rows with literal {n["state"]}=A per parent, only parents with {n["tenant"]}=site1.';cs=[candidate(sc,id+'_c0',ex,a,'Physical code A child count per parent restricted to tenant site1; no business meaning.',where=where)];req={'kind':'specified','supported':True,'grain_table':a,'relationship':r,'required_operations':['conditional_count'],'population_filters':[{'table':a,'column':n['tenant'],'value':'site1'}]}
  else:
   ex={'op':'parent_match_count','relationship_id':r};task='Count physical parent matches for each child using the declared composite key; preserve child-row analysis unit.';cs=[candidate(sc,id+'_c0',ex,b,'Lexical composite-key physical parent match count per child; zero is not a verified contract violation.')];req={'kind':'specified','supported':True,'grain_table':b,'relationship':r,'required_operations':['parent_match_count']}
  rs.append(create(id,'v5_eval_A_'+str(k),'specified_synthetic_'+str(k),sc,task,cs,req))
 # Open8: broad metadata purpose only, no hidden target candidates or scoring conditions in inputs.
 old=read(ROOT/'data/eda/feature_rule_refinement_v3/20261013T175142+0900/data/feature_catalog64.json')
 for k in range(4):
  subsets=[actual_subset(c,{P:['START_TIME','END_TIME','DEPEND_PROCESS_ID']}),actual_subset(c,{T:['STATUS'],P:['ADAPTER_TYPE','CHANNEL_ID','DEPEND_PROCESS_ID']},['physical_P_to_T']),actual_subset(c,{P:[],M:['DIRECTION','DATA_NAME','DATA_TYPE']},['physical_M_to_P_retry']),actual_subset(c,{M:['DATA_NAME','DATA_TYPE','DIRECTION','START_TIME','END_TIME']})]
  subset=subsets[k];visible={(ta,z)for ta,cols in subset.items()for z in cols};exdefs=[]
  for f in old:
   p=f['plan'];deps={(n.get('table'),n.get('column'))for n in nodes(p['expression'])if n['op']=='column'};rr={n.get('relationship_id')for n in nodes(p['expression'])if 'relationship_id'in n};relok=rr<=({'physical_P_to_T'}if k==1 else {'physical_M_to_P_retry'}if k==2 else set())
   if p['grain']['table']in subset and deps<=visible and relok:exdefs.append({z:p[z]for z in ['expression','grain','population']})
  task='Inspect these captured metadata profiles and physical relationships and discover up to two nonduplicate diagnostic variables. No specific calculation or business outcome is prescribed. Existing definitions are only a deduplication reference; other supported analyses remain possible.'
  req={'kind':'discovery','supported':True};cs=[]
  # Private existence witnesses, not mandatory AST answers; broad valid alternatives will be accepted.
  if k==0:ex=missing({'op':'datetime_diff','args':[col(P,'END_TIME'),col(P,'START_TIME')],'unit':'ms'});tab=P;meaning='Boolean unavailability of computed signed process timestamp coordinate difference; not a raw-missingness cause assertion.'
  elif k==1:ex=cond('physical_P_to_T',missing(col(P,'DEPEND_PROCESS_ID')));tab=T;meaning='Count of captured process children with unavailable dependency metadata per transaction; not a dependency contract violation.'
  elif k==2:ex=binary('divide',cond('physical_M_to_P_retry',binary('and',binary('eq',col(M,'DIRECTION'),lit('OUT')),missing(col(M,'DATA_NAME')))),count('physical_M_to_P_retry'));tab=P;meaning='Fraction of captured messages with literal OUT and unavailable DATA_NAME per physical process; zero denominator unavailable.'
  else:ex=binary('and',missing(col(M,'DIRECTION')),missing(col(M,'DATA_NAME')));tab=M;meaning='Boolean simultaneous unavailability of DIRECTION and DATA_NAME metadata; false observations are valid.'
  cs=[candidate(c,'private_B_real_'+str(k),ex,tab,meaning)]
  rs.append(create('eval_B_actual_'+str(k),'fresh_v5_actual_128','open_actual_'+str(k),c,task,cs,req,existing=exdefs,subset=subset))
 for k in range(4):
  sc,a,b,r,n=fixture('v5_eval_B_'+str(k),801+k,unknown=k==3);id='eval_B_synthetic_'+str(k);task='Explore the supplied physical metadata, profiles and verified evidence for up to two nonduplicate diagnostic variables. No analysis formula, target column or method is prescribed. Use narrow arithmetic meaning and identify unverified units/business interpretation.'
  if k==0:subset={a:[n['start'],n['end']]};delta={'op':'datetime_diff','args':[col(a,n['end']),col(a,n['start'])],'unit':'ms'};cs=[candidate(sc,'private_B_syn_0',delta,a,'Signed timestamp coordinate difference in milliseconds, not verified business processing duration.')]
  elif k==1:subset={a:[n['key'],n['tenant']],b:[n['key'],n['tenant'],n['meta'],n['state']]};cs=[candidate(sc,'private_B_syn_1',cond(r,binary('and',binary('eq',col(b,n['state']),lit('A')),missing(col(b,n['meta'])))),a,'Count of captured child rows with literal code A and unavailable metadata tag; no business outcome meaning.')]
  elif k==2:subset={a:[n['total'],n['part'],n['ms'],n['sec']]};cs=[candidate(sc,'private_B_syn_2',binary('divide',col(a,n['part']),col(a,n['total'])),a,'Ratio of verified count portion to verified count quantity; denominator zero remains unavailable.')]
  else:subset={b:[n['meta'],n['state'],n['part']]};cs=[candidate(sc,'private_B_syn_3',binary('and',missing(col(b,n['meta'])),missing(col(b,n['part']))),b,'Boolean simultaneous unavailability of categorical tag and raw numeric portion; no physical numeric unit is required.')]
  rs.append(create(id,'v5_eval_B_'+str(k),'open_synthetic_'+str(k),sc,task,cs,{'kind':'discovery','supported':True},subset=subset))
 # Four blocked specified purposes, real2/synthetic2; zero must identify exact reason.
 for k in range(2):
  cat,task,cs=real_task(c,6+k,'eval_zero_actual_'+str(k));stop=['EC_TRANSACTION_MONITORING.PROCESS_TIME has unknown physical unit and unverified duration meaning; no millisecond latency can be justified.','abs is unsupported for EC_PROCESS_MONITORING END_TIME minus START_TIME; signed difference would replace the requested absolute value.'][k];rr=create('eval_zero_actual_'+str(k),'fresh_v5_actual_128',cat,c,task,[],real_request(6+k,[]),stop=stop);rs.append(trim_actual(rr))
 for k in range(2):
  sc,a,b,r,n=fixture('v5_eval_zero_'+str(k),901+k,unknown=True);id='eval_zero_synthetic_'+str(k)
  if k==0:task=f'Compute a physically interpreted count ratio {a}.{n["part"]}/{a}.{n["total"]}; the declared units are unknown and no raw-number substitute is requested.';stop=sharpen_zero(0,sc,a,n);terms=['unit']
  else:task=f'On each child row compute its parent {a}.{n["total"]} value minus child {b}.{n["part"]}; no aggregation or alternative target allowed.';stop='Parent attribute lookup into child-row expressions is not supported. The declared relationship enables parent_match_count, not reading a parent numeric column at child grain. Replacing this with parent child_sum changes the request.';terms=['parent','lookup']
  rs.append(create(id,'v5_eval_zero_'+str(k),'blocked_'+str(k),sc,task,[],{'kind':'specified','supported':False,'zero_reason_terms':terms},stop=stop))
 assert len(rs)==20
 for r in rs:r['development_status']='frozen_v5_eval_not_training';r['messages'][0]['content']=SYSTEM
 return rs

def main():
 train=build_training();evals=build_eval();jl(DATA/'train.jsonl',train);jl(DATA/'eval_private.jsonl',evals)
 jl(DATA/'eval_inputs.jsonl',[{k:v for k,v in r.items()if k not in ['target','review','private_request_check']}|{'messages':r['messages'][:2]}for r in evals])
 tok=AutoTokenizer.from_pretrained(read(ROOT/'local_model/model_manifest.json')['snapshot_path'],local_files_only=True);lengths=[]
 for r in train+evals:
  p=tok(tok.apply_chat_template(r['messages'][:2],tokenize=False,add_generation_prompt=True,enable_thinking=False),add_special_tokens=False)['input_ids'];a=tok(r['messages'][2]['content']+tok.eos_token,add_special_tokens=False)['input_ids'];lengths.append({'id':r['id'],'input':len(p),'answer':len(a),'total':len(p+a)})
 maxtrain=max(x['total']for x in lengths if x['id'].startswith('train'));assert maxtrain<=4096,('too_long',maxtrain);assert max(x['input']for x in lengths)+2048<=16384
 write(DATA/'length_preflight.json',{'max_training':maxtrain,'max_length':4096,'response_tokens':2048,'truncation':0,'items':lengths})
 rng=random.Random(57);order=list(range(len(train)));rng.shuffle(order);order+=rng.sample(list(range(len(train))),240-len(train));schedule=[{'id':train[j]['id'],'record_sha':digest(train[j]),'answer_sha':digest(train[j]['target']),'category':train[j]['category']}for j in order];write(DATA/'training_schedule.json',schedule)
 approvals=[{'id':r['id'],'task':r['task_kind'],'family':r['family'],'input_sha':digest(r['input']),'answer_sha':digest(r['target']),'record_sha':digest(r),'source_context':r['source_context'],'approved':True,'format_pass':True,'measurement_pass':True,'independent_math_pass':True,'request_meaning_review':'Root checked narrow request and expression prior to SFT','previous_record':r.get('previous_record'),'teacher_source':'assistant_authored_silver'}for r in train]
 write(TOP/'teacher_preapproval.json',{'approved':approvals,'train_count':160,'specified':112,'open_discovery':48,'candidate_count_distribution':dict(Counter(len(r['target']['candidates'])for r in train)),'old144_blind_reuse':False,'Rule244_used':False,'FRESH23_2_3_used':False,'evaluation_in_training':False,'errors_corrected':['train_actual_12/13 parent-filter unsupported: precise zero','train_actual_6/7/14/15 precise unit/operator reason','plural specified tasks restored complete requested list'],'meaning_review_is_assistant_silver_not_business_gold':True})
 write(TOP/'evaluation_policy.json',{'version':'v5-request-discovery-2.0','groups':{'specified':8,'open_discovery':8,'limits':4},'first_only':True,'repairs':0,'max_candidates':2,'same_v4_v5_prompt':True,'NF4':True,'greedy':True,'thinking':False,'grammar':False,'max_new_tokens':2048,'allow_alternative_valid_calculations':True,'private_witness_AST_not_required_for_open_discovery':True,'scope_filter_grain_relationship_preserved':True,'no_unknown_business_contract_or_units':True,'operational_usefulness_not_inferred_from_measurement':True,'synthetic_control_separate_actual':True})
 write(TOP/'evaluation_freeze.json',{'pins':[pin(p)for p in [DATA/'train.jsonl',DATA/'eval_inputs.jsonl',DATA/'eval_private.jsonl',DATA/'training_schedule.json',TOP/'evaluation_policy.json',TOP/'teacher_preapproval.json']]+[pin(p)for p in (TOP/'contexts').rglob('*')if p.is_file()]+[pin(p)for p in (TOP/'fixtures').rglob('*')if p.is_file()],'maximum_steps':60,'maximum_responses':120,'v4_adapter':pin(V4/'training/adapter_final/adapter_model.safetensors'),'Rule_adapter_fixed':pin(PARENT/'training/adapter_final/adapter_model.safetensors')})
 print(json.dumps({'train':len(train),'open_discovery':48,'eval':20,'max_training':maxtrain}))
if __name__=='__main__':main()
