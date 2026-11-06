"""Freeze development selection, calibrate normal scores, then report reused test.

No benchmark generation, no label expansion, no test-driven hyperparameters.
The one-hour goal may fail: measured failures must remain visible.
"""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import argparse
import hashlib
import os
import joblib
import numpy as np
import pandas as pd
from ..common import DATA,ROOT,read_json,write_json,sha256
from ..benchmark import CHANNELS,load_split
from ..experiment import summarize,metadata_for_split
from .explore import OUT,initialize
from .model import StrictIntervalBank,HORIZONS


def code_hash():
    files=[Path(__file__),Path(__file__).with_name('model.py'),Path(__file__).with_name('protocol.json'),
           Path(__file__).parents[1]/'intervals/model.py',Path(__file__).parents[1]/'models.py']
    return hashlib.sha256(''.join(sha256(p) for p in files).encode()).hexdigest()


def frame_for(x,o,a,start,case):
    f=pd.DataFrame(x,columns=CHANNELS)
    f['collection_observed']=o;f['latency_available']=a
    f['timestamp']=pd.date_range(pd.Timestamp(int(start)),periods=len(x),freq='h')
    f['case_id']=str(case)
    return f


def _worker(payload):
    bank,x,o,a,start,case=payload
    c=bank.context_hours
    ref=bank.prepare_reference(frame_for(x[:c],o[:c],a[:c],start,case))
    return {h:bank.score_arrays(ref,x[c:],o[c:],a[c:],h) for h in HORIZONS}


def prediction_bank(bank,split):
    d=load_split(split,False)
    key=hashlib.sha256((code_hash()+repr(bank.record())+sha256(DATA/f'{split}_inputs.npz')).encode()).hexdigest()[:20]
    p=OUT/'cache'/f'api_scores_{split}_{key}.npz'
    if p.exists():
        with np.load(p) as z:return {h:z[f'score_{h}'] for h in HORIZONS},{h:z[f'reason_{h}'] for h in HORIZONS},d
    payloads=((bank,x,o,a,s,i) for x,o,a,s,i in zip(d['X'],d['collection_observed'],d['latency_available'],d['start_ns'],d['case_id']))
    results=[]
    with ProcessPoolExecutor(max_workers=min(6,os.cpu_count() or 2)) as pool:
        for i,result in enumerate(pool.map(_worker,payloads,chunksize=4)):
            results.append(result)
            if (i+1)%96==0:print('Strict API replay',split,i+1,'/',len(d['X']),flush=True)
    scores={h:np.asarray([r[h][0] for r in results]) for h in HORIZONS}
    reasons={h:np.asarray([r[h][1] for r in results]) for h in HORIZONS}
    np.savez_compressed(p,**{f'score_{h}':s for h,s in scores.items()},**{f'reason_{h}':s for h,s in reasons.items()})
    return scores,reasons,d


def summarize65(y,s,t,ids,split):
    r=summarize(y,s,t,ids,split)
    r['target_met']=(min(r['median'].values())>=.65 and r['maximum_scenario_fpr']<=.06
                     and r['pooled']['false_positive_rate']<=.06)
    r['median_target']=.65
    return r


def fit():
    initialize()
    if (OUT/'calibration.json').exists():raise FileExistsError('Calibration is frozen; do not silently retune reported test')
    selected=read_json(OUT/'development_selection.json')
    settings={int(h):{k:v for k,v in s.items() if k in ('density','weights','threshold','cap')} | {'quantile':.95}
              for h,s in selected['models'].items()}
    bank=StrictIntervalBank(settings)
    cal,_,_=prediction_bank(bank,'calibration')
    dev,_,d=prediction_bank(bank,'development')
    truth=load_split('development',True)['y']
    groups=metadata_for_split('development',d['case_id']).scenario.to_numpy()
    records={}
    for h,s in bank.settings.items():
        cap=s['cap']
        floor=float(np.nanquantile(cal[h],1-cap))
        normal_thresholds={g:float(np.quantile(dev[h][(groups==g)[:,None]&~truth&np.isfinite(dev[h])],1-cap,method='higher'))
                           for g in np.unique(groups)}
        s['threshold']=max(floor,*normal_thresholds.values())
        r=summarize65(truth,dev[h],s['threshold'],d['case_id'],'development')
        records[str(h)]={'normal_calibration_floor':floor,'normal_development_thresholds':normal_thresholds,
                         'development':r,'settings':s}
        print('Final development',h,r['median'],'max FPR',r['maximum_scenario_fpr'],flush=True)
    joblib.dump(bank,OUT/'models.joblib',compress=3)
    write_json(OUT/'calibration.json',{'code_sha256':code_hash(),'model_sha256':sha256(OUT/'models.joblib'),
        'selection_sha256':sha256(OUT/'development_selection.json'),'source_protocol_sha256':sha256(Path(__file__).with_name('protocol.json')),
        'normal_calibration_plus_development_selection_only':True,'test_used_for_selection':False,
        'previous_test_population_reused':True,'monitoring_state_carried':False,
        'model_record':bank.record(),'models':records})


def evaluate():
    initialize()
    if (OUT/'results.json').exists():print('Existing frozen evaluation retained',flush=True);return
    c=read_json(OUT/'calibration.json')
    assert code_hash()==c['code_sha256']
    assert sha256(OUT/'models.joblib')==c['model_sha256']
    bank=joblib.load(OUT/'models.joblib')
    values,reasons,data=prediction_bank(bank,'test')
    seals={}
    for h in HORIZONS:
        p=OUT/f'scores_{h}h.npz'
        np.savez_compressed(p,score=values[h],reason=reasons[h],case_id=data['case_id'],threshold=bank.settings[h]['threshold'])
        seals[str(h)]=sha256(p)
    write_json(OUT/'prediction_seal.json',{'code_sha256':code_hash(),'model_sha256':c['model_sha256'],
        'scores_sha256':seals,'current_prediction_arrays_saved_before_test_labels_loaded':True,
        'test_population_previously_reported':True,'fresh_blind_test_claimed':False})
    y=load_split('test',True)['y']
    results={str(h):summarize65(y,values[h],bank.settings[h]['threshold'],data['case_id'],'test') for h in HORIZONS}
    assert len({r['pooled']['assessed_hours'] for r in results.values()})==1
    write_json(OUT/'results.json',{'results':results,'model_sha256':c['model_sha256'],'code_sha256':code_hash(),
        'calibration_sha256':sha256(OUT/'calibration.json'),'source_dataset_sha256':sha256(DATA/'manifest.json'),
        'goal_met_all_lengths':all(r['target_met'] for r in results.values()),'median_goal':.65,'fpr_maximum':.06,
        'monitoring_state_carried':False,'outside_batch_monitoring_used':False,
        'previous_test_population_reused':True,'independent_new_blind_evaluation':False,
        'labels_expanded_or_point_adjusted':False,'batch_scores_not_hourly_localization':True})
    summary=[dict(analysis_hours=int(h),**{f'median_{k}':v for k,v in r['median'].items()},
        **{f'pooled_{k}':r['pooled'][k] for k in ('accuracy','precision','recall','f1','false_positive_rate')},
        maximum_scenario_fpr=r['maximum_scenario_fpr'],assessed_hours=r['pooled']['assessed_hours'],goal_met=r['target_met'])
        for h,r in results.items()]
    pd.DataFrame(summary).to_csv(OUT/'comparison.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame([dict(analysis_hours=int(h),scenario=task,**v) for h,r in results.items() for task,v in r['scenarios'].items()]).to_csv(OUT/'scenario_metrics.csv',index=False,encoding='utf-8-sig')
    print(pd.DataFrame(summary).to_string(index=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('stage',choices=['fit','evaluate']);a=p.parse_args()
    fit() if a.stage=='fit' else evaluate()
