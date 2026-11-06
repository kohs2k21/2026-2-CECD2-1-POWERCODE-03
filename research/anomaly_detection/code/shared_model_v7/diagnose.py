"""Cheap feasibility evidence: global threshold sweeps + observable-family errors."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
from model_research_v13.data import read,write
from shared_model_v7.protocol import OUT,BASE,TASKS,ALGORITHMS,freeze
from shared_model_v6.search import curves
from shared_model_v6.probe import bank
from shared_model_v6.features import Evidence

def run():
    freeze();models=joblib.load(BASE/'models.joblib')['models'];baseline=read(BASE/'summary.json')
    arrays={t:np.load(BASE/(t+'_scores.npz'))for t in TASKS};report={}
    for j,a in enumerate(ALGORITHMS):
        y=np.concatenate([arrays[t]['y']for t in TASKS]);s=np.concatenate([arrays[t]['scores'][:,j]for t in TASKS])
        thresholds=np.unique(np.r_[models[a].threshold,np.quantile(s,np.linspace(.7,.999,700))])
        totals=curves(y,s,thresholds);by={t:curves(arrays[t]['y'],arrays[t]['scores'][:,j],thresholds)for t in TASKS}
        candidates=[]
        for i,th in enumerate(thresholds):
            task=[{'task':t,**by[t][i]}for t in TASKS];r=totals[i];minr=min(z['recall']for z in task);maxfp=max(z['normal_false_positive_rate']for z in task)
            maintained=all(r[k]>=baseline['pooled'][j][k]-1e-12 for k in ('accuracy','precision','recall','f1'))
            candidates.append({'threshold':float(th),'pooled':r,'minimum_task_recall':minr,'maximum_task_FPR':maxfp,
                'maintained_pooled':maintained,'tasks':task,'passes':minr>=.55 and maxfp<.05 and maintained})
        relevant=[r for r in candidates if r['minimum_task_recall']>=.55 and r['maximum_task_FPR']<.05]
        best=max(relevant,key=lambda r:r['pooled']['f1'])if relevant else None
        report[a]={'global_threshold_passes':sum(r['passes']for r in candidates),'best_min_recall55_with_FPRcap':best}
        print('THRESHOLD DIAGNOSTIC',a,'passing',report[a]['global_threshold_passes'],'best',None if best is None else{key:best[key]for key in ('threshold','minimum_task_recall','maximum_task_FPR','maintained_pooled')},
            None if best is None else{k:best['pooled'][k]for k in ('accuracy','precision','recall','f1')},flush=True)
    d=bank();dev=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib');encoder=Evidence(dev['rank_history']).fit(dev['train'])
    groups=[]
    for t in TASKS:
        f=d[t]['frame'];values=encoder.transform(f,'peaks');dominant=np.argmax(values,axis=1);y=d[t]['y']
        for j,a in enumerate(ALGORITHMS):
            old=arrays[t];keep=old['case_index']%25==0;s=old['scores'][keep,j];assert np.array_equal(y,old['y'][keep]);p=s>models[a].threshold
            for g in range(5):
                mask=dominant==g
                groups.append({'task':t,'algorithm':a,'observed_dominant':['point','delay','trend','burst','gap'][g],
                    'normal':int((mask&~y).sum()),'anomaly':int((mask&y).sum()),'FP':int((mask&~y&p).sum()),'TP':int((mask&y&p).sum()),
                    'FN':int((mask&y&~p).sum())})
    write(OUT/'diagnostics.json',{'global_threshold_sweeps':report,'probe_group_errors':groups,
        'no_model_or_data_changes':True,'probe_uses_previously_reused_research_cases':True})

if __name__=='__main__':run()
