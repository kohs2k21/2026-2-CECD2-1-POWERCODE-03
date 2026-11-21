"""Normal references for Process families, preserving semantic DB2DB and PR stage.

Only underscore-delimited numeric run/interface tokens are generalized.
Categorical keys select normal statistics; no ID is a learned numeric feature.
"""
import re
import numpy as np
import pandas as pd

def family(value):
    return re.sub(r'(?<=_)\d+(?=_|$)','N',str(value))

class References:
    def context(self,f):
        return pd.DataFrame({'adapter':f.adapterType.to_numpy(str),'family':f.processId.map(family).to_numpy(str),
            'work':np.floor(np.nan_to_num(f.log_mean_count_5m.to_numpy(float),nan=0.)/np.log(2)).astype(int),
            'completed':np.minimum(f.completion_count_1m.fillna(0).to_numpy(int),3)},index=f.index)
    @staticmethod
    def stats(x):
        x=np.sort(np.asarray(x,float));med=float(np.median(x));q25,q75=np.quantile(x,[.25,.75])
        return med,max(float(q75-q25),.15),x
    def fit(self,f):
        ctx=self.context(f).reset_index(drop=True);y=f.log_max_duration_1m.to_numpy(float);ok=f.latency_assessed.to_numpy(bool)&np.isfinite(y)
        self.pooled=self.stats(y[ok]);self.tables=[]
        for cols in [('adapter','family','work','completed'),('adapter','family','work'),('adapter','family'),('adapter','work','completed'),('adapter','work'),('adapter',)]:
            table={}
            for key,ids in ctx.groupby(list(cols),sort=False).indices.items():
                ids=np.asarray(ids);ids=ids[ok[ids]]
                if len(ids)>=30 and f.iloc[ids].case_id.nunique()>=3:table[key if isinstance(key,tuple)else(key,)]=self.stats(y[ids])
            self.tables.append((cols,table))
        return self
    def transform(self,f):
        ctx=self.context(f).reset_index(drop=True);out=np.zeros((len(f),3));y=f.log_max_duration_1m.to_numpy(float)
        ok=f.latency_assessed.to_numpy(bool)&np.isfinite(y)
        for key,ids in ctx.groupby(['adapter','family','work','completed'],sort=False).indices.items():
            info=dict(zip(['adapter','family','work','completed'],key));ref=self.pooled
            for cols,table in self.tables:
                found=table.get(tuple(info[c]for c in cols))
                if found is not None:ref=found;break
            ids=np.asarray(ids);ids=ids[ok[ids]];med,scale,values=ref;n=len(values);upper=(n-np.searchsorted(values,y[ids],side='left')+1)/(n+1)
            out[ids,0]=np.maximum((y[ids]-med)/scale,0.);out[ids,1]=-np.log(upper)
            out[ids,2]=np.maximum(y[ids]-float(np.quantile(values,.95)),0.)/scale
        return np.minimum(out,100.)
