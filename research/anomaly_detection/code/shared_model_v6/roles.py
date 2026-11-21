"""Pairwise predictions and case-clustered F1 differences: no role invented."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
from model_research_v13.data import read,write
from shared_model_v6.protocol import OUT,TASKS,ALGORITHMS

def run():
    dest=OUT/'release';pkg=joblib.load(dest/'models.joblib');summary=read(dest/'summary.json');rows=[];pairs=[];rng=np.random.default_rng(20261018)
    for i,t in enumerate(TASKS):
        z=np.load(dest/(t+'_scores.npz'));y=z['y'];case=z['case_index'];n=int(case.max())+1
        p=np.column_stack([z['scores'][:,j]>pkg['models'][a].threshold for j,a in enumerate(ALGORITHMS)])
        bootstrap=rng.integers(0,n,size=(300,n));f1s=[]
        for j in range(3):
            pred=p[:,j];counts=np.column_stack([np.bincount(case,weights=mask,minlength=n)for mask in (y&pred,~y&pred,y&~pred)])
            sampled=counts[bootstrap].sum(axis=1);f1s.append(2*sampled[:,0]/np.maximum(2*sampled[:,0]+sampled[:,1]+sampled[:,2],1))
        metrics=summary['datasets'][i]['metrics'];rank=np.argsort([r['f1']for r in metrics])[::-1];first,second=map(int,rank[:2]);diff=(f1s[first]-f1s[second])*100;ci=np.quantile(diff,[.025,.975])
        rows.append({'task':t,'best_F1_model':ALGORITHMS[first],'runner_up':ALGORITHMS[second],
            'F1_gap_pp':100*(metrics[first]['f1']-metrics[second]['f1']),'paired_case_bootstrap_95pct_interval_pp':ci.tolist(),
            'interval_excludes_zero':bool(ci[0]>0),'interpretation':'Conditional sampling uncertainty only; does not correct adaptive model selection or prove production generalization'})
        for a,b in ((0,1),(0,2),(1,2)):
            union=p[:,a]|p[:,b];both=p[:,a]&p[:,b]
            pairs.append({'task':t,'models':[ALGORITHMS[a],ALGORITHMS[b]],'prediction_disagreement':float(np.mean(p[:,a]!=p[:,b])),
                'alarm_jaccard':float(both.sum()/max(union.sum(),1)),'anomaly_disagreement':float(np.mean(p[y,a]!=p[y,b]))})
    write(dest/'roles.json',{'best_by_task':rows,'pairwise':pairs,'bootstrap_unit':'whole paired normal/fault case; 5000 cases per task','bootstrap_draws':300,'thresholds_not_refit':True,'external_blind_test':False})
    print('ROLE EVIDENCE',rows,flush=True)

if __name__=='__main__':run()
