"""Same fresh-seed cases for baseline and frozen candidate, never reselection."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
import numpy as np
from shared_model_target.confirm import confirm
from shared_model_improvement.run import _stats,ALGORITHMS
from model_research_v13.data import read,write

def run(out,seed):
    out=Path(out).resolve();confirm(out,seed)
    bank=joblib.load(out/'fresh_confirmation_v2_inputs.joblib')
    old=joblib.load(ROOT/'reports/shared_model_v2_release/models.joblib');new=joblib.load(out/'models.joblib')
    y=np.concatenate([d['y']for d in bank.values()]);records=[]
    for version,pkg in(('baseline',old),('adopted',new)):
        for a in ALGORITHMS:
            m=pkg['models'][a];s=np.concatenate([m.score(d['frame'])for d in bank.values()])
            records.append({'version':version,'method':a,**_stats(y,s,m.threshold)})
    write(out/'fresh_comparison.json',{'seed':seed,'rows':records,'cases_used_for_reselection':False,
        'generator':'original, not mild-v2 adjusted','source_pool_shared_not_external_blind':True})
    print('Fresh comparison',records,flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--seed',type=int,default=45100042)
    a=p.parse_args();run(a.output,a.seed)
