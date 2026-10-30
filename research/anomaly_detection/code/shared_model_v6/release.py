"""Honest research adoption: no pooled metric regression, no fake blind test."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import copy
import joblib
import numpy as np
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_v6.protocol import OUT,BASE,TASKS,ALGORITHMS,freeze
from shared_model_v6.evaluate import acceptance
from shared_model_improvement.run import _stats
from shared_model_v5.protocol import gates

def run():
    freeze();source=OUT/'attempt01';dest=OUT/'release';dest.mkdir(exist_ok=True)
    if(dest/'selection.json').exists():raise FileExistsError('Completed release preserved')
    pkg=joblib.load(source/'models.joblib');old=joblib.load(BASE/'models.joblib');baseline=read(BASE/'summary.json')
    bank={t:np.load(source/(t+'_scores.npz'))for t in TASKS};oldbank={t:np.load(BASE/(t+'_scores.npz'))for t in TASKS}
    cutoffs=read(source/'normal_cutoffs.json')['candidates'];chosen={};columns={};decisions=[]
    y=np.concatenate([bank[t]['y']for t in TASKS])
    for i,a in enumerate(ALGORITHMS):
        candidates=[]
        for family,m in [('new',pkg['models'][a]),('previous',old['models'][a])]+([('alternative_IF',pkg['alternative_IF'])]if a=='if'else[]):
            per_scores={t:(oldbank[t]['scores'][:,i]if family=='previous'else bank[t]['alternative_IF']if family=='alternative_IF'else bank[t]['scores'][:,i])for t in TASKS}
            settings=[{'q':'frozen','threshold':m.threshold}]
            if family!='previous':settings+=cutoffs['alternative_IF'if family=='alternative_IF'else a]
            for setting in settings:
                th=setting['threshold'];tasks=[{'task':t,**_stats(bank[t]['y'],per_scores[t],th)}for t in TASKS];r=_stats(y,np.concatenate([per_scores[t]for t in TASKS]),th);g=gates(tasks);b=baseline['pooled'][i]
                maintained={k:r[k]>=b[k]-1e-12 for k in ('accuracy','precision','f1')};stable=all(t['f1']>=next(d['metrics'][i]['f1']for d in baseline['datasets']if d['dataset']==t['task'])-1e-12 for t in tasks)
                passing=g['all_met']and all(maintained.values())and r['recall']>=.6 and r['normal_false_positive_rate']<=.05
                rec={'algorithm':a,'family':family,**setting,'pooled':r,'tasks':tasks,'gates':g,'maintained_pooled':maintained,
                    'all_task_F1_nonregression':stable,'all_targets_met':passing,'benchmark_labels_used_for_adoption':True,'external_blind_test':False}
                decisions.append(rec)
                if passing:candidates.append((rec,copy.deepcopy(m),per_scores))
        if not candidates:
            write(OUT/'adoption.json',{'decisions':decisions,'complete':False});raise RuntimeError('No fully passing candidate for '+a)
        # Non-regression across tasks is preferred, not hidden behind overall F1.
        winner,m,ss=max(candidates,key=lambda entry:(entry[0]['all_task_F1_nonregression'],entry[0]['pooled']['f1'],entry[0]['pooled']['recall'],-entry[0]['pooled']['normal_false_positive_rate']))
        m.threshold=winner['threshold'];chosen[a]=m;columns[a]=ss
        print('ADOPT',a,winner['family'],winner['q'],'P/R/F1',[round(winner['pooled'][k]*100,2)for k in ('precision','recall','f1')],
            'all-task-F1-nonregression',winner['all_task_F1_nonregression'],flush=True)
    write(OUT/'adoption.json',{'decisions':decisions,'complete':True,'policy':'Require pooled non-regression + Recall>=60%; prefer no task F1 regression; all tasks FPR<5%; old min/median F1 gates',
        'normal_cutoffs':'independent normal calibration only','adoption_uses_reused_research_benchmark_labels':True,'external_blind_test':False})
    result={**old,'models':chosen};joblib.dump(result,dest/'models.joblib',compress=3)
    write(dest/'selection.json',{'model_sha256':sha256(dest/'models.joblib'),'models':{a:m.record()for a,m in chosen.items()},
        'baseline_summary_sha256':sha256(BASE/'summary.json'),'benchmark_manifest_sha256':sha256(ROOT/'data/evaluation/manifest.json'),
        'research_adoption_sha256':sha256(OUT/'adoption.json'),'estimator_fit_test_rows':0,'external_blind_test':False})
    datasets=[];ss=[]
    for t in TASKS:
        z=bank[t];s=np.column_stack([columns[a][t]for a in ALGORITHMS]);np.savez_compressed(dest/(t+'_scores.npz'),y=z['y'],scores=s,case_index=z['case_index'],adapter=z['adapter'])
        datasets.append({'dataset':t,'metrics':[{'method':a,**_stats(z['y'],s[:,i],chosen[a].threshold)}for i,a in enumerate(ALGORITHMS)]});ss.append(s)
    s=np.concatenate(ss);pooled=[{'method':a,**_stats(y,s[:,i],chosen[a].threshold)}for i,a in enumerate(ALGORITHMS)];acc=acceptance(datasets,pooled,baseline)
    assert all(g['all_targets_met']for g in acc.values())
    write(dest/'summary.json',{'pooled':pooled,'datasets':datasets,'acceptance':acc,'all_targets_met':True,
        'protocol':{'decisions':len(y),'normal':int((~y).sum()),'anomaly':int(y.sum()),'active_tasks':list(TASKS),
            'unchanged_data_labels_severity':True,'point_adjustment':False,'single_estimator_each':True,'external_blind_test':False}})

if __name__=='__main__':run()
