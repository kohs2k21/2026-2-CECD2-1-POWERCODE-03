"""Assistant-authored feature silver, bounded real/synthetic source replay, freeze."""
from feature_contract import *
import random, csv
from generalized_profile_v16 import build_generalized_profile
from generalized_source_policy_v21 import register_source
from transformers import AutoTokenizer

def col(t,n):return {'op':'column','table':t,'column':n}
def lit(v):return {'op':'literal','value':v}
def binary(op,a,b):return {'op':op,'args':[a,b]}
def missing(a):return {'op':'is_missing','args':[a]}
def count(r):return {'op':'child_count','relationship_id':r}
def cond(r,p):return {'op':'conditional_count','relationship_id':r,'where':p}

def fixture(family,seed,unknown=False):
 out=TOP/'fixtures'/family;out.mkdir(parents=True,exist_ok=True);a='parent_'+family;b='child_'+family;r='link_'+family
 names={'pk':'row_'+str(seed),'key':'ref_'+str(seed),'tenant':'tenant_'+str(seed),'start':'begin_'+str(seed),'end':'finish_'+str(seed),'total':'quantity_'+str(seed),'part':'portion_'+str(seed),'state':'code_'+str(seed),'meta':'tag_'+str(seed),'ms':'coord_ms_'+str(seed),'sec':'coord_s_'+str(seed)}
 declarations=[];facts=[]
 for table,cols in [(a,[(names['pk'],'VARCHAR2'),(names['key'],'VARCHAR2'),(names['tenant'],'VARCHAR2'),(names['start'],'TIMESTAMP'),(names['end'],'TIMESTAMP'),(names['total'],'NUMBER'),(names['part'],'NUMBER'),(names['ms'],'NUMBER'),(names['sec'],'NUMBER')]),(b,[(names['pk'],'VARCHAR2'),(names['key'],'VARCHAR2'),(names['tenant'],'VARCHAR2'),(names['state'],'VARCHAR2'),(names['meta'],'VARCHAR2'),(names['part'],'NUMBER')])]:
  cs=[]
  for n,typ in cols:
   u='count'if n in [names['total'],names['part']]else 'ms'if n==names['ms']else 's'if n==names['sec']else None
   if unknown:u=None
   x={'name':n,'sql_type':typ,'nullable':n!=names['pk'],'missing_tokens':[''],'content_class':'metadata','description':'Synthetic physical declaration; no operational/business interpretation'}
   if u:
    ix=len(facts);facts.append({'table':table,'column':n,'unit':u});eid='unit_'+family+'_'+str(ix);x['unit']={'name':u,'status':'verified','evidence_ids':[eid]}
   cs.append(x)
  declarations.append({'id':table,'primary_key':[names['pk']],'columns':cs})
 factpath=out/'units.json';write(factpath,{'facts':facts,'source_kind':'controlled synthetic unit contract'})
 evidence=[{'id':'unit_'+family+'_'+str(j),'kind':'unit_contract','target':{'table':f['table'],'column':f['column']},'value':f,'source':{**pin(factpath),'pointer':'/facts/'+str(j)}}for j,f in enumerate(facts)]
 schema={'dataset_id':family,'tables':declarations,'relationships':[{'id':r,'from_table':b,'from_columns':[names['tenant'],names['key']],'to_table':a,'to_columns':[names['tenant'],names['key']]}],'evidence':evidence}
 write(out/'schema.json',schema);rng=random.Random(seed);parents=[];children=[]
 for i in range(32):
  start='2026-08-12T00:00:'+str(i).zfill(2);end='2026-08-12T00:01:'+str(i).zfill(2)
  if i%9==0:end='2026-08-11T23:59:'+str(i).zfill(2)
  if i%13==0:end=''
  if i==7:end='invalid_timestamp'
  if i==8:end+='Z'
  total=0 if i%7==0 else rng.randrange(2,20);part=rng.randrange(0,total+1)
  row={names['pk']:'p'+str(i),names['key']:'k'+str(i),names['tenant']:'site'+str(i%2),names['start']:start,names['end']:end,names['total']:str(total),names['part']:str(part),names['ms']:str(1000+i*seed),names['sec']:str(1+i/10)};parents.append(row)
  for j in range((i+seed)%4):children.append({names['pk']:f'c{i}_{j}',names['key']:row[names['key']],names['tenant']:row[names['tenant']],names['state']:'A'if (i+j)%2==0 else 'B',names['meta']:''if (i+j)%5==0 else 'tag'+str((i+j)%3),names['part']:''if (i+j)%11==0 else str((i+j+seed)%5)})
 children.append({names['pk']:'orphan',names['key']:'absent',names['tenant']:'site0',names['state']:'A',names['meta']:'',''+names['part']:'1'})
 paths={}
 for table,data in [(a,parents),(b,children)]:
  path=out/(table+'.csv');paths[table]=path
  if not path.exists():
   with path.open('x',newline='')as stream:w=csv.DictWriter(stream,fieldnames=data[0].keys());w.writeheader();w.writerows(data)
 policy={'version':'source-admission-1.0.0','default_unknown':'hold','nodes':[]}
 for path in [factpath,out/'schema.json']+list(paths.values()):policy=register_source(policy,path,kind='synthetic',reason='v4 explicit controlled fixture; not operating observations',source_family=family)
 write(out/'source_policy_r02.json',policy)
 inv,receipt=guarded('profile',policy,lambda:build_generalized_profile(out/'schema.json',paths,max_rows=1000),pins=[pin(x)for x in [factpath,out/'schema.json']+list(paths.values())]);write(out/'inventory.json',inv)
 tm={t['name']:t for t in inv['tables']};rd={k:inv['relationships'][0][k]for k in ['id','from_table','from_columns','to_table','to_columns','no_reference_values']}
 capture={r:{'relationship_id':r,'status':'complete_for_selected_parents','child_count_allowed':True,'schema_version':inv['schema_version'],'relationship_definition':rd,'relationship_definition_sha':digest(rd),'parent_source_sha256':tm[a]['csv_source']['sha256'],'child_source_sha256':tm[b]['csv_source']['sha256']}}
 write(out/'capture.json',capture);scope={'source_kind':'controlled_synthetic','family':family,'selection':'all fixture rows','table_rows':{a:len(parents),b:len(children)},'period':{'start_inclusive':'2026-08-12','end_exclusive':'2026-08-13','timezone':'synthetic coordinate only'},'source_pins':inv['sources'],'business_meaning_verified':False}
 c={'path':out,'inventory':inv,'policy':policy,'capture':capture,'scope':scope};write(out/'context.json',{'inventory':pin(out/'inventory.json'),'policy':pin(out/'source_policy_r02.json'),'capture':pin(out/'capture.json'),'scope':scope});return c,a,b,r,names

def load_context(p):
 p=Path(p);v=read(p/'context.json')
 for k in ['inventory','policy','capture']:assert pin(ROOT/v[k]['path'])==v[k]
 return {'path':p,'inventory':read(ROOT/v['inventory']['path']),'policy':read(ROOT/v['policy']['path']),'capture':read(ROOT/v['capture']['path']),'scope':v['scope']}

def synthetic_task(c,a,b,r,n,k,id):
 delta={'op':'datetime_diff','args':[col(a,n['end']),col(a,n['start'])],'unit':'ms'};selector=binary('eq',col(b,n['state']),lit('A'))
 specs=[
 ('time_coordinates','Define signed end minus begin timestamp coordinates in ms and optionally diagnose unavailable computed output; do not claim business latency.',[(delta,a,'Signed end minus begin timestamp coordinates in ms; unavailable input, parse or timezone mismatch propagates.'),(missing(delta),a,'Boolean indicating unavailable calculated coordinate difference; not specifically raw missingness.')]),
 ('child_counts','Describe child row volume and child rows with literal code A per parent using the declared complete physical relationship.',[(count(r),a,'Number of captured physical child rows per uniquely matched parent.'),(cond(r,selector),a,'Number of captured child rows whose stored code equals literal A per parent; no outcome semantics.')]),
 ('verified_quantities','Define the difference and ratio of the declared count quantities; denominator zero is unavailable.',[(binary('subtract',col(a,n['total']),col(a,n['part'])),a,'Declared count quantity minus declared count portion; no business consistency contract.'),(binary('divide',col(a,n['part']),col(a,n['total'])),a,'Ratio of declared count portion to declared count quantity; zero denominator is unavailable.')]),
 ('child_quantity_sum','Aggregate the verified child count quantity where code A; propagate unavailable selected quantity.',[({'op':'child_sum','relationship_id':r,'where':selector,'value':col(b,n['part'])},a,'Sum of verified child count quantities for literal code A; empty selected child set gives zero, missing selected value propagates.')]),
 ('missing_parent','Describe unavailable metadata and physical parent matching; do not invent mandatory-parent contracts.',[(missing(col(b,n['meta'])),b,'Boolean indicating unavailable categorical tag value.'),({'op':'parent_match_count','relationship_id':r},b,'Number of lexical composite-key parent matches for each child; no matches is a physical observation, not a contract violation.')]),
 ('joint_category_frequency','Define captured-scope frequency of the joint stored code and tag tuple on child rows.',[({'op':'group_count','keys':[n['state'],n['meta']],'where':None},b,'Captured-scope row count sharing the same code and tag tuple; unavailable grouping key yields unavailable, not distinct category count.')]),
 ('conditional_ratio','Define the fraction of captured children whose metadata tag is unavailable per parent.',[(binary('divide',cond(r,missing(col(b,n['meta']))),count(r)),a,'Fraction of captured child rows with unavailable tag per parent; no children gives unavailable ratio.')]),
 ('duplicate_alternative','Existing coverage already includes total child count. Avoid repeating it and propose a different metadata analysis.',[(cond(r,binary('and',selector,missing(col(b,n['meta'])))),a,'Count of captured child rows with literal code A and unavailable tag per parent.')])]
 cat,task,sp=specs[k];sp=sp[:1]if (int(id[-2:])%3==0 and len(sp)==2)else sp
 cs=[candidate(c,id+'_c'+str(j),ex,t,meaning)for j,(ex,t,meaning)in enumerate(sp)]
 existing=[]
 if k==7:existing=[{'expression':count(r),'grain':cs[0]['plan']['grain'],'population':{'table':a,'filter':None}}]
 return cat,task,cs,existing

def projection(c,task,existing=None,feedback=None):
 i=input_projection(c,task,existing,feedback)
 scope=c['scope'];cap=scope.get('capture',{})
 i['source_context']={k:scope[k]for k in ['source_kind','family','period','table_rows']if k in scope}
 if cap:i['source_context'].update({k:cap[k]for k in ['transaction_families','table_rows','selection_windows','manifest','source_version']})
 # Keep computational fields; do not repeatedly copy capture proofs or generic prose.
 i['capture']={k:{z:(v.get('value')or v).get(z)for z in ['status','child_count_allowed','relationship_definition_sha']}for k,v in c['capture'].items()}
 for t in i['tables']:
  for x in t['columns']:
   x.pop('description',None);summary=x['statistics'].get('numeric_summary')
   if isinstance(summary,dict):x['statistics']['numeric_summary']={k:summary.get(k)for k in ['min','max','mean']}
  meta=next(z for z in c['inventory']['tables']if z['name']==t['table'])
  with (ROOT/meta['csv_source']['path']).open(encoding='utf-8-sig',newline='')as stream:data=list(csv.DictReader(stream))
  for x in t['columns']:
   if x['type']=='category' or (x['type']in ['string']and x['name']not in t['primary_key']):
    counts=Counter(row[x['name']]for row in data);x['observed_literal_codes']=dict(counts)if len(counts)<=12 else {'distinct':len(counts)}
 return i

def make_record(id,family,cat,c,task,cs,existing=None,feedback=None,stop='Bounded requested analysis covered; operational usefulness and online availability remain unconfirmed.'):
 inp=projection(c,task,existing,feedback);target={'candidates':cs,'stop_reason':stop};checked=evaluate_candidates(c,target);assert checked['JSON_contract'] and all(x['measurement_completed']and x['independent']for x in checked['candidates']),checked
 admission(c,'teacher');scope=c['scope'];pinctx=pin(c['path']/'context.json')
 rec={'id':id,'family':family,'category':cat,'teacher_source':'assistant_authored_silver','development_status':'development_silver','input':inp,'target':target,'source_context':pinctx,'source_scope':scope,'review':{'format':True,'code_measurement':True,'independent_calculation':True,'direct_meaning':'assistant authored exact narrow template checked against AST; not independent business gold','zero_reason':stop if not cs else None},'messages':[{'role':'system','content':FEATURE_SYSTEM},{'role':'user','content':json.dumps(inp,separators=(',',':'),ensure_ascii=False)},{'role':'assistant','content':json.dumps(target,separators=(',',':'),ensure_ascii=False)}]}
 if scope['source_kind']=='approved_actual_capture':rec=trim_actual(rec)
 write(TOP/'teacher_validation_r06'/(id+'.json'),{'id':id,'scope':scope,'input_sha':digest(rec['input']),'target_sha':digest(target),'checks':checked,'review':rec['review']});return rec

def actual_context(path,dest):
 c=load_capture(path);c['scope']={'source_kind':'approved_actual_capture','capture':capture_proof(str(c['path'].resolve()))};dest.mkdir(parents=True,exist_ok=True)
 for k,file in [('inventory','inventory.json'),('policy','source_policy.json'),('capture','capture.json')]:write(dest/file,c[k])
 write(dest/'context.json',{k:pin(dest/file)for k,file in [('inventory','inventory.json'),('policy','source_policy.json'),('capture','capture.json')]}|{'scope':c['scope'],'actual_capture_path':str(c['path'].relative_to(ROOT))});c['path']=dest;return c

def real_task(c,k,id):
 t='EC_TRANSACTION_MONITORING';p='EC_PROCESS_MONITORING';m='EC_MESSAGE_MONITORING';r='physical_M_to_T';s=col(m,'DIRECTION');dt=col(m,'DATA_TYPE');r2='physical_P_to_T';ratio=binary('divide',cond(r,binary('and',binary('eq',s,lit('IN')),missing(col(m,'DATA_NAME')))),count(r))
 specs=[
 ('real_conditional_metadata','Count captured message children whose DATA_NAME is unavailable per transaction; stored metadata diagnosis only.',[(cond(r,missing(col(m,'DATA_NAME'))),t,'Number of captured message children with unavailable categorical DATA_NAME per transaction.')]),
 ('real_joint_categories','Count message children with literal DIRECTION OUT and DATA_TYPE UNKNOWN per transaction; do not interpret the codes.',[(cond(r,binary('and',binary('eq',s,lit('OUT')),binary('eq',dt,lit('UNKNOWN')))),t,'Count of captured message children whose DIRECTION equals OUT and DATA_TYPE equals UNKNOWN; literal code conjunction only.')]),
 ('real_time_aggregation','Aggregate signed process end minus start timestamp coordinates in ms per transaction; not business elapsed time.',[({'op':'child_sum','relationship_id':r2,'where':None,'value':{'op':'datetime_diff','args':[col(p,'END_TIME'),col(p,'START_TIME')],'unit':'ms'}},t,'Sum of signed process END_TIME minus START_TIME coordinates in ms for captured process children; not verified business duration.')]),
 ('real_missing_ratio','Compute fraction of captured message children with literal IN and unavailable DATA_NAME per transaction.',[(ratio,t,'Fraction of captured message children with DIRECTION literal IN and unavailable DATA_NAME; denominator zero stays unavailable.')]),
 ('real_joint_frequency','Define captured-scope frequency of joint message DIRECTION and DATA_TYPE tuple.',[({'op':'group_count','keys':['DIRECTION','DATA_TYPE'],'where':None},m,'Captured-scope count of message rows sharing the same DIRECTION and DATA_TYPE tuple, not distinct categories or live reference frequency.')]),
 ('real_combined_missing','Check simultaneous unavailability of message DATA_NAME and DIRECTION.',[(binary('and',missing(col(m,'DATA_NAME')),missing(s)),m,'Boolean indicating both DATA_NAME and DIRECTION are unavailable; no arithmetic missing-count claim.')]),
 ('unknown_units','Requested purpose: derive physical millisecond latency from PROCESS_TIME numeric metadata. Its raw unit and business duration contract are unconfirmed; do not substitute a different task.',[]),
 ('unsupported_absolute','Requested purpose: one absolute value of signed process END_TIME minus START_TIME for all process rows. abs is unsupported; do not silently replace by signed difference or split population.',[])]
 cat,task,sp=specs[k];cs=[candidate(c,id+'_c'+str(j),ex,tab,meaning)for j,(ex,tab,meaning)in enumerate(sp)];return cat,task,cs

def trim_actual(rec):
 # All retained fields are verified inventory metadata. Body never projected.
 cs=rec['target']['candidates'];keep=set(tuple(z)for x in cs for z in x['sources']['columns']);tables=set(x['plan']['grain']['table']for x in cs);rels=set(z for x in cs for z in x['sources']['relationships'])
 if not cs:
  tab='EC_TRANSACTION_MONITORING'if rec['category']=='unknown_units'else 'EC_PROCESS_MONITORING';tables.add(tab);keep.update((tab,n)for n in (['PROCESS_TIME']if rec['category']=='unknown_units'else ['START_TIME','END_TIME']))
 tables|={z[0]for z in keep}
 rec['input']['tables']=[t for t in rec['input']['tables']if t['table']in tables]
 for t in rec['input']['tables']:t['columns']=[x for x in t['columns']if (t['table'],x['name'])in keep or x['name']in t['primary_key']]
 rec['input']['relationships']=[r for r in rec['input']['relationships']if r['id']in rels];rec['input']['capture']={k:v for k,v in rec['input']['capture'].items()if k in rels}
 rec['messages'][1]['content']=json.dumps(rec['input'],separators=(',',':'),ensure_ascii=False);return rec

def prepare():
 train=[];evals=[];private=[]
 for f in range(14):
  family='train_synthetic_'+str(f);c,a,b,r,n=fixture(family,41+f)
  for k in range(8):
   id=f'train_syn_{f:02d}_{k:02d}';cat,task,cs,existing=synthetic_task(c,a,b,r,n,k,id);feedback=None
   if f%3==0:
    bad=deepcopy(cs[0]);mode=k%8
    if mode==0:bad['plan']['expression']['op']='abs'
    elif mode==1:bad['expected_type']='count'
    elif mode==2:bad['expected_unit']='null'
    elif mode==3:bad['plan']['expression']['value']=binary('subtract',missing(col(b,n['meta'])),lit(0))
    elif mode==4:bad['plan']['expression']=missing(col(b,'nonexistent_column'))
    elif mode==5:bad['plan']['missing_policy']['empty_group']=None
    elif mode==6:bad['meaning']='Verified business failure rate and synchronized processing latency'
    else:bad['plan']['expression']=count(r);bad['meaning']='Original target silently replaced by total child count'
    errors=[{'path':['plan.expression','expected_type','expected_unit','plan.expression.value','plan.expression.args[0].column','plan.missing_policy.empty_group','meaning','plan.expression'][mode],'actual':'See failed_candidate at the specified field','required':'Preserve requested narrow calculation, supported AST, exact sources and inferred type/unit; no invented business meaning or silent replacement.'}]
    feedback={'failed_candidate':bad,'errors':errors,'scope':'only failed candidate; preserve original analysis target; corrected output is not a new independent discovery'}
    cs=cs[:1];task+=' This is a partial repair: return only the corrected failed candidate; do not regenerate unaffected candidates.'
    cat='repair_'+cat
   train.append(make_record(id,family,cat,c,task,cs,existing,feedback))
 # Real existing development capture, not the fresh evaluation.
 rc=actual_context(PARENT/'captures/evaluation/prepared',TOP/'contexts/training_actual')
 for j in range(16):
  k=j%8;cat,task,cs=real_task(rc,k,'train_actual_'+str(j));
  if j>=8:
   # Deliberate narrower population contrast, not an independently new source family.
   if cs:
    for x in cs:
     if x['plan']['grain']['table']=='EC_TRANSACTION_MONITORING':
      x['plan']['population']['filter']=binary('eq',col('EC_TRANSACTION_MONITORING','STATUS'),lit('S'));v=Engine(rc['inventory'],rc['policy'],rc['capture']).validate(x['plan']);assert v['passed'];x['sources']={'columns':v['identity']['source_columns'],'relationships':v['identity']['relationships']}
    task+=' Limit parent population to stored STATUS literal S; no business meaning.'
   else:task+=' Return the exact unsupported/unknown limitation, not a replacement.'
  rec=make_record('train_actual_'+str(j),'known_actual_v2_128',cat,rc,task,cs,stop=('Requested unit or operation cannot be established from the current contract; no candidate.'if not cs else 'Requested narrow calculation only; operational usefulness unconfirmed.'));train.append(trim_actual(rec))
 # Sixteen explicit negative tasks, linked to same family as any source variants.
 for j in range(16):
  c,a,b,r,n=fixture('negative_'+str(j),101+j,unknown=j%4==0)
  kind=j%4
  task=['Compute a physically interpreted count ratio from quantity and portion with unknown units; do not propose a raw-number alternative.','Compute abs of signed end-begin for the entire parent population as one variable; abs unsupported and no task substitution.','Compute a derived numeric feature from table ABSENT_TABLE column absent_qty; no other source permitted.','Only repeat the already registered total child count with a different ID. No alternative target is requested.'][kind]
  stop=['Required quantity unit contracts are missing; raw arithmetic is possible but the requested physically interpreted ratio is not justified.','Absolute-value operation is not supported. The purpose can be legitimate but is not expressible here without changing it.','Required physical table and column do not exist in this inventory.','The requested definition is already registered; a renamed duplicate is not new.'][kind]
  existing=[{'expression':count(r),'grain':{'table':a,'keys':[n['pk']]},'population':{'table':a,'filter':None}}]if kind==3 else []
  train.append(make_record('train_zero_'+str(j),'negative_'+str(j),'zero_'+str(kind),c,task,[],existing,stop=stop))
 assert len(train)==144
 fresh=actual_context(TOP/'fresh_actual/prepared',TOP/'contexts/evaluation_actual')
 for k in range(8):
  cat,task,cs=real_task(fresh,k,'eval_real_'+str(k));rec=trim_actual(make_record('eval_real_'+str(k),'fresh_actual_128',cat,fresh,task,cs,stop='Required unit evidence or abs operation is unavailable; no candidate.'if not cs else 'Narrow captured calculation only.'));rec['development_status']='frozen_same_company_unused_families';evals.append(rec)
 for j in range(8):
  c,a,b,r,n=fixture('eval_new_'+str(j),211+j,unknown=j==6)
  if j==6:cat='zero_unknown_unit';task='Compute physically comparable count ratio from the quantity and portion. Their units are unconfirmed; no alternative is requested.';cs=[];existing=[]
  elif j==7:cat='unsupported';task='Define abs of signed timestamp coordinates for all parents. abs is unsupported; do not change target.';cs=[];existing=[]
  elif j==5:
   cat='unit_conversion';task='Compute numeric ms coordinate minus converted numeric seconds coordinate in ms; explicit unit conversion required; not business duration.';cs=[candidate(c,'eval_new_5_c0',binary('subtract',col(a,n['ms']),{'op':'convert_unit','args':[col(a,n['sec'])],'from_unit':'s','to_unit':'ms'}),a,'Verified millisecond numeric coordinate minus seconds numeric coordinate explicitly converted to milliseconds; not business elapsed time.')];existing=[]
  else:cat,task,cs,existing=synthetic_task(c,a,b,r,n,j,'eval_new_'+str(j)+'00')
  rec=make_record('eval_syn_'+str(j),'eval_new_'+str(j),cat,c,task,cs,existing,stop='Missing physical unit contract or unsupported requested operation; no candidate.'if not cs else 'Narrow synthetic calculation only.');rec['development_status']='frozen_new_physical_synthetic';evals.append(rec)
 assert len(evals)==16
 # Teacher and evaluation gold are physically separate; evaluation answers never training inputs.
 jl(TOP/'data_r04/train.jsonl',train);jl(TOP/'data_r04/eval_private.jsonl',evals);jl(TOP/'data_r04/eval_inputs.jsonl',[{k:v for k,v in r.items()if k not in ['target','review']}|{'messages':r['messages'][:2]}for r in evals])
 tok=AutoTokenizer.from_pretrained(read(ROOT/'local_model/model_manifest.json')['snapshot_path'],local_files_only=True);lengths=[]
 for r in train+evals:
  prefix=tok(tok.apply_chat_template(r['messages'][:2],tokenize=False,add_generation_prompt=True,enable_thinking=False),add_special_tokens=False)['input_ids'];ans=tok(r['messages'][2]['content']+tok.eos_token,add_special_tokens=False)['input_ids'];lengths.append({'id':r['id'],'input':len(prefix),'answer':len(ans),'total':len(prefix)+len(ans)})
 maxtrain=max(x['total']for x in lengths if x['id'].startswith('train'));assert maxtrain<=4096,('train_too_long',maxtrain)
 assert max(x['input']for x in lengths)+3072<=16384
 write(TOP/'data_r04/length_preflight.json',{'train_max':maxtrain,'rows':lengths,'training_max_length':4096,'generation_max_new_tokens':2048,'no_truncation':True})
 rng=random.Random(47);order=list(range(144));rng.shuffle(order);order+=rng.sample(list(range(144)),96);schedule=[{'id':train[j]['id'],'record_sha':digest(train[j]),'answer_sha':digest(train[j]['target']),'category':train[j]['category']}for j in order];write(TOP/'data_r04/training_schedule.json',schedule)
 write(TOP/'teacher_curation.json',{'source':'assistant_authored_silver','train':144,'categories':dict(Counter(r['category']for r in train)),'candidate_count_distribution':dict(Counter(len(r['target']['candidates'])for r in train)),'positive_candidates':sum(len(r['target']['candidates'])for r in train),'code_and_independent_measured':True,'meaning_review':'narrow arithmetic and physical fixture only; assistant direct template review','same_family_variants_not_independent':True,'Rule244_reused_as_answers':False,'FRESH23_2_3_in_train':False,'evaluation_gold_in_train':False,'existing_rule_teacher_selection_unchanged':True})
 write(TOP/'evaluation_policy.json',{'version':'feature-frozen-eval-1.0.0','first_outputs_only':True,'repairs':0,'max_candidates':2,'no_exact_gold_ID_requirement':True,'zero_allowed':['explicit unknown physical quantity units','missing required source','unsupported requested purpose','requested duplicate only'],'nonzero_coverage_required_for_supported_requested_analysis':True,'metrics':['JSON','AST','physical_source','dtype_unit','measurement','independent_values','meaning','unknown_handling','definition_duplicate','new_narrow_meaning','current_variation','availability','operational_usefulness_unknown'],'forbidden_claims':['business meaning of literal codes','clock synchronization','verified lifecycle duration','fault labels','raw numeric unit invented'],'gold_input_leakage':False,'classification':'same-company unused families and synthetic new physical schema, not independent enterprise generalization'})
 frozen=[pin(x)for x in [TOP/'data_r04/train.jsonl',TOP/'data_r04/eval_private.jsonl',TOP/'data_r04/eval_inputs.jsonl',TOP/'data_r04/training_schedule.json',TOP/'evaluation_policy.json',TOP/'teacher_curation.json']];frozen+=[pin(x)for x in (TOP/'fixtures').rglob('*')if x.is_file()];frozen+=[pin(x)for x in (TOP/'contexts').rglob('*')if x.is_file()]
 write(TOP/'evaluation_freeze.json',{'pins':frozen,'SFT_experiments':1,'optimizer_steps':60,'generation_allocation':{'frozen_base':16,'frozen_feature_adapter':16,'reload':1,'development_initial':12,'development_repair':12,'rule_link':12,'representative_replay':4,'unallocated_reserve':47},'global_max_responses':120,'Rule_adapter_fixed':pin(PARENT/'training/adapter_final/adapter_model.safetensors')})
 print(json.dumps({'train':144,'eval':16,'train_max_tokens':maxtrain,'candidates':sum(len(r['target']['candidates'])for r in train)}))

if __name__=='__main__':prepare()
