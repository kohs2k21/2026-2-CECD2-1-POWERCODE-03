"""Frozen normal-mixture geometry diagnostic, not a selected production model."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
import pandas as pd
from scipy.stats import chi2
from sklearn.mixture import GaussianMixture
from threadpoolctl import threadpool_limits
from model_research_v13.data import read,write
from shared_model_improvement.run import _stats
from shared_model_v2.adbench_eval import FLAGS,COLUMNS

def run():
    out=ROOT/'reports/shared_model_v4_mixture_probe';out.mkdir(exist_ok=True)
    source=joblib.load(ROOT/'reports/shared_model_v2/development.joblib')['train']
    source=source.loc[source[list(FLAGS)].astype(bool).all(axis=1)]
    x=source[list(COLUMNS)].to_numpy(float);median=np.nan_to_num(np.nanmedian(x,axis=0));x=np.where(np.isnan(x),median,x)
    q10,q90=np.quantile(x,[.1,.9],axis=0);scale=np.maximum(q90-q10,.5);variable=np.ptp(x,axis=0)>1e-8
    normalized=((x-median)/scale)[:,variable]
    ids=np.random.default_rng(48300042).choice(len(x),min(2400,len(x)),False)
    with threadpool_limits(limits=2):gm=GaussianMixture(n_components=8,reg_covar=1e-6,random_state=48300042).fit(normalized[ids])
    # A fixed analytic Gaussian radius, not inverted from target labels/FPR.
    threshold=float(chi2.ppf(.995,int(variable.sum())))
    joblib.dump({'mixture':gm,'median':median,'scale':scale,'variable':variable,'threshold':threshold},out/'normal_profile.joblib',compress=3)
    z=np.load(ROOT/'data/adbench_1000/dataset.npz');v=((z['X']-median)/scale)[:,variable]
    radii=np.column_stack([np.sum(((v-m)@p)**2,axis=1)for m,p in zip(gm.means_,gm.precisions_cholesky_)])
    score=np.min(radii,axis=1)
    result={'all':_stats(z['y'],score,threshold),'modes':{}}
    for mode in np.unique(z['generation_mode']):
        mask=z['generation_mode']==mode;result['modes'][mode]=_stats(z['y'][mask],score[mask],threshold)
    write(out/'diagnostic.json',result);print(result,flush=True)

if __name__=='__main__':run()
