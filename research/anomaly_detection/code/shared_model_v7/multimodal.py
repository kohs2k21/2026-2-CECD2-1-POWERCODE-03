"""Conditional normal-duration neighbourhoods, including gaps between modes.

An upper percentile can miss a value BETWEEN two normal modes. These numeric
distances compare log(duration) and log(totalCount) to normal observations.
No fault labels are fitted. Training rows use leave-one-case-out references;
serving rows use the whole historical reference, never future query rows.
"""
import numpy as np
from scipy.spatial.distance import cdist
from scipy.spatial import cKDTree
from shared_model_v7.semantic import family

EXTRA=('duration_nearest_normal','duration_third_normal','duration_local_gap')


class Reference:
    def __init__(self,work_weight=1.,neighbours=3):self.work_weight=work_weight;self.neighbours=neighbours
    def context(self,f):
        return np.column_stack([f.adapterType.to_numpy(str),f.processId.to_numpy(str),f.processId.map(family).to_numpy(str)])
    def fit(self,f):
        self.tables=[];x=np.column_stack([f.log_max_duration_1m.to_numpy(float),f.log_mean_count_5m.to_numpy(float)])
        ok=f.latency_assessed.to_numpy(bool)&np.isfinite(x).all(axis=1);ctx=self.context(f);cases=f.case_id.to_numpy(str)
        for cols in((0,1),(0,2),(0,),()):
            table={};keys=[tuple(row[list(cols)])for row in ctx]
            for key in dict.fromkeys(keys):
                ids=np.array([i for i,k in enumerate(keys)if k==key and ok[i]])
                if len(ids)<8:continue
                values=x[ids];scale=np.maximum(np.quantile(values,.75,axis=0)-np.quantile(values,.25,axis=0),[.10,.35])
                coords=values/scale*np.array([1.,self.work_weight])
                spacing=cKDTree(coords).query(coords,k=min(4,len(values)))[0][:,-1]
                table[key]=(values,cases[ids],scale,np.maximum(spacing,.05))
            self.tables.append((cols,table))
        return self
    def transform(self,f,exclude_case=False):
        x=np.column_stack([f.log_max_duration_1m.to_numpy(float),f.log_mean_count_5m.to_numpy(float)])
        ok=f.latency_assessed.to_numpy(bool)&np.isfinite(x).all(axis=1);ctx=self.context(f);out=np.zeros((len(f),3))
        by={}
        for i,row in enumerate(ctx):
            if not ok[i]:continue
            for level,(cols,table)in enumerate(self.tables):
                key=tuple(row[list(cols)])
                if key in table:
                    by.setdefault((level,key),[]).append(i);break
        for(level,key),ids in by.items():
            ref=self.tables[level][1][key]
            values,cases,scale=ref[:3]
            if len(ref)==4:spacing=ref[3]
            else:
                coords=values/scale*np.array([1.,self.work_weight])
                spacing=np.maximum(cKDTree(coords).query(coords,k=min(4,len(values)))[0][:,-1],.05)
            ids=np.array(ids)
            # Workload distance selects similar counts; duration gaps remain
            # visible instead of being hidden inside a wide upper percentile.
            weight=np.array([1.,self.work_weight]);distance=cdist(x[ids]/scale*weight,values/scale*weight)
            if exclude_case:distance[f.case_id.iloc[ids].to_numpy(str)[:,None]==cases[None,:]]=np.inf
            k=min(self.neighbours,distance.shape[1]);near=np.partition(distance,k-1,axis=1)[:,:k];near.sort(axis=1)
            good=np.isfinite(near[:,0]);third=np.where(np.isfinite(near[:,-1]),near[:,-1],near[:,0])
            # Local normal spacing estimates how dense the closest reference
            # neighbourhood is. This is a feature, not an additional detector.
            closest=np.argmin(distance,axis=1);radius=spacing[closest]
            out[ids[good],0]=near[good,0];out[ids[good],1]=third[good];out[ids[good],2]=near[good,0]/radius[good]
        return np.arcsinh(np.minimum(out,100.))
