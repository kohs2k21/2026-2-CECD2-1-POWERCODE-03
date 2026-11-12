"""Independent checks of actual saved row predictions and runtime contracts."""
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.cluster import MiniBatchKMeans
from sklearn.neighbors import LocalOutlierFactor

from .data import ROOT,TASKS,write_json,digest
from .evaluate import ALGORITHMS
from .inference import EventMonitor,load
from .models import metrics

OUT=ROOT/'reports/process_event_model/row_calibrated_v3'


def fault_estimators(value):
    pending=[value];seen=set();found=[]
    while pending:
        item=pending.pop()
        if id(item) in seen:continue
        seen.add(id(item))
        if isinstance(item,(IsolationForest,MiniBatchKMeans,LocalOutlierFactor)):
            found.append(item)
        elif isinstance(item,dict):pending.extend(item.values())
        elif isinstance(item,(tuple,list)):pending.extend(item)
        elif type(item).__module__.startswith('process_event_model'):
            pending.extend(vars(item).values())
    return found


def audit(folder=None):
    output=folder or OUT
    package=joblib.load(output/'event_models.joblib')
    processor_kind=package.get('processor')
    report=json.loads((output/'confirmation.json').read_text(encoding='utf-8'))
    assert report['model_sha256']==digest(output/'event_models.joblib')
    score_folder=output
    if not (score_folder/'individual_delay_observed_if_scores.npz').exists():
        # Compact handoffs contain aggregate reports, not benchmark row caches.
        # These optional offline diagnostics must never become inference inputs.
        manifest=json.loads((output/'manifest.json').read_text(encoding='utf-8'))
        source=manifest.get('source_attempt')
        if not source:
            raise FileNotFoundError('Saved score diagnostics missing; rerun the benchmark evaluator before a full metric audit')
        score_folder=(ROOT/source.replace('\\','/')).resolve()
        if not score_folder.is_relative_to((ROOT/'reports/process_event_model').resolve()):
            raise ValueError('Offline score diagnostics must remain under the research reports folder')
        assert digest(score_folder/'event_models.joblib')==digest(output/'event_models.joblib')
    signature=hashlib.sha256(joblib.hash(package['reference']).encode()).hexdigest()[:12]
    if processor_kind=='predictive_row_context':
        signature+='-predictive-'+digest(ROOT/'code/process_event_model/predictive.py')[:12]
    folder=ROOT/'reports/process_event_model/confirmation_features'/signature
    checks={};pooled={a:dict(y=[],p=[]) for a in ALGORITHMS}
    for task in report['tasks']:
        for a in ALGORITHMS:
            yy=[];pp=[]
            for cohort in ('observed','normal'):
                z=np.load(score_folder/f'{task}_{cohort}_{a}_scores.npz')
                assert np.isfinite(z['score']).all()
                yy.append(z['label'][z['target']]);pp.append(z['score'][z['target']]>1.)
            if task=='input_stop':continue
            y,p=np.concatenate(yy),np.concatenate(pp)
            recomputed=metrics(y,p)
            for k,v in recomputed.items():assert np.isclose(v,report['tasks'][task]['metrics'][a][k]),(task,a,k)
            pooled[a]['y'].append(y);pooled[a]['p'].append(p)
    for a,p in pooled.items():
        m=metrics(np.concatenate(p['y']),np.concatenate(p['p']))
        for k,v in m.items():assert np.isclose(v,report['pooled'][a][k]),(a,k)
    pair,cases=joblib.load(folder/'group_delay_400.joblib')
    chosen=cases.case_id.iloc[:3]
    f=pair['observed'].loc[pair['observed'].case_id.isin(chosen)].copy().reset_index(drop=True)
    cols=('transactionId','processId','adapterType','dependProcessId','processHubId','channelId','status',
          'startTime','endTime','successCount','errorCount','responseCode','totalCount','retryCount',
          'availableTime','case_id','source_period','event_row_id')
    packet=f[[c for c in cols if c in f]].copy()
    forbidden={'label','row_is_anomaly','collective_context_is_anomaly','target','cohort','scenario_types','event_id'}
    for a,model in package['models'].items():
        base=model.base
        assert isinstance(base.estimator,(IsolationForest,MiniBatchKMeans,LocalOutlierFactor))
        assert len(fault_estimators(model))==1, 'Calibration parents must not add a second classifier'
        assert not (set(model.columns)&forbidden)
        result=EventMonitor(package['reference'],model,processor_kind=processor_kind).predict(packet)
        np.testing.assert_allclose(result.score,model.predict(f)[0],rtol=1e-9,atol=1e-9)
        np.testing.assert_array_equal(result.anomaly,model.predict(f)[1])
        assert result.transactionId.tolist()==packet.transactionId.tolist()
        assert len(result)==len(packet)
        cutoff=packet.availableTime.sort_values().iloc[len(packet)//2]
        prefix=(packet.availableTime<cutoff).to_numpy()
        early=EventMonitor(package['reference'],model,processor_kind=processor_kind).predict(packet.loc[prefix])
        np.testing.assert_allclose(early.score,result.loc[prefix,'score'],rtol=1e-9,atol=1e-9)
        # Actual backend contract: complete timestamp batches across calls must
        # match one batch, including optional incident state (not only scores).
        whole=load(output/'event_models.joblib',a,with_incidents=True).predict(packet)
        stream=load(output/'event_models.joblib',a,with_incidents=True)
        unique=packet.availableTime.drop_duplicates().sort_values().to_list()
        borders=[unique[len(unique)//3],unique[2*len(unique)//3]]
        seen=np.zeros(len(packet),bool);parts=[]
        for border in borders:
            take=(packet.availableTime<border).to_numpy() & ~seen
            if take.any():parts.append(stream.predict(packet.loc[take]))
            seen|=take
        if (~seen).any():parts.append(stream.predict(packet.loc[~seen]))
        import pandas as pd
        streamed=pd.concat(parts,ignore_index=True).set_index('event_row_id').loc[whole.event_row_id]
        np.testing.assert_allclose(streamed.score,whole.score,rtol=1e-9,atol=1e-9)
        np.testing.assert_array_equal(streamed.anomaly,whole.anomaly)
        np.testing.assert_array_equal(streamed.incident_active,whole.incident_active)
        actual_rows=int(base.estimator.n_samples_fit_) if hasattr(base.estimator,'n_samples_fit_') else base.fit_rows
        checks[a]=dict(one_sklearn_fault_estimator=True,original_rows_preserved=True,
            offline_and_raw_packet_scores_match=True,real_packet_prefix_causal=True,
            real_packet_streaming_scores_and_incidents_match_batch=True,
            selected_fit_rows=base.fit_rows,actual_estimator_fit_vectors=actual_rows,
            estimator_features=int(base.estimator.n_features_in_),parameters=base.estimator.get_params(),
            inference_has_no_label_columns=True,normal_statistical_fusion=model.record())
    source_folder='predictive_counts' if processor_kind=='predictive_row_context' else 'row_context_v2'
    source=joblib.load(ROOT/'reports/process_event_model'/source_folder/'original_features.joblib')
    test=source['partitions']['test'].copy()
    test['alert']=package['models']['if'].predict(test)[1]
    by_process=test.groupby('processId',observed=True).agg(rows=('alert','size'),
                   alerts=('alert','sum'),alert_rate=('alert','mean'),duration_median_ms=('duration_ms','median'))
    details=by_process.sort_values('rows',ascending=False).head(10).reset_index().to_dict('records')
    original=json.loads((ROOT/'reports/process_event_model/original_audit.json').read_text(encoding='utf-8'))
    assert original['synthetic_fit_rows']==0
    assert not original['synthetic_traffic_history_used']
    for name,frame in source['partitions'].items():
        assert not frame.benchmark_source.any()
    result=dict(passed=True,scope='execution-row metrics and actual saved-model runtime; not legacy minute-metric equivalence',
        evaluated_tasks=list(report['tasks']), excluded_tasks=report.get('excluded_tasks',{}),
        model_sha256=digest(output/'event_models.joblib'),metrics_independently_recomputed=True,
        estimators=checks,synthetic_training_Process_rows=0,
        original_normal_test_proxy_top_processes=details,
        original_S_alerts_are_not_labelled_false_positives=True,
        input_stop_not_implemented=True,production_deployed=False,
        no_claim_of_legacy_minute_metric_equivalence=True)
    write_json(output/'audit.json',result)
    print('AUDIT passed: saved metrics, original row mapping, real packet scores, causal prefix, three actual estimators',flush=True)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--folder',type=Path)
    args=parser.parse_args();audit(args.folder)
