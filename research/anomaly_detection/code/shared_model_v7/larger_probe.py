"""One thousand fixed whole cases/task, not a newly generated benchmark."""
import joblib
import numpy as np
import pandas as pd
from model_research_v13.data import read
from model_evaluation.data import sha256
from shared_model_v4.rank_context import Context
from shared_model_v7.protocol import ROOT,OUT,BASE,TASKS,atomic_dump


def bank():
    path=OUT/'probe1000.joblib'
    if path.exists():return joblib.load(path)
    history=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib')['rank_history']
    context=Context(history,6);data={}
    for t in TASKS:
        ff=[];yy=[];ii=[]
        index=read(ROOT/'reports/shared_model_v2/feature_cache/4f5469a098b82372'/t/'index.json')
        for e in index:
            p=ROOT/'reports/shared_model_v3/feature_cache/4f5469a098b82372'/t/e['file']
            assert sha256(p)==read(p.with_suffix('.json'))['sha256']
            pack=joblib.load(p);m=pack['metadata'];ids=np.asarray(m['case_index']);keep=ids%5==0
            f=pack['frame'].loc[keep].reset_index(drop=True)
            ff.append(pd.concat([f,context.transform(f)],axis=1))
            yy.append(np.asarray(m['truth'],bool)[keep]);ii.append(ids[keep])
        f=pd.concat(ff,ignore_index=True);y=np.concatenate(yy);ids=np.concatenate(ii)
        old=np.load(BASE/(t+'_scores.npz'));keep=old['case_index']%5==0
        np.testing.assert_array_equal(y,old['y'][keep]);np.testing.assert_array_equal(ids,old['case_index'][keep])
        np.testing.assert_array_equal(f.adapterType.to_numpy(str),old['adapter'][keep])
        data[t]={'frame':f,'y':y,'case_index':ids,'baseline_scores':old['scores'][keep]}
        print('LARGER WHOLE CASE BANK',t,len(y),len(np.unique(ids)),flush=True)
    atomic_dump(data,path);return data


if __name__=='__main__':bank()
