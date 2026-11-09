"""Freeze development-selected alignment before evaluating the existing test."""
from pathlib import Path
import argparse
import hashlib
import joblib
import numpy as np
import pandas as pd
from ..common import ROOT,REPORT,DATA,read_json,write_json,sha256
from ..benchmark import load_split
from ..experiment import metadata_for_split
from ..strict_intervals.run import summarize65
from ..strict_refinement.flexible_hypotheses import fit_density
from .model import AlignedPoint,AlignedBank,load_bank,HORIZONS

OUT=REPORT/'strict_alignment';OLD=REPORT/'strict_refinement'


def code_hash():
    files=[Path(__file__),Path(__file__).with_name('model.py')]
    files.extend(Path(__file__).parents[1]/'strict_refinement'/n for n in
        ('model.py','ratio.py','quantile_warp.py','flexible_hypotheses.py','shared_ar.py','preprocessing_search.py'))
    return hashlib.sha256(''.join(sha256(p) for p in files).encode()).hexdigest()


def initialize():
    OUT.mkdir(exist_ok=True);(OUT/'cache').mkdir(exist_ok=True)
    path=OUT/'preservation.json'
    if path.exists():
        for name,h in read_json(path)['files'].items():assert sha256(ROOT/name)==h,name
        return
    files=read_json(OLD/'preservation.json')['files']
    for folder in (ROOT/'code/long_term_anomaly/strict_refinement',OLD):
        files.update({p.relative_to(ROOT).as_posix():sha256(p) for p in folder.rglob('*')
            if p.is_file() and 'cache' not in p.parts and '__pycache__' not in p.parts})
    write_json(path,dict(files=files))


def predictions(point,split):
    d=load_split(split,False);scores=[];reasons=[]
    for i,(x,o,a) in enumerate(zip(d['X'],d['collection_observed'],d['latency_available'])):
        ref=point.prepare_reference(x[:672],o[:672],a[:672])
        s,r=point.score_arrays(ref,x[672:],o[672:],a[672:]);scores.append(s);reasons.append(r)
        if (i+1)%96==0:print('Frozen alignment',split,i+1,flush=True)
    return np.asarray(scores),np.asarray(reasons),d


def fit():
    initialize()
    if (OUT/'calibration.json').exists():raise FileExistsError('Frozen candidate retained')
    source=read_json(OLD/'quantile_warp_search.json')['best'];baseline=joblib.load(OLD/'models.joblib')
    grid,buckets,density,prior=fit_density()
    np.testing.assert_array_equal(grid,baseline.point.grid);np.testing.assert_array_equal(buckets,baseline.point.buckets)
    config={**baseline.point.config,'scale_alpha':[0.,.25],
        'quantiles':source['quantiles'],'alignment_strength':source['alignment_strength']}
    assert source['scale_shrinkage']==1
    point=AlignedPoint(grid,buckets,baseline.point.curves,config,np.cumsum(density,axis=-1)*(grid[1]-grid[0]),prior)
    cal,_,_=predictions(point,'calibration');dev,_,d=predictions(point,'development')
    y=load_split('development',True)['y'];groups=metadata_for_split('development',d['case_id']).scenario.to_numpy()
    floor=float(np.nanquantile(cal,.941))
    thresholds={str(g):float(np.quantile(dev[(groups==g)[:,None]&~y&np.isfinite(dev)],.941,method='higher')) for g in np.unique(groups)}
    point.config['threshold']=max(floor,*thresholds.values())
    summary=summarize65(y,dev,point.config['threshold'],d['case_id'],'development')
    # The public backend path and selection-time implementation must agree.
    np.testing.assert_allclose(summary['median']['recall'],source['recall'],rtol=0,atol=1e-10)
    np.testing.assert_allclose(summary['median']['f1'],source['f1'],rtol=0,atol=1e-10)
    bank=AlignedBank(baseline.ordinary,point,{'normal_training_cases':64,'normal_reference_hours':672,
        'normal_fit_used_benchmark_anomalies':False,'development_selected_candidate':True,
        'original_model_sha256':sha256(OLD/'models.joblib')})
    joblib.dump(bank,OUT/'models.joblib',compress=3)
    write_json(OUT/'protocol.json',dict(goal_median_each_horizon=.65,fpr_maximum=.06,horizons=list(HORIZONS),
        normal_training_cases=64,normal_reference_hours=672,monitoring_state_carried=False,
        outside_batch_monitoring_used=False,benchmark_or_labels_changed=False,point_adjustment=False,
        other_four_providers_unchanged=True,test_population_previously_reported=True,new_independent_blind_test=False,
        candidate_selection='development quantile_warp_search best only; no test labels used for current settings',
        rule='report this single preselected frozen candidate regardless of final test outcome'))
    write_json(OUT/'calibration.json',dict(code_sha256=code_hash(),model_sha256=sha256(OUT/'models.joblib'),
        selection_sha256=sha256(OLD/'quantile_warp_search.json'),normal_floor=floor,normal_thresholds=thresholds,
        protocol_sha256=sha256(OUT/'protocol.json'),test_used_for_current_selection=False,
        record=bank.record(),development=summary))
    print('Frozen alignment development',summary['median'],'max FPR',summary['maximum_scenario_fpr'],flush=True)


def evaluate():
    initialize()
    if (OUT/'results.json').exists():raise FileExistsError('Frozen result retained')
    c=read_json(OUT/'calibration.json');assert c['code_sha256']==code_hash()
    assert c['model_sha256']==sha256(OUT/'models.joblib')
    bank=load_bank(OUT/'models.joblib');score,reason,d=predictions(bank.point,'test')
    seals={};arrays={}
    for h in HORIZONS:
        if h==1:s,r=score,reason
        else:
            with np.load(OLD/f'scores_{h}h.npz') as z:s,r=z['score'],z['reason']
        p=OUT/f'scores_{h}h.npz';np.savez_compressed(p,score=s,reason=r,threshold=bank.threshold(h),case_id=d['case_id'])
        arrays[h]=s;seals[str(h)]=sha256(p)
    write_json(OUT/'prediction_seal.json',dict(model_sha256=c['model_sha256'],code_sha256=code_hash(),
        scores_sha256=seals,predictions_saved_before_test_truth_loaded=True,previous_test_population_reused=True))
    y=load_split('test',True)['y']
    results={str(h):summarize65(y,arrays[h],bank.threshold(h),d['case_id'],'test') for h in HORIZONS}
    write_json(OUT/'results.json',dict(results=results,model_sha256=c['model_sha256'],code_sha256=code_hash(),
        calibration_sha256=sha256(OUT/'calibration.json'),goal_met_all_lengths=all(r['target_met'] for r in results.values()),
        median_goal=.65,maximum_fpr=.06,benchmark_and_truth_unchanged=True,monitoring_state_carried=False,
        outside_batch_monitoring_used=False,new_independent_blind_evaluation=False,point_adjustment=False))
    rows=[dict(analysis_hours=int(h),**{f'median_{k}':v for k,v in r['median'].items()},
        maximum_scenario_fpr=r['maximum_scenario_fpr'],goal_met=r['target_met']) for h,r in results.items()]
    pd.DataFrame(rows).to_csv(OUT/'comparison.csv',index=False,encoding='utf-8-sig')
    print(pd.DataFrame(rows).to_string(index=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('stage',choices=['fit','evaluate']);args=p.parse_args()
    fit() if args.stage=='fit' else evaluate()
