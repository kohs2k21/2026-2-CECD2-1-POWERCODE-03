"""Research diagnostics, never used at inference or for estimator fitting."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import numpy as np
from sklearn.metrics import roc_auc_score
from shared_model_v6.probe import bank
from model_research_v13.data import write
from shared_model_v7.protocol import OUT,freeze

def run():
    freeze();d=bank();results={}
    for t,p in d.items():
        f=p['frame'];y=p['y'];rank=[]
        for c in f.select_dtypes(include='number').columns:
            if any(key in c.lower()for key in ('anomal','label','scenario','target','case','event')):continue
            x=np.nan_to_num(f[c].to_numpy(float),nan=0.,posinf=0.,neginf=0.)
            if np.unique(x).size<2:continue
            auc=float(roc_auc_score(y,x))
            rank.append({'feature':c,'upper_ROCAUC':auc,'mean_normal':float(x[~y].mean()),'mean_anomaly':float(x[y].mean())})
        results[t]=sorted(rank,key=lambda z:z['upper_ROCAUC'],reverse=True)
        print(t,[(r['feature'],round(r['upper_ROCAUC'],3))for r in results[t][:18]],flush=True)
    write(OUT/'feature_diagnostics.json',results)

if __name__=='__main__':run()
