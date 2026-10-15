from feature_contract import *

def run():
 fs=read(TOP/'definition_usage71.json');original=read(V3/'data/feature_catalog64.json')+read(V3/'data/HR_extra5.json');new=read(V3/'new_features/adopted_narrow2.json');origin={f['id']:f for f in original};origin.update({x['feature']['id']:x['feature']for x in new});valuegroups={};definitiongroups={};counts=Counter();items=[]
 for x in fs:
  s=x['summary'];n=s['valid_count'];var=s['variation'];counts['constant'if var=='constant'and n>0 else 'no_valid_observation'if n==0 else 'variable']+=1
  f=origin[x['id']];vs=f.get('value_digest');sig=calc_signature(x['plan']);definitiongroups.setdefault(sig,[]).append(x['id'])
  if vs:valuegroups.setdefault((x['plan']['grain']['table'],digest(x['plan']['population']),x['dtype'],vs),[]).append(x['id'])
  x=deepcopy(x);x['constant']=n>0 and var=='constant';x['no_valid_observation']=n==0;x['current_scope_analysis_state']='no_observation'if n==0 else 'calculation_available_narrow_meaning_only';x['IF_state']='excluded_current_sample_constant'if x['constant']else 'deferred';x['IF_reason']='No variation in this current capture; retain confirmed HR indicator and distinct definition.'if x['constant']else 'No usable observations in current capture.'if n==0 else 'Offline capture may use future child/category rows; query time, as-of calculation, lifecycle completion, leakage and transformation policy unconfirmed.'
  ex=x['plan']['expression'];x['unit_review']='raw numeric unit unknown; no physical quantity meaning'if x['unit']is None and x['dtype']in ['number','integer']else 'calculation unit verified by typed operation, not business-duration proof'if x['unit']in ['ms','s','us']else 'count/ratio/Boolean computational unit, not mandatory business contract'
  x['availability']='offline completed captured-file rows only; live business completion not verified';x['frequency_policy']='captured population frequency only; future/as-of reference/new-category policy absent'if ex['op']=='group_count'else None;items.append(x)
 definitiondup=[{'signature':k,'ids':v}for k,v in definitiongroups.items()if len(v)>1];valuedup=[{'table':k[0],'population_sha':k[1],'type':k[2],'value_sha':k[3],'ids':v,'different_definition_retained':True}for k,v in valuegroups.items()if len(v)>1]
 write(TOP/'definition_usage71_review.json',{'definitions':items,'counts':{'total':71,'original64':64,'HR_code5':5,'new_narrow2':2,**dict(counts),'measured':71,'with_any_valid_observations':sum(x['summary']['valid_count']>0 for x in items),'operational_usefulness_approved':0,'IF_approved':0,'IF_excluded_current_constant':sum(x['constant']for x in items),'IF_deferred':sum(not x['constant']for x in items)},'definition_duplicates':definitiondup,'sample_value_duplicate_groups':valuedup,'different_definitions_not_deleted':True,'HR_constraints_retained_at_zero':True,'measurements_reused_from':'v3 fixed evaluation capture; not recalculated on v4 new source'})
 print(dict(counts))
if __name__=='__main__':run()
