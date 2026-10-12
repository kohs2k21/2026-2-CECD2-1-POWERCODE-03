"""Plan deterministic replacement of only cases violating anomaly invariants.

Unchanged CSV records are copied verbatim by author_csv.mjs --repair.
No source data, model, rule threshold, or case count is changed.
"""
import argparse
from collections import Counter
import json
from pathlib import Path

import pandas as pd

from generate import Catalog, TABLES, generate_case, make_history, stable_seed


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--inputs',type=Path,required=True)
    parser.add_argument('--dataset-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    bundle=json.loads(args.inputs.read_text(encoding='utf-8'))
    root=json.loads((args.dataset_root/'manifest.json').read_text(encoding='utf-8'))
    catalog=Catalog(bundle)
    _,history=make_history(root['seed'])
    plan={'datasets':[], 'reason':'strict positive paired delay; trend bounded by actual baseline headroom; deterministic rejection, no changed rule policy'}
    for dataset in root['datasets']:
        folder=args.dataset_root/dataset
        cases=pd.read_csv(folder/'cases.csv',encoding='utf-8-sig')
        events=pd.read_csv(folder/'events.csv',encoding='utf-8-sig')
        bad=set()
        for event in events.loc[events.scenario.eq('gradual_degradation')].itertuples():
            e=json.loads(event.evidence_json)
            maximum=max(v['normal_duration_ms'] for v in e['increments'])
            feasible=min(e['max_increment_ms'],e['reference_profile']['frozen_duration_cap_ms']-maximum-2)
            if feasible<1 or feasible<e['max_increment_ms']:
                bad.add(event.case_id)
        if dataset in ('individual_delay','group_delay','mixed_hard'):
            for labels in pd.read_csv(folder/'process_labels.csv',encoding='utf-8-sig',chunksize=100_000):
                delay=labels.scenario_types.astype('string').str.contains('individual_delay|group_delay',regex=True,na=False)
                bad.update(labels.loc[delay&labels.observed_duration_ms.le(labels.normal_duration_ms),'case_id'])
        if not bad:
            continue
        indices={f"C{stable_seed(root['seed'],dataset,i,'id'):016x}":i for i in range(len(cases))}
        metadata=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        metadata.pop('files')
        replacements={name:{'filename':name,'columns':columns,'rows':[]} for name,columns in TABLES.items()}
        old_tx=set()
        old_periods,old_adapters=Counter(),Counter()
        for labels in pd.read_csv(folder/'process_labels.csv',encoding='utf-8-sig',chunksize=100_000):
            selected=labels.loc[labels.case_id.isin(bad)]
            old_tx.update(selected.transactionId)
            old_periods.update(selected.source_period)
        for event in events.loc[events.case_id.isin(bad)&events.scenario.eq('input_stop')].itertuples():
            old_tx.update(json.loads(event.evidence_json)['missing_transaction_ids'])
        for records in pd.read_csv(folder/'processes.csv',usecols=['transactionId','adapterType'],encoding='utf-8-sig',chunksize=100_000):
            old_adapters.update(records.loc[records.transactionId.isin(old_tx),'adapterType'])
        fingerprints=set(cases.loc[~cases.case_id.isin(bad),'unique_content_fingerprint'])
        new_periods,new_adapters=Counter(),Counter()
        for case_id in sorted(bad,key=indices.get):
            tables=generate_case(catalog,dataset,indices[case_id],root['seed'],history).export()
            fp=tables['cases.csv'][0]['unique_content_fingerprint']
            if fp in fingerprints:
                raise ValueError('Replacement duplicates existing content')
            fingerprints.add(fp)
            new_periods.update(r['source_period'] for r in tables['process_labels.csv'])
            new_adapters.update(r['adapterType'] for r in tables['processes.csv'])
            for name,rows in tables.items():
                replacements[name]['rows'].extend([[r.get(c) for c in TABLES[name]] for r in rows])
        for period in set(old_periods)|set(new_periods):
            metadata['source_period_process_rows'][period]=metadata['source_period_process_rows'].get(period,0)-old_periods[period]+new_periods[period]
        for adapter in set(old_adapters)|set(new_adapters):
            key='adapter:'+adapter
            metadata['event_and_adapter_counts'][key]=metadata['event_and_adapter_counts'].get(key,0)-old_adapters[adapter]+new_adapters[adapter]
        metadata['generation_constraints']='strictly increased paired delay; per-case actual trend headroom; up to 128 deterministic attempts'
        metadata['replaced_case_count']=len(bad)
        plan['datasets'].append({'dataset':dataset,'case_ids':sorted(bad),'transaction_ids':sorted(old_tx),
                                 'tables':list(replacements.values()),'metadata':metadata})
        print(f'{dataset}: replace {len(bad)} cases; all other records unchanged',flush=True)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(plan,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    print('Replacement plan saved',flush=True)


if __name__=='__main__':
    main()
