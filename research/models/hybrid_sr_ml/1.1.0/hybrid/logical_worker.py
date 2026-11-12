"""Execute frozen whole C3 packs in an isolated interpreter, without model calls."""
from pathlib import Path
from copy import deepcopy
from collections import Counter,defaultdict
import argparse,csv,gzip,json,sys,time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hybrid.common import ROOT,read,write,sha,combine,named,seal
LOGICAL=ROOT/'vendor/logical/model'
sys.path[:0]=[str(LOGICAL/'implementation'),str(LOGICAL)]
from serve_rules import serve
from replay_esb_export_observation import require_family_locality
from export_contracts import export

def esb_cases(capture,profile,batch_size):
    manifest=read(capture);base=Path(capture).resolve().parent
    if manifest.get('contract')!='shared42-capture1.0':raise ValueError('Admitted shared42 capture required')
    if manifest.get('admission',{}).get('complete_family_capture')is not True:raise ValueError('Complete finite source snapshot required')
    allrows={};families={};pins=[]
    for short,entry in manifest['tables'].items():
        path=(base/entry['file']).resolve()
        # Existing admitted captures support absolute, SHA-pinned local source
        # files as well as relative files produced by capture-esb. Read only.
        if sha(path)!=entry['sha256']:raise ValueError('Capture source changed')
        pins.append(dict(path=str(path),sha256=sha(path)))
        with path.open(newline='',encoding='utf-8-sig') as f:rows=list(csv.DictReader(f))
        if len(rows)!=entry['row_count']:raise ValueError('Source row count changed')
        allrows[short]=rows
        for i,row in enumerate(rows):
            tid=row.get('TRANSACTION_ID') or None
            identity=tid if tid else '__unlinked__:'+short+':'+str(i)
            families.setdefault(identity,defaultdict(list))[short].append(i)
    groups=[]
    for item in profile['logical_sources']:
        template=read(ROOT/item['source']);pack=read(ROOT/item['pack']);require_family_locality(pack)
        mapping={}
        for table in template['schema']:
            short='T' if table.startswith('Transaction') else 'P' if table.startswith('Process') else 'M' if table.startswith('Message') else None
            if short is None:raise ValueError('Unreviewed ESB source binding')
            mapping[table]=short
        groups.append((item,template,pack,mapping))
    order=list(families)
    for offset in range(0,len(order),batch_size):
        selected=order[offset:offset+batch_size]
        cases=[]
        for item,template,pack,mapping in groups:
            case=deepcopy(template);source_map={}
            for table,short in mapping.items():
                fields=list(dict.fromkeys(case['schema'][table].get('columns',[])+case['schema'][table].get('keys',[])))
                indexes=[i for key in selected for i in families[key].get(short,[])]
                case['records'][table]=[];source_map[table]=[]
                for i in indexes:
                    raw=allrows[short][i]
                    if set(fields)-set(raw):raise ValueError('Logical metadata fields missing: '+table)
                    case['records'][table].append({k:None if raw[k]=='' else raw[k] for k in fields})
                    tid=raw.get('TRANSACTION_ID') or None
                    entity=tid if tid else '__unlinked__:'+short+':'+str(i)
                    source_map[table].append(dict(entity_id=entity,transaction_id=tid,physical_table=short,source_row_id=raw.get('__SOURCE_ROW_ID',str(i)),source_row_index=i))
            case['capture']['source_version']='hybrid-'+sha(capture)+'-batch-'+str(offset)
            cases.append((item['name'],case,pack,source_map,[]))
        yield selected,cases,pins

def flow_cases(path,workflow):
    folder=ROOT/'profiles/flowbench'/workflow
    profile=read(folder/'profile.json');template=read(folder/'logical/source_template.json');pack=read(folder/'logical/serving_pack.json')
    with gzip.open(path,'rt') as f:
        for line in f:
            payload=json.loads(line);case=deepcopy(template);case['records']=payload['records']
            case['capture']['entity_family_complete']=True;case['capture']['source_version']=payload['source_version']
            maps={}
            for table,rows in case['records'].items():
                maps[table]=[dict(entity_id=(row['RUN_ID']+':'+row['NODE_ID']) if table=='Task' else None,
                  transaction_id=None,physical_table=table,source_row_id=str(i),source_row_index=i) for i,row in enumerate(rows)]
            entities=[x['entity_id'] for x in maps['Task']]
            yield entities,[('flowbench_'+workflow,case,pack,maps,profile['integrity_only_proposal_ids'])],[]

def run(cases,output):
    start=time.perf_counter();out=Path(output);out.mkdir(parents=True,exist_ok=False)
    rule_counts=defaultdict(Counter);all_states=Counter();pins={};exported=set();n=0;unlinked=Counter()
    with gzip.open(out/'predictions.csv.gz','wt',newline='') as predictions,gzip.open(out/'predicate_values.csv.gz','wt',newline='') as values,gzip.open(out/'warning_and_unknown_evidence.jsonl.gz','wt') as ev:
        pw=csv.DictWriter(predictions,fieldnames=['entity_id','logical_SR','fired_rule_ids','unavailable_rule_ids','integrity_state']);pw.writeheader()
        vw=csv.DictWriter(values,fieldnames=['entity_id','group','rule_id','channel','physical_table','source_row_id','value','feature_id','feature_definition_sha256']);vw.writeheader()
        for entities,groupcases,sourcepins in cases:
            for p in sourcepins:pins[p['path']]=p['sha256']
            byentity=defaultdict(dict);integrity=defaultdict(list);expected=[]
            for name,case,pack,maps,integrity_ids in groupcases:
                result=serve(pack,case,bulk=True)
                contracts=export(pack,case)
                if name not in exported:
                    write(out/'linked_contracts'/f'{name}.json',contracts);exported.add(name)
                bindings={(v['original_model_proposal_id'],v['source_table'],v['source_row_index']):v for v in result['linked_feature_values']}
                for candidate in pack['proposal']['candidates']:
                    rid=name+':'+candidate['id']
                    if candidate['id'] not in integrity_ids:expected.append(rid)
                for rule in result['rules']:
                    rid=name+':'+rule['rule_id'];channel='integrity' if rule['rule_id'] in integrity_ids else 'logical_SR'
                    for row in rule['rows']:
                        location=maps[row['source_table']][row['source_row_index']];entity=location['entity_id'];state=named(row['value'])
                        rule_counts[rid][state]+=1
                        binding=bindings[(rule['rule_id'],row['source_table'],row['source_row_index'])]
                        vw.writerow(dict(entity_id=entity or '',group=name,rule_id=rid,channel=channel,physical_table=location['physical_table'],source_row_id=location['source_row_id'],value='' if row['value']is None else int(row['value']),feature_id=binding['feature_id'],feature_definition_sha256=binding['feature_definition_sha256']))
                        if entity is not None:
                            if channel=='integrity':integrity[entity].append(state)
                            else:byentity[entity].setdefault(rid,[]).append(state)
                        else:unlinked[row['source_table']]+=1
                        if state!='nonwarning':
                            support=[]
                            for s in row['support_rows']:
                                support.append(dict(table=s['table'],**maps[s['table']][s['row_index']]))
                            ev.write(json.dumps(dict(group=name,rule_id=rid,channel=channel,**location,value=row['value'],decision=state,source_record=row['source_record'],support_rows=support,feature_definition_sha256=binding['feature_definition_sha256']),separators=(',',':'))+'\n')
            for entity in entities:
                states={rid:combine(byentity[entity].get(rid,[])) for rid in expected}
                state=combine(states.values());all_states[state]+=1;n+=1
                pw.writerow(dict(entity_id=entity,logical_SR=state,fired_rule_ids=json.dumps([rid for rid,v in states.items() if v=='warning']),unavailable_rule_ids=json.dumps([rid for rid,v in states.items() if v=='unavailable']),integrity_state=combine(integrity.get(entity,[]))))
    for path,expected in pins.items():
        if sha(path)!=expected:raise ValueError('Source changed during logical execution')
    summary=dict(entities=n,logical_SR=dict(all_states),rules={k:dict(v) for k,v in rule_counts.items()},
      unlinked_rule_rows=dict(unlinked),seconds=time.perf_counter()-start,actual_LLM_calls=0,fit_calls=0,
      operational_approval=False,scope='Source-bound factual review predicates, not verified operating incidents.',
      missing_root_rows_or_unresolved_evidence='unavailable',source_pins=pins)
    write(out/'summary.json',summary);seal(out);print(json.dumps(summary),flush=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--capture');p.add_argument('--flow-records');p.add_argument('--workflow',choices=['1000genome','montage']);p.add_argument('--output',required=True);p.add_argument('--batch-size',type=int,default=500)
    a=p.parse_args()
    if bool(a.capture)==bool(a.flow_records) or not 1<=a.batch_size<=2000:raise ValueError('Choose exactly one admitted source mode')
    if a.capture:
        profile=read(ROOT/'profiles/esb/profile.json');cases=esb_cases(a.capture,profile,a.batch_size)
    else:
        if not a.workflow:raise ValueError('Explicit workflow required')
        cases=flow_cases(a.flow_records,a.workflow)
    run(cases,a.output)

if __name__=='__main__':main()
