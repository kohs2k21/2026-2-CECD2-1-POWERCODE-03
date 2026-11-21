from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
from model_research_v13.data import write
from shared_model_v7.protocol import OUT,freeze,atomic_dump
from shared_model_v7.exact_count_context import Context,COLUMNS
from shared_model_v6.probe import bank
from shared_model_v6.search import curves


def values(x):
    short=x[:,:2].max(axis=1);long=x[:,2:].max(axis=1)
    return np.column_stack([x,short,long,np.minimum(short,long)])


def run():
    freeze();dev=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib');d=bank();path=OUT/'exact_count_features.joblib'
    if path.exists():pack=joblib.load(path)
    else:
        c=Context(dev['rank_history']);pack={'context':c,'train':values(c.transform(dev['train']).to_numpy()),'normal':values(c.transform(dev['normal_calibration']).to_numpy()),
            'probe':{t:values(c.transform(p['frame']).to_numpy())for t,p in d.items()}}
        atomic_dump(pack,path)
    names=[*COLUMNS,'exact_short_tail','exact_long_tail','exact_short_long_agreement'];result=[]
    y=d['time_burst']['y']
    for j,name in enumerate(names):
        q=np.r_[np.linspace(.8,.999,200),.9995,1.];threshold=np.quantile(pack['normal'][:,j],q,method='higher')
        rows=curves(y,pack['probe']['time_burst'][:,j],threshold);ok=[r for r in rows if r['recall']>=.55 and r['normal_false_positive_rate']<.05]
        best=max(ok,key=lambda r:r['precision'])if ok else None
        result.append({'feature':name,'oracle_P_at_R55':best});print('EXACT COUNTS',name,'P at R55',None if best is None else round(best['precision']*100,2),flush=True)
    write(OUT/'exact_count_diagnostics.json',{'feature_not_model_result':True,'adaptive_labels_used':True,'results':result})

if __name__=='__main__':run()
