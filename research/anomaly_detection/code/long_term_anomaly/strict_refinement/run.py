"""Freeze normal-trained AR refinement, calibrate without test, then evaluate."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import argparse
import hashlib
import joblib
import numpy as np
import pandas as pd
from ..common import ROOT,REPORT,DATA,read_json,write_json,sha256
from ..benchmark import load_split
from ..experiment import metadata_for_split
from ..strict_intervals.run import frame_for,summarize65
from ..strict_intervals.model import HORIZONS
from .experiment import OUT,initialize
from .shared_ar import learned_density,BANDWIDTHS,RANGES
from .model import RefinedBank,SharedARPoint


def code_hash():
    names=('run.py','model.py','ratio.py','shared_ar.py')
    files=[Path(__file__).with_name(n) for n in names]+[Path(__file__).parents[1]/'strict_intervals/model.py']
    return hashlib.sha256(''.join(sha256(p) for p in files).encode()).hexdigest()


def make_point(config):
    trained=learned_density();br=config['bandwidth_requests'];bd=config['bandwidth_duration']
    ru=RANGES.index(tuple(config['request_up']));rd=RANGES.index(tuple(config['request_down']));du=RANGES.index(tuple(config['duration_up']))
    fields=np.asarray([trained['curves'][br,ru][:,0,0],trained['curves'][br,rd][:,0,1],
                       trained['curves'][br,0][:,0,2],trained['curves'][bd,du][:,1,0],
                       trained['curves'][bd,0][:,1,1],trained['curves'][bd,0][:,1,2]])
    return SharedARPoint(trained['grid'],trained['buckets'],fields,config)


def _worker(args):
    bank,x,o,a,start,case=args;c=bank.context_hours
    ref=bank.prepare_reference(frame_for(x[:c],o[:c],a[:c],start,case))
    return {h:bank.score_arrays(ref,x[c:],o[c:],a[c:],h) for h in HORIZONS}


def predictions(bank,split):
    d=load_split(split,False)
    signature=hashlib.sha256((code_hash()+repr(bank.record())+sha256(DATA/f'{split}_inputs.npz')).encode()).hexdigest()[:20]
    p=OUT/'cache'/f'final_scores_{split}_{signature}.npz'
    if p.exists():
        with np.load(p) as z:return {h:z[f'score_{h}'] for h in HORIZONS},{h:z[f'reason_{h}'] for h in HORIZONS},d
    args=((bank,x,o,a,s,i) for x,o,a,s,i in zip(d['X'],d['collection_observed'],d['latency_available'],d['start_ns'],d['case_id']))
    results=[]
    with ThreadPoolExecutor(max_workers=6) as executor:
        for i,result in enumerate(executor.map(_worker,args)):
            results.append(result)
            if (i+1)%96==0:print('Frozen refinement replay',split,i+1,'/',len(d['X']),flush=True)
    scores={h:np.asarray([r[h][0] for r in results]) for h in HORIZONS}
    reasons={h:np.asarray([r[h][1] for r in results]) for h in HORIZONS}
    np.savez_compressed(p,**{f'score_{h}':v for h,v in scores.items()},**{f'reason_{h}':v for h,v in reasons.items()})
    return scores,reasons,d


def fit():
    initialize()
    if (OUT/'calibration.json').exists():raise FileExistsError('Frozen calibration retained; no silent retuning')
    source=read_json(OUT/'local_AR_search.json')['best']
    keys=('bandwidth_requests','bandwidth_duration','request_up','request_down','duration_up','weights','mode','cap','threshold','temperature')
    config={k:source[k] for k in keys}
    ordinary=joblib.load(REPORT/'strict_intervals/models.joblib')
    bank=RefinedBank(ordinary,make_point(config),{'old_model_sha256':sha256(REPORT/'strict_intervals/models.joblib'),
        'normal_training_input_sha256':sha256(DATA/'train_inputs.npz'),'normal_training_cases':64,
        'normal_training_reference_hours':672,'normal_fit_used_anomaly_truth':False})
    cal,_,_=predictions(bank,'calibration');dev,_,d=predictions(bank,'development')
    y=load_split('development',True)['y'];groups=metadata_for_split('development',d['case_id']).scenario.to_numpy()
    cap=config['cap'];floor=float(np.nanquantile(cal[1],1-cap))
    normal_thresholds={str(g):float(np.quantile(dev[1][(groups==g)[:,None]&~y&np.isfinite(dev[1])],1-cap,method='higher'))
                       for g in np.unique(groups)}
    bank.point.config['threshold']=max(floor,*normal_thresholds.values())
    development={str(h):summarize65(y,dev[h],bank.threshold(h),d['case_id'],'development') for h in HORIZONS}
    joblib.dump(bank,OUT/'models.joblib',compress=3)
    write_json(OUT/'final_protocol.json',{'median_target_each_length':.65,'maximum_fpr':.06,
        'evaluation_unit':'all original observed hours, scenario medians reported separately from pooled metrics',
        'normal_reference_hours':672,'analysis_hours':list(HORIZONS),'monitoring_state_carried':False,
        'outside_monitoring_batch_used':False,'benchmark_labels_or_strength_changed':False,
        'normal_fit':'64 normal train references, 672 hours each, shared AR innovation density',
        'selection':'development only, generic shift hypotheses, evidence weights and temperatures',
        'threshold':'normal calibration plus worst normal-development scenario quantile',
        'test_population_previously_reported':True,'new_independent_blind_evaluation':False,
        'other_four_length_models':'frozen original strict_intervals providers, unchanged'})
    write_json(OUT/'calibration.json',{'code_sha256':code_hash(),'model_sha256':sha256(OUT/'models.joblib'),
        'selection_sha256':sha256(OUT/'local_AR_search.json'),'protocol_sha256':sha256(OUT/'final_protocol.json'),
        'test_used_for_current_selection':False,'previous_test_population_reused':True,
        'normal_calibration_floor':floor,'normal_development_thresholds':normal_thresholds,
        'record':bank.record(),'development':development})
    print('Calibrated point model, development',development['1']['median'],'maximum FPR',development['1']['maximum_scenario_fpr'],flush=True)


def evaluate():
    initialize()
    if (OUT/'results.json').exists():print('Existing refinement test retained',flush=True);return
    c=read_json(OUT/'calibration.json');assert c['code_sha256']==code_hash()
    assert c['model_sha256']==sha256(OUT/'models.joblib')
    bank=joblib.load(OUT/'models.joblib');scores,reasons,d=predictions(bank,'test')
    seals={}
    for h in HORIZONS:
        p=OUT/f'scores_{h}h.npz';np.savez_compressed(p,score=scores[h],reason=reasons[h],threshold=bank.threshold(h),case_id=d['case_id'])
        seals[str(h)]=sha256(p)
    write_json(OUT/'prediction_seal.json',{'model_sha256':c['model_sha256'],'code_sha256':code_hash(),
        'scores_sha256':seals,'current_prediction_arrays_saved_before_test_truth_loaded':True,
        'previous_test_population_reused':True,'new_independent_blind_claim':False})
    y=load_split('test',True)['y']
    results={str(h):summarize65(y,scores[h],bank.threshold(h),d['case_id'],'test') for h in HORIZONS}
    unchanged={}
    for h in HORIZONS[1:]:
        with np.load(REPORT/'strict_intervals'/f'scores_{h}h.npz') as f:
            np.testing.assert_allclose(scores[h],f['score'],rtol=0,atol=1e-12,equal_nan=True)
        unchanged[str(h)]=True
    write_json(OUT/'results.json',{'results':results,'model_sha256':c['model_sha256'],'code_sha256':code_hash(),
        'calibration_sha256':sha256(OUT/'calibration.json'),'goal_met_all_lengths':all(v['target_met'] for v in results.values()),
        'median_goal':.65,'fpr_maximum':.06,'previous_test_population_reused':True,
        'new_independent_blind_evaluation':False,'monitoring_state_carried':False,'outside_batch_monitoring_used':False,
        'labels_expanded_or_point_adjusted':False,'other_length_scores_unchanged':unchanged})
    rows=[dict(analysis_hours=int(h),**{f'median_{k}':v for k,v in r['median'].items()},
        **{f'pooled_{k}':r['pooled'][k] for k in ('accuracy','precision','recall','f1','false_positive_rate')},
        maximum_scenario_fpr=r['maximum_scenario_fpr'],assessed_hours=r['pooled']['assessed_hours'],goal_met=r['target_met'])
        for h,r in results.items()]
    pd.DataFrame(rows).to_csv(OUT/'comparison.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame([dict(analysis_hours=int(h),scenario=t,**v) for h,r in results.items() for t,v in r['scenarios'].items()]).to_csv(OUT/'scenario_metrics.csv',index=False,encoding='utf-8-sig')
    print(pd.DataFrame(rows).to_string(index=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('stage',choices=['fit','evaluate']);a=p.parse_args()
    fit() if a.stage=='fit' else evaluate()
