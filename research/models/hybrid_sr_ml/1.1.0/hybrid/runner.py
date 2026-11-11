"""One explicit OR result with separate, source-traceable component decisions."""
from pathlib import Path
from collections import Counter,defaultdict
import csv,gzip,json,sys,time
from hybrid.common import ROOT,read,write,sha,combine,canonical,csv_rows,run_worker,seal

NUMERIC=ROOT/'vendor/numeric'
NATIVE='research.integration.feature_rule_ml.shared42'

def unique_rows(path,key):
    out={}
    if not Path(path).is_file():return out
    for row in csv_rows(path):
        k=row[key]
        if k in out:raise ValueError('Duplicate component identity: '+str(k))
        out[k]=row
    return out

def summarize_components(entities,numerical,logical,output,profile_name,errors=None):
    """Missing prediction rows remain unknown; known peer warnings survive."""
    counters={k:Counter() for k in ['numeric_SR','logical_SR','ML','combined']};overlap=Counter();missing=Counter();transitions=Counter();scopes=Counter()
    expected_ids=set();output=Path(output)
    fields=['entity_id','transaction_id','source_row_id','numeric_SR','logical_SR','if','kmeans','lof','ML','combined','strict_numeric_SR','strict_combined','fallback_for_rule_ids','fallback_actual_rule_ids','fallback_evaluation_count','original_unavailable_numeric_rule_ids','numeric_evaluation_scope','numeric_policy_version','fired_numeric_rule_ids','fired_logical_rule_ids','unavailable_numeric_rule_ids','unavailable_logical_rule_ids','unavailable_components','component_failure_reasons','interpretation']
    with gzip.open(output/'decisions.csv.gz','wt',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        for entity in entities:
            identity=entity['entity_id']
            if identity in expected_ids:raise ValueError('Ambiguous duplicate source identity')
            expected_ids.add(identity);n=numerical.get(identity);l=logical.get(identity)
            states={'numeric_SR':canonical(n['numeric_SR']) if n else 'unavailable','logical_SR':canonical(l['logical_SR']) if l else 'unavailable','ML':canonical(n['ML']) if n else 'unavailable'}
            if not n:missing['numerical_prediction']+=1
            if not l:missing['logical_prediction']+=1
            final=combine(states.values());active=[key for key,value in states.items() if value=='warning'];overlap['+'.join(active) if active else 'no_warning_some_unavailable' if final=='unavailable' else 'all_nonwarning']+=1
            strict_numeric=canonical(n.get('strict_numeric_SR',n['numeric_SR'])) if n else 'unavailable'
            strict_final=combine([strict_numeric,states['logical_SR'],states['ML']]);transitions[strict_final+' -> '+final]+=1
            scope=n.get('numeric_evaluation_scope','original') if n else 'unavailable';scopes[scope]+=1
            for k,v in states.items():counters[k][v]+=1
            counters['combined'][final]+=1
            writer.writerow(dict(entity_id=identity,transaction_id=entity.get('transaction_id',''),source_row_id=entity.get('source_row_id',''),**states,combined=final,strict_numeric_SR=strict_numeric,strict_combined=strict_final,
              fallback_for_rule_ids=n.get('fallback_for_rule_ids','[]') if n else '[]',fallback_actual_rule_ids=n.get('fallback_actual_rule_ids','[]') if n else '[]',fallback_evaluation_count=n.get('fallback_evaluation_count',0) if n else 0,
              original_unavailable_numeric_rule_ids=n.get('strict_unavailable_numeric_rule_ids',n.get('unavailable_numeric_rule_ids','[]')) if n else '[]',numeric_evaluation_scope=scope,numeric_policy_version=n.get('policy_version','strict-original') if n else 'unavailable',
              **{a:canonical(n[a]) if n and n.get(a) else 'unavailable' for a in ['if','kmeans','lof']},
              fired_numeric_rule_ids=n.get('fired_numeric_rule_ids','[]') if n else '[]',fired_logical_rule_ids=l.get('fired_rule_ids','[]') if l else '[]',
              unavailable_numeric_rule_ids=n.get('unavailable_numeric_rule_ids','[]') if n else '[]',unavailable_logical_rule_ids=l.get('unavailable_rule_ids','[]') if l else '[]',
              unavailable_components=json.dumps([k for k,v in states.items() if v=='unavailable']),component_failure_reasons=json.dumps(errors or {},ensure_ascii=False),
              interpretation='research anomaly/review candidate; not a confirmed incident'))
    extras=set(logical)-expected_ids
    if extras:
        with gzip.open(output/'logical_entities_without_T.csv.gz','wt',newline='') as f:
            columns=list(next(iter(logical.values())));w=csv.DictWriter(f,fieldnames=columns);w.writeheader();w.writerows(logical[e] for e in sorted(extras))
    return dict(profile=profile_name,entities=len(expected_ids),components={k:dict(v) for k,v in counters.items()},contributions=dict(overlap),
      prediction_rows_missing=dict(missing),logical_entities_not_linkable_to_source_T=len(extras),component_errors=errors or {},strict_to_current_transitions=dict(transitions),numeric_evaluation_scopes=dict(scopes),
      interpretation='OR combines anomaly/review candidate warnings, not company-confirmed incidents.',operational_approval=False)

def esb_numerical(output,entities):
    folder=output/'numerical';path=folder/'transaction_decisions.csv.gz'
    if not path.is_file():return {}
    profile=read(ROOT/'vendor/numeric/research/integration/feature_rule_ml/shared42/release/profile.json')
    numeric_ids={r['id'] for r in profile['definition']['rules'] if r['role']=='numeric_SR'}
    by_source={r['source_row_id']:r['entity_id'] for r in entities};bytid={r['transaction_id']:r['entity_id'] for r in entities if r['transaction_id']}
    records={}
    for row in csv_rows(path):
        entity=by_source.get(row['source_row_id'])
        if entity is None:raise ValueError('Numerical T prediction has unknown source row')
        records[entity]={**row,'numeric_SR':canonical(row['numeric_SR']),'ML':canonical(row['ML']),
          'fired_numeric_rule_ids':'[]','unavailable_numeric_rule_ids':'[]'}
    fired=defaultdict(set);unknown=defaultdict(set)
    with gzip.open(output/'numeric_and_ML_warning_evidence.jsonl.gz','wt') as evidence:
        for short in 'TPM':
            path=folder/(short+'_decisions.csv.gz')
            if not path.is_file():continue
            for row in csv_rows(path):
                entity=by_source.get(row['source_row_id']) if short=='T' else bytid.get(row['transaction_id'])
                f=set(json.loads(row['fired_rule_ids']))&numeric_ids;u=set(json.loads(row['unavailable_rule_ids']))&numeric_ids
                if entity:fired[entity]|=f;unknown[entity]|=u
                mlstates={a:canonical(row[a]) for a in ['if','kmeans','lof']}
                if f or u or any(s!='nonwarning' for s in mlstates.values()):
                    evidence.write(json.dumps(dict(entity_id=entity,source_table=short,source_row_id=row['source_row_id'],transaction_id=row['transaction_id'],process_id=row['process_id'],message_id=row['message_id'],numeric_rule_ids=sorted(f),unavailable_numeric_rule_ids=sorted(u),ML_states=mlstates,scores={k:None if row[k]=='' else float(row[k]) for k in ['if_margin','kmeans_margin','lof_score']}),separators=(',',':'))+'\n')
    for entity,row in records.items():row.update(fired_numeric_rule_ids=json.dumps(sorted(fired[entity])),unavailable_numeric_rule_ids=json.dumps(sorted(unknown[entity])))
    return records

def apply_ratio_alternatives(records,entities,path):
    """Fail closed on result identity/binding drift; never fill a missing row."""
    guarded=unique_rows(path,'source_row_id');expected={r['source_row_id']:r for r in entities}
    if set(guarded)!=set(expected):raise ValueError('Alternative transaction coverage differs')
    result={}
    for source,row in guarded.items():
        entity=expected[source];old=records.get(entity['entity_id'])
        if old is None or row['transaction_id']!=entity['transaction_id'] or canonical(row['strict_numeric_SR'])!=canonical(old['numeric_SR']):raise ValueError('Alternative transaction/strict source binding differs')
        result[entity['entity_id']]={**old,**row}
    return result

def detect_esb(capture,output,models=None,ratio_policy='fallback'):
    if ratio_policy not in ('fallback','strict'):raise ValueError('Unsupported ratio policy')
    started=time.perf_counter();out=Path(output).resolve();out.mkdir(parents=True,exist_ok=False)
    steps=[];errors={};capture=Path(capture).resolve();pin=sha(capture)
    step=run_worker([sys.executable,'-B',str(ROOT/'hybrid/esb_preflight.py'),'--capture',str(capture),'--output',str(out/'admission')],out/'admission_logs');steps.append(step)
    if step['exit_code']:
        write(out/'execution.json',dict(state='unavailable',reason='source_admission_failed',steps=steps,fit_calls=0,LLM_calls=0));return dict(state='unavailable',reason='source_admission_failed',output=str(out))
    entities=list(csv_rows(out/'admission/entities.csv.gz'))
    cmd=[sys.executable,'-B','-m',NATIVE,'detect','--capture',str(capture),'--trust-local-model','--output',str(out/'numerical')]
    if models:cmd+=['--models',str(Path(models).resolve())]
    step=run_worker(cmd,out/'numerical_logs',NUMERIC);steps.append(step)
    if step['exit_code']:errors['numeric_SR_and_ML']='worker_failed; see numerical_logs/stderr.log'
    if ratio_policy=='fallback' and 'numeric_SR_and_ML' not in errors:
        step=run_worker([sys.executable,'-B',str(ROOT/'hybrid/ratio_fallback_worker.py'),'--capture',str(capture),'--native',str(out/'numerical'),'--output',str(out/'ratio_fallback')],out/'ratio_fallback_logs');steps.append(step)
        if step['exit_code']:errors['ratio_fallback']='alternative_comparison_worker_failed; original strict results retained; see ratio_fallback_logs/stderr.log'
    step=run_worker([sys.executable,'-B',str(ROOT/'hybrid/logical_worker.py'),'--capture',str(capture),'--output',str(out/'logical')],out/'logical_logs');steps.append(step)
    if step['exit_code']:errors['logical_SR']='worker_failed; see logical_logs/stderr.log'
    # Failed partial files never become trusted predictions. Preserve them for review.
    numerical=esb_numerical(out,entities) if 'numeric_SR_and_ML' not in errors else {}
    if ratio_policy=='fallback' and not errors.get('ratio_fallback') and not errors.get('numeric_SR_and_ML'):
        try:numerical=apply_ratio_alternatives(numerical,entities,out/'ratio_fallback/transaction_predictions.csv.gz')
        except (ValueError,KeyError) as exc:errors['ratio_fallback']='alternative_result_binding_failed: '+str(exc)
    logical=unique_rows(out/'logical/predictions.csv.gz','entity_id') if 'logical_SR' not in errors else {}
    summary=summarize_components(entities,numerical,logical,out,'esb_shared42_C3',errors)
    if sha(capture)!=pin:raise ValueError('Admitted capture changed during detection')
    summary.update(state='completed_with_unavailable' if errors else 'completed',ratio_policy=ratio_policy,ratio_policy_version=read(ROOT/'profiles/esb/ratio_fallback.json')['version'] if ratio_policy=='fallback' else 'strict-original',ratio_fallback_evidence='ratio_fallback/alternative_rule_evidence.csv.gz' if (out/'ratio_fallback/alternative_rule_evidence.csv.gz').is_file() else None,total_seconds=time.perf_counter()-started,raw_features_recomputed=True,ML_input_definitions=42,ML_models=9,numeric_SR_rules=42,logical_SR_rules=3,fit_calls=0,LLM_calls=0,threshold_changes=0,
      legacy_observed_SR_in_primary=False,HR_in_primary=False,source_capture_sha256=pin,post_completion_batch=True,real_time_latency_evaluated=False)
    write(out/'summary.json',summary);write(out/'execution.json',dict(state='completed_with_unavailable' if errors else 'completed',steps=steps,model_sha256=sha(ROOT/'model.json'),capture_sha256=pin,operational_approval=False))
    seal(out);return summary

def detect_flowbench(workflow,output,input_dir=None,run_list=None):
    started=time.perf_counter();out=Path(output).resolve();out.mkdir(parents=True,exist_ok=False);errors={};steps=[]
    cmd=[sys.executable,'-B',str(ROOT/'hybrid/flowbench_worker.py'),'--workflow',workflow,'--output',str(out/'numerical')]
    cmd+=['--input-dir',str(Path(input_dir).resolve())] if input_dir else ['--run-list',str(Path(run_list).resolve())]
    step=run_worker(cmd,out/'numerical_logs');steps.append(step)
    if step['exit_code']:errors['numeric_SR_and_ML']='worker_failed; see numerical_logs/stderr.log'
    logical_input=out/'numerical/logical_records.jsonl.gz'
    if logical_input.is_file():
        step=run_worker([sys.executable,'-B',str(ROOT/'hybrid/logical_worker.py'),'--flow-records',str(logical_input),'--workflow',workflow,'--output',str(out/'logical')],out/'logical_logs');steps.append(step)
        if step['exit_code']:errors['logical_SR']='worker_failed; see logical_logs/stderr.log'
    else:errors['logical_SR']='source_projection_unavailable; no input or fallback invented'
    path=out/'numerical/predictions.csv.gz'
    numerical=unique_rows(path,'entity_id') if path.is_file() and 'numeric_SR_and_ML' not in errors else {}
    metadata=out/'numerical/metadata/TASK.csv'
    if metadata.is_file():entities=[dict(entity_id=r['ROW_ID'],transaction_id='',source_row_id=r['ROW_ID']) for r in csv_rows(metadata)]
    else:
        write(out/'execution.json',dict(state='unavailable',reason='source_projection_failed',steps=steps));return dict(state='unavailable',output=str(out),reason='source_projection_failed')
    logical=unique_rows(out/'logical/predictions.csv.gz','entity_id') if 'logical_SR' not in errors else {}
    summary=summarize_components(entities,numerical,logical,out,'flowbench_'+workflow,errors)
    summary.update(state='completed_with_unavailable' if errors else 'completed',total_seconds=time.perf_counter()-started,raw_features_recomputed=True,ML_input_definitions=8,ML_models=3,fit_calls=0,LLM_calls=0,threshold_changes=0,
      integrity_checks='Actual generated RunContext lookup remains separately reported and excluded from primary SR by explicit profile policy.',
      evaluation_labels_read=False,post_completion_batch=True,real_time_latency_evaluated=False)
    write(out/'summary.json',summary);write(out/'execution.json',dict(state='completed_with_unavailable' if errors else 'completed',steps=steps,model_sha256=sha(ROOT/'model.json'),operational_approval=False));seal(out);return summary
