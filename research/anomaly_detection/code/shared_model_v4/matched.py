"""Normal lookup moments/tails, not Process/Adapter expert detectors."""
import numpy as np
import pandas as pd

MATCHED=('matched_upper','matched_lower','matched_duration_z','matched_median_z',
    'matched_last_delay','matched_positive_ewma','matched_specific','matched_available')

def _stats(values):
    v=np.sort(np.asarray(values,float));q25,median,q75=np.quantile(v,[.25,.5,.75])
    return float(median),max(float(q75-q25),.05),v

class Profile:
    required={'adapterType','processId','duration_p95_ms','duration_p50_ms','log_mean_count_5m'}
    def fit(self,f):
        f=f.loc[f.latency_assessed].copy();f['_bin']=np.floor(f.log_mean_count_5m.fillna(0)/np.log(2)).astype(int)
        f['_p95']=np.log1p(f.duration_p95_ms);f['_p50']=np.log1p(f.duration_p50_ms)
        self.tables=[]
        for keys in(('adapterType','processId','_bin'),('adapterType','processId'),('adapterType','_bin'),('adapterType',)):
            table={}
            for key,g in f.groupby(list(keys),observed=True,sort=False):
                v=g.loc[np.isfinite(g._p95)&np.isfinite(g._p50)]
                if len(v)>=8:table[key if isinstance(key,tuple)else(key,)]=(_stats(v._p95),_stats(v._p50))
            self.tables.append((keys,table))
        self.global_stats=(_stats(f._p95.dropna()),_stats(f._p50.dropna()))
        return self
    def transform(self,f):
        out=pd.DataFrame(0.,index=f.index,columns=MATCHED)
        if not self.required<=set(f):return out
        bins=np.floor(f.log_mean_count_5m.fillna(0)/np.log(2)).astype(int).to_numpy()
        p95=np.log1p(f.duration_p95_ms.to_numpy(float));p50=np.log1p(f.duration_p50_ms.to_numpy(float))
        available=f.processId.notna().to_numpy()&f.adapterType.notna().to_numpy()
        observed=f.latency_assessed.to_numpy(bool)&np.isfinite(p95)&np.isfinite(p50)&available
        values=np.zeros((len(f),len(MATCHED)));values[:,7]=available.astype(float)
        groups=f.groupby([f.adapterType,f.processId,pd.Series(bins,index=f.index)],sort=False).indices
        for (adapter,process,b),ids in groups.items():
            refs=self.global_stats;level=4;lookup={'adapterType':adapter,'processId':process,'_bin':b}
            for j,(keys,tables)in enumerate(self.tables):
                found=tables.get(tuple(lookup[k]for k in keys))
                if found is not None:refs=found;level=j;break
            ids=ids[observed[ids]]
            if not len(ids):continue
            a,c=refs;z=np.clip((p95[ids]-a[0])/a[1],-100,100);n=len(a[2])
            upper=-np.log((n-np.searchsorted(a[2],p95[ids],side='left')+1)/(n+1))
            lower=-np.log((np.searchsorted(a[2],p95[ids],side='right')+1)/(n+1))
            values[ids,0]=upper;values[ids,1]=lower;values[ids,2]=z
            values[ids,3]=np.clip((p50[ids]-c[0])/c[1],-100,100);values[ids,6]=float(level<=1)
        if 'case_id'in f and 'window_start'in f:
            # Split reused paired replay IDs. Every stream must be chronological.
            seen=set();starts=[0]
            for i,key in enumerate(zip(f.case_id,f.window_start)):
                if key in seen:starts.append(i);seen=set()
                seen.add(key)
            starts.append(len(f))
            for a,b in zip(starts[:-1],starts[1:]):
                part=f.iloc[a:b];dates=pd.to_datetime(part.window_start,format='ISO8601')
                if not dates.groupby(part.case_id,sort=False).diff().dropna().eq(pd.Timedelta(minutes=1)).all():raise ValueError('Minute grid required')
                for ids in part.groupby('case_id',sort=False).indices.values():
                    ids=ids+a;last=0.;age=100;ew=0.
                    for i in ids:
                        if observed[i]:
                            last=max(values[i,2],0.);age=0;ew=.3*last+.7*ew
                            if last<.5:ew=0.
                        else:age+=1
                        values[i,4]=last*np.exp(-age/4)if age<=8 else 0.
                        values[i,5]=ew*np.exp(-age/4)if age<=8 else 0.
        out.loc[:,:]=values
        return out
