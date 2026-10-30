"""Label-aware upper-bound-style diagnostics, NOT a serving task router."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
from shared_model_v6.probe import bank
from shared_model_v7.protocol import BASE,OUT,ALGORITHMS,freeze
from shared_model_v7.change_scan import COLUMNS
from model_research_v13.data import write


def run():
    freeze();b=bank()['time_burst'];y=b['y'];scan=joblib.load(OUT/'scan_features.joblib')['probe']['time_burst']
    old=np.load(BASE/'time_burst_scores.npz');score=old['scores'][old['case_index']%25==0];result=[]
    for ai,a in enumerate(ALGORITHMS):
        t=np.unique(np.quantile(score[:,ai],np.linspace(.1,1,110)));p=score[:,ai][None,:]>t[:,None]
        for j,name in enumerate(COLUMNS):
            cut=np.unique(np.quantile(scan[:,j],np.linspace(.1,1,110)));q=scan[:,j][None,:]>cut[:,None]
            tp=p[:,y].astype(np.int32)@q[:,y].astype(np.int32).T;fp=p[:,~y].astype(np.int32)@q[:,~y].astype(np.int32).T
            precision=tp/np.maximum(tp+fp,1);recall=tp/y.sum();fpr=fp/(~y).sum();valid=(recall>=.55)&(fpr<.05)
            best=None
            if valid.any():
                i,k=np.unravel_index(np.argmax(np.where(valid,precision,-1)),precision.shape)
                best={'precision':float(precision[i,k]),'recall':float(recall[i,k]),'FPR':float(fpr[i,k]),'tp':int(tp[i,k]),'fp':int(fp[i,k]),
                    'model_cutoff':float(t[i]),'scan_cutoff':float(cut[k])}
            result.append({'algorithm':a,'feature':name,'oracle_pair_at_Recall55':best})
        print('SCORE ALIGNMENT',a,sorted([(r['feature'],round(r['oracle_pair_at_Recall55']['precision']*100,2))for r in result if r['algorithm']==a and r['oracle_pair_at_Recall55']],key=lambda z:z[1],reverse=True)[:4],flush=True)
    write(OUT/'score_alignment_diagnostics.json',{'task':'time_burst','not_a_serving_policy':True,'adaptive_labels_used':True,'results':result})

if __name__=='__main__':run()
