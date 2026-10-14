"""v5: call-local model aliases, explicit code identity, and request preservation.
Does not repair model ASTs. Original candidates remain immutable in result files.
"""
import copy
from feature_contract import *
from pilot_io import digest
CONTRACT_VERSION='feature-request-discovery-2.0'
SYSTEM=FEATURE_SYSTEM+''' The id field is only a local proposal_id alias inside this response; code assigns global registry identifiers. Preserve the requested target row, parent population filter, relationship direction, and aggregation level. Never replace a parent child_sum with individual child datetime_diff or a child parent_match_count with parent child_count. and(is_missing(X),is_missing(Y)) is supported Boolean arithmetic, including when all current results are false. Distinguish definition/type/unit rejection from runtime timestamp parsing or timezone incompatibility: datetime_diff propagates unavailable inputs and parsing/timezone incompatibility; it does not silently fix or convert them. For zero candidates name the exact unavailable column, unknown unit, unsupported operator, missing relationship, or registered duplicate and why this blocks the specific request. Unknown string units do not block Boolean missingness. In open discovery no particular answer is required: inspect the supplied physical evidence and propose up to two nonduplicate narrowly meaningful calculations, or give a precise reason for zero. New observations, code meanings, units, clocks and business contracts must not be invented.'''

def bind_candidates(context,obj,call_id):
 result=evaluate_candidates(context,obj)
 for item in result.get('candidates',[]):
  if not item.get('definition_valid'):continue
  raw=item['candidate'];definition=item['validation']['definition_sha256']
  registry='fv5_'+digest({'call_context':call_id,'definition':definition,'source':context['inventory']['source_version']})[:24]
  item['identity_mapping']={'call_id':call_id,'model_proposal_id':raw['id'],'registry_id':registry,'raw_candidate_sha256':digest(raw),'definition_sha256':definition,'source_version':context['inventory']['source_version'],'raw_id_preserved':True}
  if item.get('measurement_completed'):
   f=item['feature'];p=copy.deepcopy(f['plan']);p['id']=registry
   engine=Engine(context['inventory'],context['policy'],context['capture']);v=engine.validate(p)
   assert v['passed'] and v['definition_sha256']==definition
   m=engine.measure(p,retain_values=False);assert m['measurement_status']=='measured'
   f.update(id=registry,plan=p,measurement=m,model_proposal_id=raw['id'],identity_mapping=item['identity_mapping'])
 return result

def node_matches(node,table,column):return node.get('op')=='column' and node.get('table')==table and node.get('column')==column

def scope_check(obj,request):
 """Check public analysis scope; never compare with one hidden answer AST."""
 errors=[]
 if not isinstance(obj,dict):return {'passed':False,'errors':['not JSON object']}
 candidates=obj.get('candidates',[])
 if not candidates:
  reason=obj.get('stop_reason','')
  if request.get('supported',True):errors.append('supported requested analysis abandoned')
  for term in request.get('zero_reason_terms',[]):
   if term.lower() not in reason.lower():errors.append('zero reason does not identify '+term)
  return {'passed':not errors,'errors':errors}
 if not request.get('supported',True):errors.append('candidate produced for blocked specific request')
 if request.get('kind')=='discovery':return {'passed':not errors,'errors':errors,'open_discovery_no_fixed_answer_AST':True}
 for index,candidate_obj in enumerate(candidates):
  p=candidate_obj.get('plan',{});ex=p.get('expression',{});ns=nodes(ex);pop=p.get('population',{})
  if request.get('grain_table') and p.get('grain',{}).get('table')!=request['grain_table']:errors.append(f'candidates[{index}].plan.grain: requested target changed')
  if request.get('relationship'):
   if not any(n.get('relationship_id')==request['relationship'] for n in ns):errors.append(f'candidates[{index}]: requested physical relationship omitted')
  for operation in request.get('required_operations',[]):
   if not any(n.get('op')==operation for n in ns):errors.append(f'candidates[{index}]: required computation {operation} omitted')
  for table,column in request.get('required_columns',[]):
   if not any(node_matches(n,table,column)for n in ns):errors.append(f'candidates[{index}]: requested column {table}.{column} omitted')
  for f in request.get('population_filters',[]):
   found=False
   for n in nodes(pop.get('filter')):
    if n.get('op')=='eq' and len(n.get('args',[]))==2:
     a,b=n['args'];found|=(node_matches(a,f['table'],f['column']) and b=={'op':'literal','value':f['value']})or(node_matches(b,f['table'],f['column']) and a=={'op':'literal','value':f['value']})
   if not found:errors.append(f'candidates[{index}].plan.population.filter: requested {f["table"]}.{f["column"]}={f["value"]} omitted')
 return {'passed':not errors,'errors':errors}

def evidence_ref_compatible(mapping,definition,source):
 return mapping['definition_sha256']==definition and mapping['source_version']==source
