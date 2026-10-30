"""Single-feature oracle diagnostics ONLY; not deployable task-based routing."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import numpy as np
from model_research_v13.data import read,write
from shared_model_v6.probe import bank
from shared_model_v6.search import curves
from shared_model_v7.protocol import OUT,BASE,freeze

def run():
    freeze();d=bank();base=read(BASE/'summary.json');results={}
    for ti,(t,p)in enumerate(d.items()):
        f=p['frame'];y=p['y'];rank=[]
        for c in ['current_source_upper','current_source_delay','log_max_duration_1m','seq_last_shift','seq_last_residual','rank_upper_memory','rank_duration_cusum',
            'arrival_ewma','arrival_dev_5m','seq_burst_8m','traffic_tail_5m','traffic_zero_logprob']:
            x=np.nan_to_num(f[c].to_numpy(float),nan=0.);ts=np.unique(np.quantile(x,np.linspace(.3,1.,700)))
            rows=curves(y,x,ts);eligible=[r for r in rows if r['recall']>=.55 and r['normal_false_positive_rate']<.05]
            best=max(eligible,key=lambda r:r['precision'])if eligible else None
            rank.append({'feature':c,'best_P_at_R55_FPR5':None if best is None else{key:best[key]for key in ('precision','recall','f1','normal_false_positive_rate')}})
        results[t]=rank
        print('ORACLE',t,[(r['feature'],None if r['best_P_at_R55_FPR5']is None else round(r['best_P_at_R55_FPR5']['precision']*100,2))for r in rank],flush=True)
    write(OUT/'oracle_single_feature_diagnostics.json',{'results':results,'not_deployable_per_task_policies':True})

if __name__=='__main__':run()
