"""Development-only missed-anomaly analysis. No final benchmark labels read."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
from model_research_v13.data import write
from shared_model_improvement.run import _stats,ALGORITHMS

OUT=ROOT/'reports/shared_model_v3';BASE=ROOT/'reports/shared_model_v2_release'

def run():
    OUT.mkdir(exist_ok=True)
    d=joblib.load(ROOT/'reports/shared_model_v2/development.joblib')
    p=joblib.load(BASE/'models.joblib');records={}
    for a in ALGORITHMS:
        m=p['models'][a];records[a]={}
        for name in ('search','confirmation'):
            f=d[name]['frame'];y=d[name]['y'];s=m.score(f);pred=s>m.threshold
            rows=[]
            groups=f['scenario'].unique() if 'scenario'in f else ['ALL']
            for g in groups:
                mask=f.scenario.eq(g).to_numpy()if g!='ALL'else np.ones(len(f),bool)
                rows.append({'group':g,**_stats(y[mask],s[mask],m.threshold)})
            fn=y&~pred;fp=~y&pred
            records[a][name]={'metrics':_stats(y,s,m.threshold),'by_scenario':rows,
                'false_negative_latency_assessed':float(f.loc[fn,'latency_assessed'].mean()),
                'false_positive_latency_assessed':float(f.loc[fp,'latency_assessed'].mean()),
                'missed_feature_quantiles':{c:np.nanquantile(f.loc[fn,c].to_numpy(float),[.1,.5,.9]).tolist()for c in m.columns if c in f}}
    f=d['search']['frame'];y=d['search']['y'];n=d['normal_calibration']
    cols=[c for c in f.select_dtypes(include='number') if c not in ('case_index','window_is_anomaly','scenario_bits')]
    ranked=[]
    for c in cols:
        ns=n[c].fillna(0).to_numpy(float);s=f[c].fillna(0).to_numpy(float)
        for sign in (1,-1):
            for q in (.95,.975,.99,.995):
                t=float(np.quantile(ns*sign,q,method='higher'));v=_stats(y,s*sign,t)
                if v['normal_false_positive_rate']<.06:
                    ranked.append({'feature':c,'sign':sign,'q':q,'threshold':t,**v})
    ranked.sort(key=lambda r:r['f1'],reverse=True)
    write(OUT/'diagnostic.json',{'baseline_development':records,'univariate_search':ranked})
    print('BASELINE',[(a,{k:(round(v['metrics']['f1'],4),round(v['metrics']['recall'],4),round(v['metrics']['normal_false_positive_rate'],4))for k,v in r.items()})for a,r in records.items()],flush=True)
    print('TOP SINGLE FEATURES',[(r['feature'],r['sign'],r['q'],round(r['f1'],4),round(r['recall'],4),round(r['normal_false_positive_rate'],4))for r in ranked[:25]],flush=True)

if __name__=='__main__':run()
