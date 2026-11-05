"""Development-only search. No fixed task test files or scores are read."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
from dataclasses import replace
import joblib
import numpy as np
from model_research_v13.data import read,write
from shared_model_v5.search_existing import prepare
from shared_model_v5.protocol import TASKS,ALGORITHMS,gates
from shared_model_v6.protocol import OUT,BASE,freeze
from shared_model_v6.models import Detector,Setting
from shared_model_target.calibration import NormalReference,CalibratedDetector
from shared_model_improvement.run import _stats

QUANTILES=(.95,.975,.98,.985,.99,.9925,.995,.9975)

def curves(y,s,thresholds):
    order=np.argsort(s);sy=np.asarray(y,bool)[order];v=np.asarray(s)[order];cum=np.r_[0,np.cumsum(sy)];cut=np.searchsorted(v,thresholds,side='right')
    n=len(y);pos=int(sy.sum());neg=n-pos;tp=pos-cum[cut];fp=n-cut-tp;fn=pos-tp;tn=neg-fp
    out=[]
    for a,b,c,d in zip(tn,fp,fn,tp):
        a,b,c,d=map(int,(a,b,c,d));out.append(dict(n=n,normal=neg,anomaly=pos,tn=a,fp=b,fn=c,tp=d,
            accuracy=(a+d)/n,precision=d/max(d+b,1),recall=d/max(pos,1),f1=2*d/max(2*d+b+c,1),normal_false_positive_rate=b/max(neg,1)))
    return out

def settings(a):
    for view in ('peaks','compact','raw'):
        for trans in ('linear','asinh'):
            for white in (False,True):yield Setting(a,view=view,transform=trans,white=white)
    if a=='if':
        for samples in (32,64,256,512):yield Setting(a,samples=samples,view='compact')
        for conditional in (False,True):yield Setting(a,conditional=conditional,samples=64,max_features=.5,view='compact')
    elif a=='kmeans':
        for k in (2,16,32,64):yield Setting(a,clusters=k,view='compact')
        for w in (2.,4.):yield Setting(a,view='compact',gap_weight=w,burst_weight=w)
    else:
        for k in (30,200,400,800):yield Setting(a,neighbors=k,view='compact')
        for w in (2.,4.):yield Setting(a,view='compact',gap_weight=w,burst_weight=w)

def run(algorithms,output=None):
    freeze();dest=OUT/'search' if output is None else Path(output);dest.mkdir(exist_ok=True);d=prepare();folds=('search','confirmation');cal=d['normal_calibration'];old=joblib.load(BASE/'models.joblib')['models']
    for a in algorithms:
        if(dest/(a+'_selected.joblib')).exists():raise FileExistsError('Completed selection preserved')
        baselines={k:_stats(d[k]['y'],old[a].score(d[k]['frame']),old[a].threshold)for k in folds}
        write(dest/(a+'_baseline_dev.json'),baselines);winners=[]
        for i,setting in enumerate(settings(a)):
            path=dest/(a+'_candidate_'+str(i)+'.joblib');base=Detector(setting,d['rank_history']).fit(d['train'])
            joblib.dump(base,path,compress=3);nr=base.score(cal);raw={k:base.score(d[k]['frame'])for k in folds}
            joblib.dump({'normal':nr,**raw},dest/(a+'_raw_'+str(i)+'.joblib'),compress=3)
            records=[]
            for mode in ('global','availability'):
                for kind in ('robust','cdf'):
                    for alpha in ((1.,)if mode=='global'else(.5,1.)):
                        ref=NormalReference.fit(cal,nr,mode,alpha,kind);ns=ref.apply(cal,nr);ss={k:ref.apply(d[k]['frame'],raw[k])for k in folds}
                        ts=np.quantile(ns,QUANTILES,method='higher');all_rows={};by_task={}
                        for k in folds:
                            all_rows[k]=curves(d[k]['y'],ss[k],ts)
                            by_task[k]={t:curves(d[k]['y'][mask],ss[k][mask],ts)for t in TASKS for mask in [d[k]['scenario']==t]}
                        for qi,(q,th)in enumerate(zip(QUANTILES,ts)):
                            fm={k:all_rows[k][qi]for k in folds};tm={k:[{'task':t,**by_task[k][t][qi]}for t in TASKS]for k in folds}
                            gg={k:gates(tm[k])for k in folds};fp=max(g['maximum_task_normal_FPR']for g in gg.values())
                            gains=[fm[k][m]-baselines[k][m]for k in folds for m in ('accuracy','precision','f1')]
                            recall=min(fm[k]['recall']for k in folds);gain=min(gains);maintain=gain>=-1e-12
                            r={'algorithm':a,'candidate':i,'setting':setting.__dict__,'mode':mode,'kind':kind,'alpha':alpha,'q':q,'threshold':float(th),
                                'folds':fm,'tasks':tm,'gates':gg,'maximum_task_FPR':fp,'minimum_metric_gain':gain,
                                'maintains_development_metrics':maintain,'minimum_fold_recall':recall,
                                'minimum_task_F1':min(g['minimum_task_F1']for g in gg.values()),
                                'mean_fold_F1':float(np.mean([fm[k]['f1']for k in folds])),
                                'eligible':maintain and recall>=.6 and all(g['all_met']for g in gg.values())}
                            # Infeasible candidates remain visible, never quietly published.
                            r['rank']=[r['eligible'],r['minimum_task_F1']>=.3,maintain,recall>=.6,gain,r['mean_fold_F1'],r['minimum_task_F1'],-fp]
                            records.append(r)
            feasible=[r for r in records if r['maximum_task_FPR']<=.05]
            best=max(feasible or records,key=lambda r:r['rank']);winners.append(best)
            write(dest/(a+'_candidate_'+str(i)+'.json'),records);write(dest/(a+'_winners.json'),winners)
            ref=NormalReference.fit(cal,nr,best['mode'],best['alpha'],best['kind']);model=CalibratedDetector(base,ref,best['threshold'])
            joblib.dump(model,dest/(a+'_calibrated_'+str(i)+'.joblib'),compress=3)
            print('NEW',a,i,setting.view,setting.transform,setting.white,'F1',round(best['mean_fold_F1']*100,2),
                'R',round(best['minimum_fold_recall']*100,2),'gain',round(best['minimum_metric_gain']*100,2),
                'taskFP',round(best['maximum_task_FPR']*100,2),'eligible',best['eligible'],flush=True)
        passing=[r for r in winners if r['maximum_task_FPR']<=.05]
        ranked=sorted(passing or winners,key=lambda r:r['rank'],reverse=True);chosen=ranked[0]
        joblib.dump(joblib.load(dest/(a+'_calibrated_'+str(chosen['candidate'])+'.joblib')),dest/(a+'_selected.joblib'),compress=3)
        write(dest/(a+'_selection.json'),{'chosen':chosen,'top_five':ranked[:5],'full_benchmark_read_by_search':False})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(ALGORITHMS));run(p.parse_args().algorithms)
