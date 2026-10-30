"""Normal-only representation diagnostic, labelled ranking is research only."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
from sklearn.metrics import roc_auc_score
from shared_model_v7.protocol import OUT,atomic_dump,freeze
from shared_model_v7.expanded import prepare
from shared_model_v7.multimodal import Reference,EXTRA
from shared_model_v6.probe import bank
from model_research_v13.data import write
from shared_model_v6.search import curves


def run():
    freeze();dev=prepare();d=bank();result=[]
    for work in(0.,1.):
        reference=Reference(work).fit(dev['train']);raw={t:reference.transform(p['frame'])for t,p in d.items()}
        atomic_dump({'reference':reference,'probe':raw,'normal':reference.transform(dev['normal_calibration'])},OUT/('neighbourhood_'+str(int(work))+'.joblib'))
        for t,p in d.items():
            for j,c in enumerate(EXTRA):
                s=raw[t][:,j];rows=curves(p['y'],s,np.unique(np.quantile(s,np.linspace(0,1,800))));valid=[r for r in rows if r['recall']>=.55 and r['normal_false_positive_rate']<.05]
                r=max(valid,key=lambda r:r['precision'])if valid else None
                result.append({'work_weight':work,'task':t,'feature':c,'ROCAUC':float(roc_auc_score(p['y'],s)),'oracle_precision_at_Recall55_FPR5':r})
                print('NEIGHBOUR',work,t,c,'AUC',round(result[-1]['ROCAUC'],3),'best P at R55',None if r is None else round(r['precision']*100,2),flush=True)
    write(OUT/'neighbourhood_diagnostics.json',result)

if __name__=='__main__':run()
