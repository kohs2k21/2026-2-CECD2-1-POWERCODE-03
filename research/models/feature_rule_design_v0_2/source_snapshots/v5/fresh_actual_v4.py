"""Unused complete real families and fixed-template replay; no discovery/training."""
from policy_v3 import *
import sys, csv, importlib.util
from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal
sys.path.insert(0,str(ROOT))
import prepare_approved_tpm_v23 as sampler
from generalized_source_policy_v21 import assert_source_paths, canonical_sha
from generalized_TPM_sample_v21 import _rows, _key, _time, _within

V2=ROOT/'data/eda/rule_sft_pilot_v2/20261012T223007+0900'
P25=ROOT/'data/eda/schema_generalization_v25/20261012_sr_review_feature_scope'
OLD=ROOT/'data/eda/schema_generalization_v23/20261012_feature_repair_expansion/approved_TPM_4096_r02'
sys.path.insert(0,str(P25/'implementation'))
from analysis_engine import Engine
from source_gate import guarded

def jsonlines(p):return [json.loads(s)for s in p.read_text().splitlines()]
def clean(v):return {'decimal':str(v)}if isinstance(v,Decimal)else v
def main():
 out=TOP/'fresh_actual/prepared';out.mkdir(parents=True)
 old=read(OLD/'source_sample/sample_manifest.json')
 old_policy=read(sampler.APPROVED_POLICY)
 assert_source_paths([str(sampler.APPROVED_SAMPLE/'schema.json'),str(sampler.APPROVED_SAMPLE/'sample_manifest.json')],old_policy,'measurement')
 schema=read(sampler.APPROVED_SAMPLE/'schema.json')
 assert_source_paths(old['source_pins'],old_policy,'selection_source_scan')
 assert_source_paths([str(OLD/'source_sample'/f'{t}.csv')for t in old['selection_plan']['table_order']],read(OLD/'source_sample/source_policy.json'),'measurement')
 plan=sampler.original_TPM_plan();tables={t['id']:t for t in schema['tables']}
 paths={Path(p['path']).stem:ROOT/p['path']for p in old['source_pins']}
 oldkeys={_key(row,['TRANSACTION_ID'])for _,row in _rows(OLD/'source_sample/EC_TRANSACTION_MONITORING.csv',tables['EC_TRANSACTION_MONITORING'],10000)}
 assert None not in oldkeys and len(oldkeys)==671
 recent=read(ROOT/"data/eda/rule_sft_real_eval_v1/20261013T001643+0900/prepared/selected_families.json")
 oldkeys.update((x["transaction_id"],) for x in recent)
 # Conservative whole-history ancestry audit; original row proofs, never held CSV contents.
 prior_proofs=[];prior_hashes=set();historical_rows={t:set()for t in tables}
 approved_SHAs={t:next(x['sha256']for x in old['source_pins']if Path(x['path']).stem==t)for t in tables}
 for proofpath in (ROOT/'data/eda').rglob('*.row_admission.json'):
  if str(TOP) in str(proofpath):continue
  pr=read(proofpath);origin=pr.get('original_source',{});table=Path(origin.get('path','')).stem
  if table not in approved_SHAs or origin.get('sha256')!=approved_SHAs[table]:continue
  prior_proofs.append(pin(proofpath))
  for rowproof in pr['rows']:
   if rowproof.get('source_sha256')==approved_SHAs[table]:
    historical_rows[table].add(rowproof['source_row_ordinal']);prior_hashes.add(rowproof['transaction_key_sha256'])
 for ordinal,row in _rows(paths['EC_TRANSACTION_MONITORING'],tables['EC_TRANSACTION_MONITORING'],1000000):
  k=_key(row,['TRANSACTION_ID'])
  if k is not None and canonical_sha(k)in prior_hashes:oldkeys.add(k)
 write(out/'all_history_ancestry.json',{'proofs':prior_proofs,'conservative_prior_family_hashes':len(prior_hashes),'known_original_keys':len(oldkeys),'prior_source_rows':{t:len(x)for t,x in historical_rows.items()},'held_CSV_read':False})
 oldrows=historical_rows
 for t in tables:
  assert all(_key(row,['TRANSACTION_ID'])in oldkeys for _,row in _rows(OLD/'source_sample'/f'{t}.csv',tables[t],10000))
 old_inv=read(OLD/'analysis_sample/inventory.json');versions=set();used=[]
 for run in [V1,V2]:
  for p in run.rglob('*.jsonl'):
   if 'consumed_samples'in p.name or 'events'in p.name:continue
   for r in jsonlines(p):
    i=r.get('input')
    if i and r.get('family','').startswith('approved'):
     versions.add(i['version']['source']);used.append({'file':pin(p),'id':r.get('id'),'source_version':i['version']['source'],'scope':i['scope']})
 assert versions=={old_inv['source_version']},versions
 write(out/'prior_use_audit.json',{'old_capture':pin(OLD/'source_sample/sample_manifest.json'),'real_input_records':used,'actual_source_versions':sorted(versions),'exclusion_is_conservative_full_capture':True,'excluded_transaction_families':len(oldkeys),'excluded_original_rows':{t:len(v)for t,v in oldrows.items()},'same_transaction_retries_and_all_P_M_same_family':True})
 write(out/'excluded_prior_families.json',sorted(k[0]for k in oldkeys))
 # This local adapter replaces selection only, retaining the unmodified guarded sampler.
 # All family children are scanned before selection; any inadmissible child holds its WHOLE family.
 exclusions=[];selected_metadata=[]
 windows=[tuple(__import__('os').environ['REFINEMENT_WINDOW'].split(','))]
 def selector(path,table,plan,period,limit,row_bound):
  records={};counters=Counter()
  for ordinal,row in _rows(path,table,row_bound):
   k=_key(row,['TRANSACTION_ID']);status=_within(row.get('START_TIME',''),period)
   if k is None:counters['missing_T_key']+=1;continue
   x=records.setdefault(k,{'count':0,'ok':True,'day':None,'ordinal':ordinal});x['count']+=1;x['ok'] &= status=='in_period'
   if status=='in_period':x['day']=_time(row['START_TIME']).date().isoformat()
  candidates={}
  for k,x in records.items():
   reason='prior_v1_v2_family'if k in oldkeys else 'duplicate_or_unverified_T'if x['count']!=1 or not x['ok']else 'outside_selected_date_windows'if not any(a<=x['day']<b for a,b in windows)else None
   if reason:exclusions.append({'transaction_key_sha':canonical_sha(k),'reason':reason});counters[reason]+=1
   else:candidates[k]={'rows':1,'held':False,**x}
  for tid in plan['table_order'][1:]:
   for ordinal,row in _rows(paths[tid],tables[tid],row_bound):
    k=_key(row,['TRANSACTION_ID'])
    if k not in candidates:continue
    candidates[k]['rows']+=1
    tc=plan['time_columns'].get(tid);st=_within(row.get(tc,''),period)if tc else 'missing'
    allowed=st=='in_period'or(st=='missing'and tid in plan.get('allow_timeless_by_approved_transaction',[]))
    if not allowed:candidates[k]['held']=True
  answer=[];used_rows=0
  for w,(a,b)in enumerate(windows):
   pool=sorted([(k,x)for k,x in candidates.items()if a<=x['day']<b],key=lambda item:canonical_sha(['feature_rule_refinement_v4',TOP.name,item[0]]));n=0
   for k,x in pool:
    reason='whole_family_has_unverified_child_period'if x['held']else 'whole_family_row_budget'if used_rows+x['rows']>10000 else 'date_window_128_family_quota'if n>=128 else None
    if reason:exclusions.append({'transaction_key_sha':canonical_sha(k),'reason':reason,'rows':x['rows'],'window':w});counters[reason]+=1;continue
    answer.append(k);n+=1;used_rows+=x['rows'];selected_metadata.append({'transaction_id':k[0],'transaction_key_sha':canonical_sha(k),'window':w,'T_day':x['day'],'T_source_ordinal':x['ordinal'],'all_related_raw_rows':x['rows']})
   assert n==128,{'window':w,'selected':n}
  assert len(answer)==128 and not set(answer)&oldkeys
  return answer,{'strategy':'unused transaction families;128 complete families per predeclared T wall-clock window;canonical key SHA order;whole-family exclusion and10000-row bound','windows':windows,'window_basis':'T.START_TIME wall-clock;P/M connected by original key, not separately assigned dates','prior_family_count':len(oldkeys),'selected':128,'counts':dict(counters),'conservative_selected_rows':used_rows,'rows_never_truncated':True}
 original_selector=sampler._select_transaction_keys;sampler._select_transaction_keys=selector
 try:sample,policy=sampler.prepare_expanded_relationship_sample(schema,paths,old_policy,plan,out/'source_sample',max_transactions=128)
 finally:sampler._select_transaction_keys=original_selector
 write(out/'excluded_families.json',exclusions);write(out/'selected_families.json',selected_metadata)
 inv,policy,capture,lineage=sampler.materialize_analysis_sample(sample,schema,policy,out/'analysis_sample')
 write(out/'analysis_sample/inventory.json',inv);write(out/'analysis_sample/source_policy.json',policy)
 write(out/'capture_context.json',capture)
 capture=read(out/'capture_context.json')
 newkeys={_key(row,['TRANSACTION_ID'])for _,row in _rows(out/'source_sample/EC_TRANSACTION_MONITORING.csv',tables['EC_TRANSACTION_MONITORING'],10000)}
 overlap={};actual_rows={}
 for t in tables:
  proof=read(out/'source_sample'/f'{t}.row_admission.json');ordinals={r['source_row_ordinal']for r in proof['rows']};actual_rows[t]=len(ordinals)
  assert len(ordinals)==proof['row_count'];overlap[t]=len(ordinals&oldrows[t]);assert overlap[t]==0
  assert all(_key(r,['TRANSACTION_ID'])in newkeys for _,r in _rows(out/'source_sample'/f'{t}.csv',tables[t],10000))
 assert sum(actual_rows.values())==sum(x['all_related_raw_rows']for x in selected_metadata)
 write(out/'sample_audit.json',{'transactions':len(newkeys),'table_rows':actual_rows,'old_family_overlap':len(newkeys&oldkeys),'original_row_overlap':overlap,'selected_windows':windows,'selected_T_days':dict(Counter(x['T_day']for x in selected_metadata)),'children_truncated':False,'all_related_original_file_rows_included':True,'source_manifest':pin(out/'source_sample/sample_manifest.json'),'source_policy':pin(out/'analysis_sample/source_policy.json'),'new_schema_version':inv['schema_version'],'new_source_version':inv['source_version'],'Body_excluded':True,'held_ancestry_admission_passed':True,'limits':['Same company/schema and original approved files; no independent-company claim','Unknown source timezone; no business processing-time or lifecycle completeness claim','Missing message timestamps admitted only by approved T key and original source provenance','T duplicate keys and families with unverifiable child periods excluded; not population-random']})
 _,receipt=guarded('profile',policy,lambda:None,inventory=inv);write(out/'profile_admission.json',receipt)
 print(json.dumps({'fresh_families':len(newkeys),'rows':actual_rows,'family_overlap':len(newkeys&oldkeys),'source_row_overlap':overlap}),flush=True)

if __name__=='__main__':main()
