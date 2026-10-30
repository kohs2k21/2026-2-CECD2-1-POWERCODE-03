"""ADBench-specific development search with conservative binomial screening."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import dataclasses
import joblib
import numpy as np
import pandas as pd
from shared_model_v4.adbench_training import build,OUT as PARENT,FLAGS
from shared_model_v4.adbench_quantile import Setting,Detector
from shared_model_v4.calibrate_adbench import upper_bound,standardized_f1
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_improvement.run import ALGORITHMS,_stats

OUT=PARENT/'quantile'

def candidates(a):
    for floor in (1e-8,1e-5):
        for shrinkage in (0.,.01):
            for view in ('rank','hybrid','summary'):
                yield Setting(a,floor=floor,shrinkage=shrinkage,quantile_view=view,neighbors=50)
    if a=='if':
        for n in (32,1024):yield Setting(a,floor=1e-8,shrinkage=0.,quantile_view='summary',samples=n)
    elif a=='kmeans':
        for k in (1,4,64):yield Setting(a,floor=1e-8,shrinkage=0.,quantile_view='rank',clusters=k)
    else:
        for k in (10,200,800):yield Setting(a,floor=1e-8,shrinkage=0.,quantile_view='rank',neighbors=k)

def run():
    OUT.mkdir(exist_ok=True)
    if(OUT/'selection.json').exists():raise FileExistsError('Quantile selection already frozen')
    d=build();y=d['development'].label.to_numpy(bool);caps=read(ROOT/'reports/shared_model_v4/protocol.json')['adbench_false_positive_caps'];winners={};records=[]
    for a in ALGORITHMS:
        best=(-1.,0.)
        for i,setting in enumerate(candidates(a)):
            model=Detector(setting).fit(d['train']);nr=model.score(d['calibration']);ds=model.score(d['development'])
            joblib.dump(model,OUT/(a+'_candidate_'+str(i)+'.joblib'),compress=3)
            # Quantile-derived cutoffs plus explicit normal-calibration margins;
            # useful when a tiny normal validation sample has heavier extremes.
            thresholds=[(q,1.,float(np.quantile(nr,q,method='higher')))for q in (.975,.98,.985,.99,.9925,.995,.9975,.999,1.)]
            thresholds += [(1.,m,float(nr.max()*m))for m in (1.1,1.25,1.5,2.)]
            for q,margin,t in thresholds:
                r={'method':a,'candidate':i,'settings':dataclasses.asdict(setting),'threshold_quantile':q,'threshold_margin':margin,'threshold':t,**_stats(y,ds,t)}
                r['normal_FPR_UCB95']=upper_bound(r['fp'],r['normal']);r['eligible']=r['normal_FPR_UCB95']<=caps[a]/800
                records.append(r);key=(standardized_f1(r),-r['normal_false_positive_rate'])
                if r['eligible']and key>best:best=key;winners[a]=r
            print('AD quantile',a,i,'dev best',best,flush=True)
        write(OUT/(a+'_search.json'),[r for r in records if r['method']==a]);write(OUT/'winners_so_far.json',winners)
    for a,r in winners.items():
        model=joblib.load(OUT/(a+'_candidate_'+str(r['candidate'])+'.joblib'));model.threshold=r['threshold'];joblib.dump(model,OUT/(a+'_specific.joblib'),compress=3);r['model_sha256']=sha256(OUT/(a+'_specific.joblib'))
    write(OUT/'selection.json',{'selected':winners,'test_score_inversion':False,'normal_only_fit':True,'ADBench_specific_retraining':True,'adaptive_not_blind':True,
        'policy':'95% exact binomial UCB for dev normal FPR <= previous budget; standardized 20% prevalence F1'})
    z=np.load(ROOT/'data/adbench_1000/dataset.npz');f=pd.DataFrame(z['X'],columns=z['columns'])
    for c in FLAGS:f[c]=f[c].astype(bool)
    f['adapterType']='ADBENCH_GLOBAL_CONTEXT';f['case_id']=z['sample_id'];rows=[];scores={}
    for a in ALGORITHMS:
        model=joblib.load(OUT/(a+'_specific.joblib'));s=model.score(f);scores[a]=s;st=_stats(z['y'],s,model.threshold)
        rows.append({'method':a,**st,'threshold':model.threshold,'F1_target_met':st['f1']>=.6,'FP_cap':caps[a],'FP_cap_met':st['fp']<=caps[a],
            'by_mode':{m:_stats(z['y'][z['generation_mode']==m],s[z['generation_mode']==m],model.threshold)for m in ('global','local','cluster','dependency')}})
    np.savez_compressed(OUT/'scores.npz',y=z['y'],sample_id=z['sample_id'],generation_mode=z['generation_mode'],**scores)
    write(OUT/'results.json',{'results':rows,'ADBench_specific_retrained':True,'same_service_model_transfer':False,'test_points_fit':0,
        'fixed_npz_sha256':sha256(ROOT/'data/adbench_1000/dataset.npz')})
    print('AD QUANTILE FIXED TEST',[(r['method'],r['f1'],r['fp'],r['F1_target_met']and r['FP_cap_met'])for r in rows],flush=True)

if __name__=='__main__':run()
