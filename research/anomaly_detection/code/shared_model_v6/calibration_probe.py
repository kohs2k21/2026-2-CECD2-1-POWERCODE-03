"""Research-only recalibration probe on unchanged complete benchmark cases."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
import numpy as np
from model_research_v13.data import read,write
from shared_model_v6.protocol import OUT,BASE,freeze
from shared_model_v5.protocol import TASKS,ALGORITHMS,gates
from shared_model_v6.probe import bank,Q
from shared_model_v6.search import curves
from shared_model_v6.features import Evidence
from shared_model_v6.calibration import SignalReference
from shared_model_target.calibration import CalibratedDetector

def run(algorithms,probe_only=False):
    freeze();dest=OUT/('calibration_probe2'if probe_only else 'calibration_probe');dest.mkdir(exist_ok=True);d=bank();dev=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib');cal=dev['normal_calibration'];encoder=Evidence(dev['rank_history']).fit(dev['train'])
    for a in algorithms:
        j=ALGORITHMS.index(a);old=joblib.load(BASE/'models.joblib')['models'][a];y=np.concatenate([d[t]['y']for t in TASKS]);baseline=curves(y,np.concatenate([d[t]['baseline_scores'][:,j]for t in TASKS]),[old.threshold])[0]
        bt={t:curves(d[t]['y'],d[t]['baseline_scores'][:,j],[old.threshold])[0]for t in TASKS};records=[];best=None;winner=None
        sources={}
        for release in ('shared_model_v3_release','shared_model_v4_final'):
            sources[release]=joblib.load(ROOT/'reports'/release/'models.joblib')['models'][a].base
        for search in ('search','joint_search','tail_search'):
            p=OUT/search/(a+'_selected.joblib')
            if p.exists():sources[search]=joblib.load(p).base
        if probe_only:
            sources={'probe':joblib.load(OUT/'probe'/(a+'_selected.joblib')).base}
        for name,base in sources.items():
            nr=base.score(cal);raw={t:base.score(d[t]['frame'])for t in TASKS};joblib.dump(base,dest/(a+'_'+name+'_base.joblib'),compress=3);joblib.dump({'normal':nr,**raw},dest/(a+'_'+name+'_raw.joblib'),compress=3)
            for mode in ('dominant','availability_dominant','phase_dominant'):
                for alpha in (.5,.75,1.):
                    for kind in ('robust','cdf'):
                        ref=SignalReference.fit(cal,nr,encoder,mode,alpha,kind);ns=ref.apply(cal,nr);ts=np.quantile(ns,Q,method='higher');ss={t:ref.apply(d[t]['frame'],raw[t])for t in TASKS}
                        rows={t:curves(d[t]['y'],ss[t],ts)for t in TASKS};pooled=curves(y,np.concatenate([ss[t]for t in TASKS]),ts)
                        for qi,(q,th)in enumerate(zip(Q,ts)):
                            tm=[{'task':t,**rows[t][qi]}for t in TASKS];g=gates(tm);r=pooled[qi];gains={m:r[m]-baseline[m]for m in ('accuracy','precision','recall','f1')}
                            maintain=all(gains[m]>=-1e-12 for m in ('accuracy','precision','f1'));per_task=all(rows[t][qi]['f1']>=bt[t]['f1']-1e-12 for t in TASKS)
                            passed=maintain and r['recall']>=.6 and g['all_met'];rec={'algorithm':a,'source':name,'mode':mode,'alpha':alpha,'kind':kind,'q':q,'threshold':float(th),
                                'pooled':r,'tasks':tm,'gates':g,'gains':gains,'maintains_pooled_metrics':maintain,'maintains_each_task_F1':per_task,'all_targets_met_on_probe':passed}
                            rec['rank']=[passed,per_task,maintain,g['all_met'],r['recall']>=.6,min(gains[m]for m in ('accuracy','precision','f1')),r['f1'],r['recall'],-r['normal_false_positive_rate']]
                            records.append(rec)
                            if g['all_task_FPR_below_5pct']and(best is None or rec['rank']>best['rank']):best=rec;winner=CalibratedDetector(base,ref,float(th))
            write(dest/(a+'_records.json'),records);write(dest/(a+'_selection.json'),{'best':best,'baseline':baseline,'research_test_labels_used_for_selection':True,'external_blind_test':False})
            if winner is not None:joblib.dump(winner,dest/(a+'_selected.joblib'),compress=3)
            print('CALPROBE',a,name,'best',best['source']if best else None,'metrics',None if best is None else[round(best['pooled'][k]*100,2)for k in ('precision','recall','f1')],
                'pass',None if best is None else best['all_targets_met_on_probe'],'taskstable',None if best is None else best['maintains_each_task_F1'],flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(ALGORITHMS));p.add_argument('--probe-only',action='store_true');args=p.parse_args();run(args.algorithms,args.probe_only)
