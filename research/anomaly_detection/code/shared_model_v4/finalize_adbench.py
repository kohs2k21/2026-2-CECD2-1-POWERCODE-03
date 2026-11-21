"""Separate tabular release; do not alias it as the operational log model."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
import pandas as pd
from shared_model_v4.adbench_training import build,OUT as PARENT,FLAGS
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_improvement.run import ALGORITHMS,_stats

OUT=ROOT/'reports/shared_model_v4_adbench_release'

def run():
    OUT.mkdir(exist_ok=True)
    if(OUT/'selection.json').exists():raise FileExistsError('ADBench release already frozen')
    d=build();sources={'if':PARENT/'quantile/if_specific.joblib','lof':PARENT/'quantile/lof_specific.joblib',
        'kmeans':PARENT/'assigned_cluster/kmeans_specific.joblib'};models={a:joblib.load(p)for a,p in sources.items()};caps=read(ROOT/'reports/shared_model_v4/protocol.json')['adbench_false_positive_caps']
    m=models['kmeans'];m.threshold=float(np.quantile(m.score(d['calibration']),.9975,method='higher'))
    selected={}
    for a in ALGORITHMS:
        path=OUT/(a+'_adbench_specific.joblib');joblib.dump(models[a],path,compress=3)
        selected[a]={'source':str(sources[a].relative_to(ROOT)),'model_sha256':sha256(path),**models[a].record(),
            'threshold_policy':'normal calibration P99.75'if a=='kmeans'else'unchanged quantile development selection'}
    write(OUT/'selection.json',{'selected':selected,'protocol':'ADBench-specific retraining on disjoint unused normal generator rows',
        'same_service_model_transfer':False,'normal_only_fit':True,'test_vectors_in_training_or_calibration':0,
        'mixture_preprocessor_used':False,'generation_mode_is_feature':False,'adaptive_reused_benchmark_not_blind':True,
        'prior_aggregate_results_informed_conservative_policy':True,'test_quantile_or_ROC_cutoff_inversion':False,
        'initial_failed_attempts_preserved':str(PARENT.relative_to(ROOT))})
    z=np.load(ROOT/'data/adbench_1000/dataset.npz');f=pd.DataFrame(z['X'],columns=z['columns'])
    for c in FLAGS:f[c]=f[c].astype(bool)
    f['adapterType']='ADBENCH_GLOBAL_CONTEXT';f['case_id']=z['sample_id'];rows=[];scores={}
    for a in ALGORITHMS:
        m=models[a];s=m.score(f);scores[a]=s;r={'method':a,**_stats(z['y'],s,m.threshold)}
        r.update({'threshold':m.threshold,'FP_cap':caps[a],'FP_cap_met':r['fp']<=caps[a],'F1_target_met':r['f1']>=.6,
            'by_mode':{k:_stats(z['y'][z['generation_mode']==k],s[z['generation_mode']==k],m.threshold)for k in ('global','local','cluster','dependency')}});rows.append(r)
    np.savez_compressed(OUT/'scores.npz',y=z['y'],sample_id=z['sample_id'],generation_mode=z['generation_mode'],**scores)
    write(OUT/'results.json',{'results':rows,'ADBench_specific_retrained':True,'same_service_model_transfer':False,'test_points_fit':0,
        'fixed_npz_sha256':sha256(ROOT/'data/adbench_1000/dataset.npz'),'ADBench_official_generator_1000_custom_samples_not_all_ADBench_real_datasets':True})
    print('FINAL ADBENCH SPECIFIC',[(r['method'],r['f1'],r['fp'],r['F1_target_met']and r['FP_cap_met'])for r in rows],flush=True)

if __name__=='__main__':run()
