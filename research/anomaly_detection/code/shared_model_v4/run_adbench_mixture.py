from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
import pandas as pd
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_v4.adbench_training import build,OUT as PARENT,FLAGS
from shared_model_v4.adbench_mixture import Representation,Setting,Detector
from shared_model_v4.calibrate_adbench import upper_bound,standardized_f1
from shared_model_improvement.run import ALGORITHMS,_stats

OUT=PARENT/'mixture'

def run():
    OUT.mkdir(exist_ok=True)
    if(OUT/'selection.json').exists():raise FileExistsError('Mixture selection already frozen')
    d=build();y=d['development'].label.to_numpy(bool);caps=read(ROOT/'reports/shared_model_v4/protocol.json')['adbench_false_positive_caps'];best={};records=[];winners={}
    for components in (4,8,16,32):
        for regularization in (1e-6,1e-8):
            ep=OUT/f'normal_representation_{components}_{regularization}.joblib'
            if ep.exists():encoder=joblib.load(ep)
            else:
                encoder=Representation(components,regularization).fit(d['train']);joblib.dump(encoder,ep,compress=3)
            for a in ALGORITHMS:
                for view in ('local','summary','both'):
                    s=Setting(a,components,regularization,view);model=Detector(s,encoder).fit(d['train'])
                    ns=model.score(d['calibration']);ds=model.score(d['development']);name=f'{a}_{components}_{regularization}_{view}'
                    joblib.dump(model,OUT/(name+'.joblib'),compress=3)
                    for q in (.975,.98,.985,.99,.9925,.995,.9975,.999,1.):
                        t=float(np.quantile(ns,q,method='higher'));st=_stats(y,ds,t);ucb=upper_bound(st['fp'],st['normal']);eligible=ucb<=caps[a]/800*.5
                        r={'method':a,'candidate_file':name+'.joblib','settings':s.__dict__,'threshold_quantile':q,'threshold':t,'normal_FPR_UCB95':ucb,'eligible':eligible,**st};records.append(r)
                        key=(standardized_f1(r),-r['normal_false_positive_rate'])
                        if eligible and key>best.get(a,(-1.,0.)):best[a]=key;winners[a]=r
                    print('AD mixture',name,'dev best',best.get(a),flush=True)
            write(OUT/'search.json',records);write(OUT/'winners_so_far.json',winners)
    missing=set(ALGORITHMS)-set(winners)
    if missing:
        write(OUT/'rejection.json',{'reason':'No development candidate within the strict normal FPR confidence budget',
            'missing_algorithms':sorted(missing),'fixed_test_scored':False,'adopted':False})
        print('Mixture branch rejected before fixed test:',sorted(missing),flush=True);return
    for a in ALGORITHMS:
        r=winners[a];model=joblib.load(OUT/r['candidate_file']);model.threshold=r['threshold'];joblib.dump(model,OUT/(a+'_specific.joblib'),compress=3);r['model_sha256']=sha256(OUT/(a+'_specific.joblib'))
    write(OUT/'selection.json',{'selected':winners,'policy':'development normal FPR 95% exact upper bound <= HALF old budget; F1 standardized to 20% anomaly prevalence',
        'test_score_threshold_inversion':False,'additional_normal_GMM_representation':True,'ADBench_specific_not_service_model':True,'adaptive_not_blind':True})
    z=np.load(ROOT/'data/adbench_1000/dataset.npz');f=pd.DataFrame(z['X'],columns=z['columns'])
    for c in FLAGS:f[c]=f[c].astype(bool)
    f['adapterType']='ADBENCH_GLOBAL_CONTEXT';f['case_id']=z['sample_id'];rows=[];scores={}
    for a in ALGORITHMS:
        model=joblib.load(OUT/(a+'_specific.joblib'));s=model.score(f);scores[a]=s;st=_stats(z['y'],s,model.threshold)
        rows.append({'method':a,**st,'threshold':model.threshold,'F1_target_met':st['f1']>=.6,'FP_cap':caps[a],'FP_cap_met':st['fp']<=caps[a],
            'by_mode':{m:_stats(z['y'][z['generation_mode']==m],s[z['generation_mode']==m],model.threshold)for m in ('global','local','cluster','dependency')}})
    np.savez_compressed(OUT/'scores.npz',y=z['y'],sample_id=z['sample_id'],generation_mode=z['generation_mode'],**scores)
    write(OUT/'results.json',{'results':rows,'ADBench_specific_retrained':True,'same_service_model_transfer':False,
        'additional_learned_normal_GMM_preprocessor':True,'normal_only_train':True,'test_points_fit':0,'fixed_npz_sha256':sha256(ROOT/'data/adbench_1000/dataset.npz')})
    print('AD MIXTURE FIXED TEST',[(r['method'],r['f1'],r['fp'],r['F1_target_met']and r['FP_cap_met'])for r in rows],flush=True)

if __name__=='__main__':run()
