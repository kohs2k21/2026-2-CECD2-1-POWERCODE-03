"""Feature-only bounded contract. Compiler is the unchanged calculation3.0.4."""
from policy_v3 import *
from independent_candidates import verify_candidates
VERSION='feature-silver-1.0.0'
OPS={'column':0,'literal':0,'is_missing':1,'subtract':2,'divide':2,'eq':2,'lt':2,'lte':2,'gt':2,'gte':2,'and':2,'datetime_diff':2,'convert_unit':1,'child_count':0,'parent_match_count':0,'conditional_count':0,'child_sum':0,'group_count':0}
FEATURE_SYSTEM='''Design derived metadata FEATURES, not HR/SR rules. Return JSON only {"candidates":[...],"stop_reason":"..."}, zero to two candidates. Each candidate has exactly id,purpose,meaning,sources,expected_type,expected_unit,availability,required_evidence,plan. sources={"columns":[[table,column],...],"relationships":[id,...]} includes every computational dependency including relationship keys. expected_type is integer/number/boolean/category/timestamp. expected_unit is count/ratio/boolean/ms/s/us or JSON null (never the string "null"). availability="offline_captured_scope". required_evidence lists unresolved needs; do not invent business facts. plan={id,description,expression,grain:{table,keys},population:{table,filter},group_by:[],schema_version,source_version,missing_policy:{input,empty_group,denominator_zero,child_missing,unknown_predicate}}. Copy supplied versions and exact primary keys. column AST={op:"column",table,column}; literal={op:"literal",value}; is_missing has args:[one AST]. subtract/divide/eq/lt/lte/gt/gte/and have args:[two ASTs]. datetime_diff has args:[end timestamp,start timestamp],unit:"ms"/"s"/"us". convert_unit has args:[one numeric AST],from_unit,to_unit (verified time units only). child_count/parent_match_count require relationship_id. conditional_count requires relationship_id,where:boolean AST or null. child_sum requires relationship_id,where,value:numeric child AST. group_count requires keys:[columns of current table],where:boolean AST or null; it is row-associated category frequency, not distinct count. These are the ONLY supported ops: no add,abs,not,or. Boolean is not numeric; and takes Boolean operands. Keep real signed coordinate meaning separate from business duration/common clock. Stored codes have no business semantics beyond confirmed documents. Use exact supported physical sources; parent_match_count runs on child, child aggregates on parent. missing_policy.input="propagate" (or count_missing for missing diagnosis), empty_group="zero" if ANY child_count/conditional_count/child_sum/group_count occurs in expression OR population filter, otherwise JSON null; denominator_zero="missing" if divide occurs, otherwise JSON null; child_missing="propagate" or "skip"; unknown_predicate="defer". Division by zero/unavailable stays unavailable, never zero/false. Existing definitions, raw column reading, subtract-zero, and missing-policy-only changes are not new discoveries. Provide narrow literal calculation meaning and unresolved operational needs, never fabricate measurements/thresholds/HR. If the requested purpose lacks required source/unit/relation, is already covered, or cannot be expressed, return candidates:[] with the precise reason. Unsupported purpose is not inherently wrong. In repair preserve the target meaning or explicitly disclose replacement; no silent replacement.'''

def nodes(ex):
 if not isinstance(ex,dict):return []
 return [ex]+sum([nodes(a)for a in ex.get('args',[])],[])+nodes(ex.get('where'))+nodes(ex.get('value'))

def calc_signature(p):
 return digest({k:p[k]for k in ['expression','grain','population','group_by','missing_policy']})

def candidate(c,id,ex,table,meaning,purpose=None,where=None,needs=None):
 inv=c['inventory'];t=next(t for t in inv['tables']if t['name']==table);ns=nodes(ex)+nodes(where)
 p={'id':id,'description':meaning,'expression':ex,'grain':{'table':table,'keys':t['primary_key']},'population':{'table':table,'filter':where},'group_by':[],'schema_version':inv['schema_version'],'source_version':inv['source_version'],'missing_policy':{'input':'count_missing'if ex['op']=='is_missing'else 'propagate','empty_group':'zero'if any(n['op']in ['child_count','conditional_count','child_sum','group_count']for n in ns)else None,'denominator_zero':'missing'if any(n['op']=='divide'for n in ns)else None,'child_missing':'propagate','unknown_predicate':'defer'}}
 v=Engine(inv,c['policy'],c['capture']).validate(p);assert v['passed'],v
 return {'id':id,'purpose':purpose or meaning,'meaning':meaning,'sources':{'columns':v['identity']['source_columns'],'relationships':v['identity']['relationships']},'expected_type':v['inferred_type'],'expected_unit':v['unit'],'availability':'offline_captured_scope','required_evidence':needs or ['online availability and leakage policy; operational usefulness unconfirmed'],'plan':p}

def evaluate_candidates(c,obj,existing=None,independent=True):
 result={'JSON_contract':False,'candidates':[],'zero':False,'errors':[]}
 if not isinstance(obj,dict)or set(obj)!={'candidates','stop_reason'}or not isinstance(obj['stop_reason'],str)or not isinstance(obj['candidates'],list)or len(obj['candidates'])>2:
  result['errors']=['output contract'];return result
 result['JSON_contract']=True;result['zero']=not obj['candidates'];seen=set(existing or []);engine=Engine(c['inventory'],c['policy'],c['capture'])
 for j,x in enumerate(obj['candidates']):
  item={'index':j,'contract':False,'source_exists':False,'definition_valid':False,'measurement_completed':False,'independent':False,'duplicate':False,'errors':[],'candidate':x}
  try:
   assert set(x)=={'id','purpose','meaning','sources','expected_type','expected_unit','availability','required_evidence','plan'},'candidate fields'
   assert all(isinstance(x[k],str)and x[k]for k in ['id','purpose','meaning']);assert x['availability']=='offline_captured_scope';assert isinstance(x['required_evidence'],list)and all(isinstance(z,str)for z in x['required_evidence'])
   assert x['expected_type']in ['integer','number','boolean','category','timestamp'];assert x['expected_unit']in ['count','ratio','boolean','ms','s','us',None]
   assert x['id']==x['plan']['id'];assert set(x['sources'])=={'columns','relationships'}
   item['contract']=True;p=x['plan'];v=engine.validate(p);item['validation']=v
   actual={(t['name'],y['name'])for t in c['inventory']['tables']for y in t['columns']if not y['excluded']};rels={r['id']for r in c['inventory']['relationships']}
   item['source_exists']=all((n.get('table'),n.get('column'))in actual for n in nodes(p['expression'])+nodes(p['population']['filter'])if n['op']=='column')and all(n.get('relationship_id')in rels for n in nodes(p['expression'])if 'relationship_id'in n)
   assert v['passed'],v['errors'];assert x['expected_type']==v['inferred_type'],'expected_type mismatch';assert x['expected_unit']==v['unit'],'expected_unit mismatch'
   assert sorted(tuple(z)for z in x['sources']['columns'])==sorted(tuple(z)for z in v['identity']['source_columns'])and sorted(x['sources']['relationships'])==sorted(v['identity']['relationships']),'sources dependency mismatch'
   item['definition_valid']=True;sig=calc_signature(p);item['duplicate']=sig in seen;seen.add(sig);item['calculation_signature']=sig
   ex=p['expression'];item['raw_reexpression']=ex['op']=='column'or(ex['op']=='subtract'and ex['args'][1]=={'op':'literal','value':0})
   m=engine.measure(p,retain_values=True);assert m['measurement_status']=='measured',m;vals=m.pop('_values');m.pop('_keys',None)
   vals=[{'decimal':str(z)}if isinstance(z,Decimal)else z for z in vals]
   f={'id':x['id'],'origin':'feature_model_proposal','meaning':x['meaning'],'plan':p,'dtype':v['inferred_type'],'unit':v['unit'],'definition_sha256':v['definition_sha256'],'schema_version':c['inventory']['schema_version'],'source_version':c['inventory']['source_version'],'calculation_policy':m['version'],'source_columns':v['identity']['source_columns'],'relationships':v['identity']['relationships'],'measurement':m}
   item.update(feature=f,values=vals,measurement_completed=True)
   if independent:item['independent_checks']=verify_candidates(c,[item]);item['independent']=True
  except Exception as e:item['errors'].append(str(e))
  result['candidates'].append(item)
 return result

def input_projection(c,task,existing=None,feedback=None):
 admission(c,'model_input');inv=c['inventory'];tabs=[]
 for t in inv['tables']:
  cols=[]
  for col in t['columns']:
   if col['excluded']:continue
   st=col['statistics'] or {};cols.append({'name':col['name'],'type':col['logical_type'],'nullable':col['nullable'],'unit':col['unit'],'description':col.get('description',''),'statistics':{k:st.get(k)for k in ['row_count','missing_count','distinct_nonmissing','invalid_type_count','numeric_summary']}})
  tabs.append({'table':t['name'],'primary_key':t['primary_key'],'rows':t['row_count'],'columns':cols})
 obj={'contract':VERSION,'schema_version':inv['schema_version'],'source_version':inv['source_version'],'task':task,'tables':tabs,'relationships':[{k:r[k]for k in ['id','from_table','from_columns','to_table','to_columns','statistics']}for r in inv['relationships']],'capture':{k:(v.get('value')or v)for k,v in c['capture'].items()},'source_context':c.get('scope',{}),'unknown':['business meaning of literal codes','business duration or common clock','online availability, leakage and operational usefulness'],'existing_definitions':existing or [],'supported_ops':list(OPS)}
 if feedback:obj['repair']=feedback
 return obj
