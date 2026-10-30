from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import itertools
import joblib
import numpy as np
from model_research_v13.data import write
from shared_model_v7.protocol import OUT,freeze,atomic_dump
from shared_model_v7.count_filter import Context,scores
from shared_model_v6.probe import bank
from shared_model_v6.search import curves


def run():
    freeze();dev=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib');d=bank();context=Context(dev['rank_history'])
    path=OUT/'count_filter_emissions.joblib'
    if path.exists():payload=joblib.load(path)
    else:
        payload={'context':context,'train':context.emissions(dev['train']),'normal':context.emissions(dev['normal_calibration']),
            'probe':{t:context.emissions(p['frame'])for t,p in d.items()}}
        atomic_dump(payload,path)
    rows=[]
    for i,(onset,recovery,tilt)in enumerate(itertools.product((.001,.01),(.1,.3),(.25,.5))):
        setting={'onset':onset,'recovery':recovery,'tilt':tilt};normal=scores(payload['normal'],**setting)
        raw={t:scores(e,**setting)for t,e in payload['probe'].items()}
        atomic_dump({'setting':setting,'normal':normal,'probe':raw},OUT/('count_filter_values_'+str(i)+'.joblib'))
        s=raw['time_burst'];y=d['time_burst']['y'];q=np.r_[np.linspace(.8,.999,150),.9995,1.];threshold=np.quantile(normal,q,method='higher')
        values=curves(y,s,threshold);eligible=[r for r in values if r['recall']>=.55 and r['normal_false_positive_rate']<.05]
        best=max(eligible,key=lambda r:r['precision'])if eligible else None
        record={'candidate':i,'setting':setting,'best_burst_P_at_R55_FPR5':best,'feature_not_IF_KMeans_LOF_result':True}
        rows.append(record);print('FILTER',i,setting,'P at R55',None if best is None else round(best['precision']*100,2),flush=True)
    write(OUT/'count_filter_diagnostics.json',{'adaptive_feature_diagnostic_only':True,'not_calibrated_fault_probability':True,'results':rows})

if __name__=='__main__':run()
