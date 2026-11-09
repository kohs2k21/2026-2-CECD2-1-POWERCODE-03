"""Development-only channel/tail alarm-budget balancing, no test data."""
import argparse
import numpy as np
import pandas as pd
from ..benchmark import load_split
from ..experiment import metadata_for_split
from ..common import write_json
from .explore import bank, OUT
from .moments import compact_metrics


def search():
    d = load_split("development", True)
    y = d["y"]
    groups = metadata_for_split("development",d['case_id']).scenario.to_numpy()
    normal = [(groups == g)[:,None] & ~y for g in np.unique(groups)]
    rng = np.random.default_rng(20261019)
    weights = [np.asarray([1., low, dur, dur*ld, den, den])
               for low in (.05,.25,1.) for dur in (.5,1.,1.5,2.,3.)
               for ld in (.05,.25,1.) for den in (0.,.02)]
    weights += [np.r_[1.,np.exp(rng.uniform(-3,1)), np.exp(rng.uniform(-1,1.5)),
                       np.exp(rng.uniform(-3,1)), np.exp(rng.uniform(-4,-1)),
                       np.exp(rng.uniform(-4,-1))] for _ in range(160)]
    records=[]
    for q in (.95,.99):
        f, _ = bank("development", "phase_median", q)
        valid = np.isfinite(f).any(axis=2)
        safe=np.nan_to_num(f,nan=0.)
        for w in weights:
            score = np.max(safe*w,axis=2)
            score[~valid]=np.nan
            for cap in (.05,.055):
                t=max(np.quantile(score[m & valid],1-cap,method="higher") for m in normal)
                result=compact_metrics(y,score,t,groups)
                records.append({"quantile":q,"weights":w.tolist(),"threshold":float(t),
                                "cap":cap,**result["median"],"max_fpr":float(result["maximum_scenario_fpr"])})
        best=max(records,key=lambda r:min(r[k] for k in ('accuracy','precision','recall','f1')))
        print("Balanced point candidate",best,flush=True)
    write_json(OUT/'point_balance_search.json',records)
    pd.DataFrame([{k:v for k,v in r.items() if k!='weights'} | {f'w{i}':x for i,x in enumerate(r['weights'])}
                  for r in records]).to_csv(OUT/'point_balance_search.csv',index=False)


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    search()
