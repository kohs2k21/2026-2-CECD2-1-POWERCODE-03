"""Fresh native raw features -> explicit source-bound alternative comparisons."""
from pathlib import Path
from collections import Counter,defaultdict
from decimal import localcontext
import argparse,csv,gzip,json,sys,time
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hybrid.common import ROOT,read,write,sha,combine,canonical,csv_rows,seal
from hybrid.ratio_fallback_policy import VERSION,ALTERNATIVES,COUNTER_RULES,make_registry,decide
sys.path.insert(0,str(ROOT/'vendor/numeric'))
from research.integration.feature_rule_ml.shared42.artifacts import profile,verify
from research.integration.feature_rule_ml.shared42.capture import Capture
from specmodel.rule_logic import value_condition

def run(capture,native,output):
    started=time.perf_counter();out=Path(output);out.mkdir(exist_ok=False)
    _,p=profile();registry=make_registry(p);configured=read(ROOT/'profiles/esb/ratio_fallback.json')
    if registry!=configured:raise ValueError('Alternative rule/feature/operand/reference binding changed')
    native=verify(native,'detection_run');c=Capture(capture,p)
    if read(native/'summary.json')['capture_sha']!=c.manifest_sha:raise ValueError('Native results use another capture')
    if read(native/'execution.json')['source_pins']!=c.pins:raise ValueError('Native sources differ from current raw data')
    d=p['definition'];fs={f['id']:f['feature'] for f in d['features']};rules=[r for r in d['rules'] if r['role']=='numeric_SR'];rulemap={r['id']:r for r in rules}
    old=list(csv_rows(native/'transaction_decisions.csv.gz'));by_source={r['source_row_id']:r for r in old};bytid={r['transaction_id']:r['source_row_id'] for r in old if r['transaction_id']}
    if len(by_source)!=len(old) or len(bytid)!=sum(bool(r['transaction_id']) for r in old):raise ValueError('Transaction prediction duplicates')
    families={k:defaultdict(list) for k in ['strict','new']};events=defaultdict(list);fired=defaultdict(set);unavailable=defaultdict(set);strict_failures=defaultdict(set);stats=[];sources={};unlinked=Counter()
    for s,spec in d['schema']['tables'].items():
        rs=list(csv_rows(native/(s+'_decisions.csv.gz')));raw=c.rows[spec['name']]
        # NPZ is a lazy ZIP reader: load each matrix once, never per row.
        with np.load(native/(s+'_features.npz')) as archive:arr={k:archive[k] for k in archive.files}
        ids=arr['feature_ids'].tolist()
        if len(rs)!=len(raw) or len(raw)!=arr['values'].shape[0]:raise ValueError('Native/raw row coverage differs')
        for a,b in zip(rs,raw):
            if any(a[k]!=b.get(k.upper(),'') for k in ('transaction_id','process_id','message_id')) or a['source_row_id']!=b['__SOURCE_ROW_ID']:raise ValueError('Native/raw identity mismatch')
        if len({r['source_row_id'] for r in rs})!=len(rs):raise ValueError('Duplicate native source key')
        sources[s]=dict(rows=rs,raw=raw,npz=arr,ids=ids,fired=[set(json.loads(r['fired_rule_ids'])) for r in rs],unknown=[set(json.loads(r['unavailable_rule_ids'])) for r in rs])
    fields=['transaction_id','table','source_row_id','ratio_rule_id','ratio_feature_sha','original_state','new_state','reason','actual_evaluation_rule','numerator','denominator','alternative_value','alternative_condition','counter_values']
    with gzip.open(out/'alternative_rule_evidence.csv.gz','wt',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        with localcontext() as ctx:
            ctx.prec=50
            for r in rules:
                f=fs[r['feature_id']];s=next(s for s,t in d['schema']['tables'].items() if t['name']==r['table']);src=sources[s];j=src['ids'].index(r['feature_id']);counts=Counter();reasons=Counter()
                e=c.engine;e.current_table=r['table'];e.current_population=f['population']['filter']
                for i,row in enumerate(src['raw']):
                    tx=row['TRANSACTION_ID'];entity=row['__SOURCE_ROW_ID'] if s=='T' else bytid.get(tx)
                    pred=src['rows'][i]
                    if src['npz']['selection_unknown'][i,j]:state='unavailable'
                    elif not src['npz']['population'][i,j]:state='outside_population'
                    elif r['id']in src['fired'][i]:state='warning'
                    elif r['id']in src['unknown'][i]:state='unavailable'
                    else:state='nonwarning'
                    new=state;detail=None;alternative_value=None;condition=None;operands=None;counters=None
                    if r['id']in ALTERNATIVES and state=='unavailable' and not src['npz']['selection_unknown'][i,j]:
                        operands=[e.evaluate(x,row,r['table'],f['missing_policy']) for x in f['expression']['args']]
                        aid=ALTERNATIVES[r['id']];altstate=None
                        if aid is not None:
                            ar=rulemap[aid];af=fs[ar['feature_id']];alternative_value=e.evaluate(af['expression'],row,r['table'],af['missing_policy']);condition=ar['condition']
                            value=value_condition(alternative_value,condition);altstate='unavailable' if value is None else 'warning' if value else 'nonwarning'
                            aj=src['ids'].index(ar['feature_id'])
                            expected='outside_population' if not src['npz']['population'][i,aj] else 'warning' if aid in src['fired'][i] else 'unavailable' if aid in src['unknown'][i] else 'nonwarning'
                            if altstate!=expected:raise ValueError('Alternative differs from current native comparator')
                        if r['id']in COUNTER_RULES:counters={k:e.column(row,r['table'],k) for k in ['TOTAL_COUNT','SUCCESS_COUNT','ERROR_COUNT']}
                        detail=decide(r['id'],*operands,state,altstate,counters);new=detail.state
                        reasons[detail.reason]+=1
                        record=dict(transaction_id=tx,table=s,source_row_id=row['__SOURCE_ROW_ID'],ratio_rule_id=r['id'],ratio_feature_sha=f['sha'],original_state=state,new_state=new,reason=detail.reason,actual_evaluation_rule=detail.actual_rule or '',numerator='' if operands[0]is None else str(operands[0]),denominator='' if operands[1]is None else str(operands[1]),alternative_value='' if alternative_value is None else str(alternative_value),alternative_condition=json.dumps(condition),counter_values=json.dumps(counters,default=str))
                        writer.writerow(record)
                        if entity is not None:events[entity].append(dict(rule_id=r['id'],actual_rule=detail.actual_rule,used_fallback=detail.used_fallback,state=new,reason=detail.reason))
                    counts[new]+=1
                    if entity is None:
                        unlinked[s+':'+r['id']]+=1
                        continue
                    families['strict'][entity].append(state);families['new'][entity].append(new)
                    if state=='unavailable':strict_failures[entity].add(r['id'])
                    if new=='warning':fired[entity].add(detail.actual_rule if detail and detail.used_fallback else r['id'])
                    if new=='unavailable':unavailable[entity].add(r['id'])
                stats.append(dict(rule_id=r['id'],states=dict(counts),reasons=dict(reasons)))
    fields=['transaction_id','source_row_id','numeric_SR','strict_numeric_SR','fired_numeric_rule_ids','unavailable_numeric_rule_ids','strict_unavailable_numeric_rule_ids','fallback_for_rule_ids','fallback_actual_rule_ids','fallback_evaluation_count','fallback_evaluation_unknown_count','numeric_evaluation_scope','policy_version']
    counts=Counter();originals=Counter();fallback_counts=Counter()
    with gzip.open(out/'transaction_predictions.csv.gz','wt',newline='') as stream:
        w=csv.DictWriter(stream,fieldnames=fields);w.writeheader()
        for row in old:
            tx=row['transaction_id'];entity=row['source_row_id'];strict=combine([x for x in families['strict'][entity] if x!='outside_population'])
            if strict!=canonical(row['numeric_SR']):raise ValueError('Strict original aggregation differs')
            current=combine([x for x in families['new'][entity] if x!='outside_population']);ev=events[entity];used=[v for v in ev if v['used_fallback']]
            counts[current]+=1;originals[strict]+=1;fallback_counts['transactions_using_alternative']+=bool(used);fallback_counts['alternative_comparisons']+=len(used)
            for v in used:fallback_counts['alternative_'+v['state']]+=1
            w.writerow(dict(transaction_id=tx,source_row_id=row['source_row_id'],numeric_SR=current,strict_numeric_SR=strict,fired_numeric_rule_ids=json.dumps(sorted(fired[entity])),unavailable_numeric_rule_ids=json.dumps(sorted(unavailable[entity])),strict_unavailable_numeric_rule_ids=json.dumps(sorted(strict_failures[entity])),fallback_for_rule_ids=json.dumps(sorted({v['rule_id'] for v in used})),fallback_actual_rule_ids=json.dumps(sorted({v['actual_rule'] for v in used if v['actual_rule']})),fallback_evaluation_count=len(used),fallback_evaluation_unknown_count=sum(v['state']=='unavailable' for v in ev),numeric_evaluation_scope='original_and_explicit_alternative_comparisons' if used else 'original',policy_version=VERSION))
    c.assert_unchanged();write(out/'summary.json',dict(version=VERSION,entities=len(old),strict_numeric_SR=dict(originals),numeric_SR=dict(counts),fallback_counts=dict(fallback_counts),not_applicable_count=0,per_rule=stats,unlinked_source_rule_instances=dict(unlinked),registry_sha=sha(ROOT/'profiles/esb/ratio_fallback.json'),capture_sha=c.manifest_sha,native_manifest_sha=sha(native/'manifest.json'),fit_calls=0,LLM_calls=0,threshold_changes=0,seconds=time.perf_counter()-started,interpretation='Explicit alternate measure/reference or reported-count consistency evaluation, not an incident certificate.'))
    seal(out)

def main():
    p=argparse.ArgumentParser();p.add_argument('--capture',required=True);p.add_argument('--native',required=True);p.add_argument('--output',required=True);a=p.parse_args();run(a.capture,a.native,a.output)

if __name__=='__main__':main()
