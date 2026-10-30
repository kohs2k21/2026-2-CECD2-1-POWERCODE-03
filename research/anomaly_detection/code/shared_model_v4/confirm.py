"""Frozen-model extra checks; neither dataset is used for reselection."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib,argparse
import numpy as np
import pandas as pd
from shared_model_improvement.run import ALGORITHMS,_stats
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_v4.run_rank import OUT
from shared_model_v2.adbench_eval import FLAGS

def run(output=None,seed=None):
    global OUT
    if output is not None:OUT=Path(output).resolve()
    selection=read(OUT/'selection.json');assert sha256(OUT/'models.joblib')==selection['model_sha256']
    new=joblib.load(OUT/'models.joblib');old=joblib.load(ROOT/'reports/shared_model_v3_release/models.joblib')
    source=ROOT/'reports/shared_model_v3_release'
    if seed is not None:
        from shared_model_target.confirm import confirm
        confirm(OUT,seed);source=OUT
    receipt=read(source/'fresh_confirmation_v2_inputs.json')
    assert sha256(source/'fresh_confirmation_v2_inputs.joblib')==receipt['sha256'];bank=joblib.load(source/'fresh_confirmation_v2_inputs.joblib')
    records=[];all_y=np.concatenate([d['y']for d in bank.values()])
    for version,pkg in (('previous',old),('new',new)):
        for a in ALGORITHMS:
            model=pkg['models'][a];s=np.concatenate([model.score(d['frame'])for d in bank.values()])
            records.append({'version':version,'method':a,**_stats(all_y,s,model.threshold)})
    write(OUT/'fresh_comparison.json',{'rows':records,'seed':receipt['seed'],'not_used_for_reselection':True,
        'generator':'original replay, not mild-v2','previously_generated_for_v3_not_new_blind':seed is None,
        'fresh_seed_after_policy_freeze':seed is not None,'same_source_pool_not_external_blind':True})
    z=np.load(ROOT/'data/adbench_1000/dataset.npz');f=pd.DataFrame(z['X'],columns=z['columns'])
    for c in FLAGS:f[c]=f[c].astype(bool)
    f['adapterType']='ADBENCH_GLOBAL_CONTEXT';f['case_id']=z['sample_id'];rows=[];scores={}
    for a in ALGORITHMS:
        model=new['models'][a];s=model.score(f);scores[a]=s;r=_stats(z['y'],s,model.threshold)
        rows.append({'method':a,**r,'threshold':model.threshold,'FP_cap':selection['protocol']['adbench_false_positive_caps'][a],
            'F1_target_met':r['f1']>=.6,'FP_cap_met':r['fp']<=selection['protocol']['adbench_false_positive_caps'][a]})
    np.savez_compressed(OUT/'adbench_transfer_scores.npz',y=z['y'],sample_id=z['sample_id'],generation_mode=z['generation_mode'],**scores)
    write(OUT/'adbench_transfer.json',{'results':rows,'protocol':'same service-model transfer; no ADBench retraining',
        'native_context_available':False,'missing_native_history_explicit_zero_features_and_mask':True,
        'context_unavailable_regime_in_service_normal_training':False,'interpretation':'out-of-domain stress only; not an operational log F1',
        'fixed_npz_sha256':sha256(ROOT/'data/adbench_1000/dataset.npz')})
    print('Extra frozen replay',[(r['version'],r['method'],r['f1'],r['fp'])for r in records],flush=True)
    print('Service-model ADBench transfer',[(r['method'],r['f1'],r['fp'])for r in rows],flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'reports/shared_model_v4_final');p.add_argument('--seed',type=int)
    args=p.parse_args();run(args.output,args.seed)
