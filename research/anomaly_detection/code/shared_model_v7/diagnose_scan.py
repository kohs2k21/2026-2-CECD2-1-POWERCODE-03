from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
from sklearn.metrics import roc_auc_score
from model_research_v13.data import write
from shared_model_v7.protocol import OUT,freeze,atomic_dump
from shared_model_v7.change_scan import Context,COLUMNS
from shared_model_v6.probe import bank
from shared_model_v6.search import curves


def run():
    freeze();dev=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib');c=Context(dev['rank_history']);d=bank();result=[];raw={}
    for t,p in d.items():
        raw[t]=c.transform(p['frame']).to_numpy()
        for j,name in enumerate(COLUMNS):
            s=raw[t][:,j];rows=curves(p['y'],s,np.unique(np.quantile(s,np.linspace(0,1,1000))));valid=[r for r in rows if r['recall']>=.55 and r['normal_false_positive_rate']<.05]
            r=max(valid,key=lambda x:x['precision'])if valid else None
            result.append({'task':t,'feature':name,'ROCAUC':float(roc_auc_score(p['y'],s)),'oracle_P_at_R55':r})
            if t=='time_burst':print('SCAN',name,'AUC',round(result[-1]['ROCAUC'],3),'P55',None if r is None else round(r['precision']*100,2),flush=True)
    atomic_dump({'context':c,'probe':raw,'train':c.transform(dev['train']).to_numpy(),'normal':c.transform(dev['normal_calibration']).to_numpy()},OUT/'scan_features.joblib')
    write(OUT/'scan_diagnostics.json',{'oracle_not_an_inference_policy':True,'results':result})

if __name__=='__main__':run()
