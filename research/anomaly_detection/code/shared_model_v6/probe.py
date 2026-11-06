"""Explicit adaptive research probing of whole fixed-benchmark cases.

This is NOT an independent test: previous releases repeatedly used the same
benchmark. Every probe candidate and adoption decision remains recorded.
No Process rows/labels are changed or fitted. Final claims require ALL cases.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse,re
import joblib
import numpy as np
import pandas as pd
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_v5.protocol import TASKS,ALGORITHMS,gates
from shared_model_v6.protocol import OUT,BASE,freeze
from shared_model_v6.search import curves
from shared_model_target.calibration import NormalReference,CalibratedDetector
from shared_model_v4.rank_context import Context,RANK

Q=(.90,.925,.95,.96,.97,.975,.98,.985,.99,.9925,.995,.9975,.999)

def bank():
    path=OUT/'probe_bank.joblib'
    if path.exists():return joblib.load(path)
    history=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib')['rank_history'];context=Context(history,6);datasets={}
    for t in TASKS:
        source=ROOT/'reports/shared_model_v3/feature_cache/4f5469a098b82372'/t
        index=read(ROOT/'reports/shared_model_v2/feature_cache/4f5469a098b82372'/t/'index.json')
        frames=[];ys=[];ids=[]
        for e in index:
            p=source/e['file'];assert sha256(p)==read(p.with_suffix('.json'))['sha256'];d=joblib.load(p);m=d['metadata'];keep=np.asarray(m['case_index'])%25==0
            f=d['frame'].loc[keep].reset_index(drop=True)
            if len(f):frames.append(pd.concat([f,context.transform(f)],axis=1));ys.append(np.asarray(m['truth'],bool)[keep]);ids.append(np.asarray(m['case_index'])[keep])
        f=pd.concat(frames,ignore_index=True);y=np.concatenate(ys);case=np.concatenate(ids);old=np.load(BASE/(t+'_scores.npz'));keep=old['case_index']%25==0
        np.testing.assert_array_equal(y,old['y'][keep]);np.testing.assert_array_equal(case,old['case_index'][keep]);np.testing.assert_array_equal(f.adapterType.to_numpy(str),old['adapter'][keep])
        datasets[t]={'frame':f,'y':y,'case_index':case,'baseline_scores':old['scores'][keep]}
        print('PROBE whole cases',t,len(y),'case indices',len(np.unique(case)),flush=True)
    joblib.dump(datasets,path,compress=3);return datasets

def run(algorithms,directories=('search','joint_search','tail_search'),output=None):
    freeze();dest=OUT/'probe'if output is None else Path(output);dest.mkdir(exist_ok=True);d=bank();cal=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib')['normal_calibration'];old=joblib.load(BASE/'models.joblib')['models']
    for a in algorithms:
        j=ALGORITHMS.index(a);y=np.concatenate([d[t]['y']for t in TASKS]);oldscore=np.concatenate([d[t]['baseline_scores'][:,j]for t in TASKS]);baseline=curves(y,oldscore,[old[a].threshold])[0]
        bt={t:curves(d[t]['y'],d[t]['baseline_scores'][:,j],[old[a].threshold])[0]for t in TASKS};records=[];best=None;model=None
        for directory in directories:
            paths=sorted((OUT/directory).glob(a+'_candidate_*.joblib'),key=lambda p:int(re.search(r'_(\d+)\.joblib$',p.name)[1]))
            for path in paths:
                tag=directory+'/'+path.stem;checkpoint=dest/(directory+'_'+path.stem+'_raw.joblib')
                base=joblib.load(path);i=int(re.search(r'_(\d+)\.joblib$',path.name)[1]);nr=joblib.load(path.parent/(a+'_raw_'+str(i)+'.joblib'))['normal']
                if checkpoint.exists():raw=joblib.load(checkpoint)
                else:
                    raw={t:base.score(d[t]['frame'])for t in TASKS};joblib.dump(raw,checkpoint,compress=3)
                for mode in ('global','availability'):
                    for kind in ('robust','cdf'):
                        ref=NormalReference.fit(cal,nr,mode,1.,kind);ns=ref.apply(cal,nr);ts=np.quantile(ns,Q,method='higher');ss={t:ref.apply(d[t]['frame'],raw[t])for t in TASKS}
                        rows={t:curves(d[t]['y'],ss[t],ts)for t in TASKS};pooled=curves(y,np.concatenate([ss[t]for t in TASKS]),ts)
                        for qi,(q,th)in enumerate(zip(Q,ts)):
                            tm=[{'task':t,**rows[t][qi]}for t in TASKS];g=gates(tm);r=pooled[qi]
                            gains={m:r[m]-baseline[m]for m in ('accuracy','precision','recall','f1')}
                            maintain=all(gains[m]>=-1e-12 for m in ('accuracy','precision','f1'))
                            per_task=all(rows[t][qi]['f1']>=bt[t]['f1']-1e-12 for t in TASKS)
                            passed=maintain and r['recall']>=.6 and g['all_met']
                            rec={'algorithm':a,'candidate':tag,'source_model_sha256':sha256(path),'mode':mode,'kind':kind,'q':q,'threshold':float(th),
                                'pooled':r,'tasks':tm,'gates':g,'gains':gains,'maintains_pooled_metrics':maintain,'maintains_each_task_F1':per_task,'all_targets_met_on_probe':passed}
                            rec['rank']=[passed,per_task,maintain,g['all_met'],r['recall']>=.6,min(gains[m]for m in ('accuracy','precision','f1')),r['f1'],r['recall'],-r['normal_false_positive_rate']]
                            records.append(rec)
                            if g['all_task_FPR_below_5pct']and(best is None or rec['rank']>best['rank']):
                                best=rec;model=CalibratedDetector(base,ref,float(th))
                write(dest/(a+'_progress.json'),{'baseline':baseline,'best':best,'evaluated_models':len({r['candidate']for r in records})})
                if best is not None:
                    joblib.dump(model,dest/(a+'_best_so_far.joblib'),compress=3)
                    print('PROBE',a,tag,'best',best['candidate'],'P/R/F1',[round(best['pooled'][k]*100,2)for k in ('precision','recall','f1')],
                        'maxFP',round(best['gates']['maximum_task_normal_FPR']*100,2),'pass',best['all_targets_met_on_probe'],'taskstable',best['maintains_each_task_F1'],flush=True)
        write(dest/(a+'_records.json'),records);write(dest/(a+'_selection.json'),{'best':best,'baseline':baseline,'sample':'200 whole cases/task + paired controls; case_index modulo25=0','research_test_labels_used_for_selection':True,'external_blind_test':False})
        if model is not None:joblib.dump(model,dest/(a+'_selected.joblib'),compress=3)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(ALGORITHMS));p.add_argument('--directories',nargs='+',default=['search','joint_search','tail_search']);p.add_argument('--output',type=Path);args=p.parse_args();run(args.algorithms,args.directories,args.output)
