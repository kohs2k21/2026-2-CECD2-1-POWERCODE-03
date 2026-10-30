"""Normal marginal transport of improved numeric evidence into frozen models.

This changes the feature generator and recalibrates alarms, not the dataset or
underlying IF/K-means/LOF estimator. The mapping uses independent normal cases
only; query histories are causal. Report this as feature-domain adaptation,
NOT as estimator retraining or an independent validation.
"""
from dataclasses import dataclass
import weakref
import numpy as np
from shared_model_v7.change_scan import Context
from shared_model_v6.features import Evidence,RAW

_NEAR_CACHE={}
_SCAN_CACHE={}


def cached(cache,key,f,calculate):
    item=cache.get(key)
    if item is not None and item[0]()is f:return item[1]
    value=calculate();cache[key]=(weakref.ref(f,lambda unused:cache.pop(key,None)),value)
    return value


class Marginal:
    def fit(self,source,target):
        self.source=np.sort(source);self.target=np.sort(target)
        self.scale=max(float(np.quantile(source,.95)-np.median(source)),.05)
        self.target_scale=max(float(np.quantile(target,.95)-np.median(target)),.15)
        return self
    def apply(self,x):
        p=np.searchsorted(self.source,x,side='left')/max(len(self.source),1)
        mapped=np.quantile(self.target,np.minimum(p,1.))
        return mapped+np.maximum(x-self.source[-1],0.)/self.scale*self.target_scale


@dataclass(frozen=True)
class Setting:
    algorithm:str
    point_weight:float=.5
    burst_kind:str='none'
    burst_weight:float=1.
    point_context:str='global'
    point_gain:float=1.
    cold_only:bool=False
    view:str='transported_evidence'
    transform:str='normal_marginal_transport'


class Transport:
    def __init__(self,s,reference,history):self.setting=s;self.reference=reference;self.context=Context(history)
    def point(self,f):
        near=cached(_NEAR_CACHE,(id(self.reference),id(f)),f,lambda:self.reference.transform(f)[:,0])
        return np.minimum(near,np.maximum(f.current_source_upper.to_numpy(float),0.)*self.setting.point_weight)
    @staticmethod
    def cold(f):
        # A stream's observed completions BEFORE the current minute, not case
        # age, truth type or a presumption that the prefix was healthy.
        seen=set();starts=[0]
        for i,key in enumerate(zip(f.case_id,f.window_start)):
            if key in seen:starts.append(i);seen=set()
            seen.add(key)
        starts.append(len(f));out=np.ones(len(f),bool)
        for a,b in zip(starts[:-1],starts[1:]):
            part=f.iloc[a:b];values=np.nan_to_num(part.completion_count_1m.to_numpy(float),nan=0.)
            for ids in part.groupby('case_id',sort=False).indices.values():
                out[a+ids]=(np.cumsum(values[ids])-values[ids])<3
        return out
    def burst(self,f):
        kind=self.setting.burst_kind
        signature=tuple(sorted(self.context.history.items()))
        s=cached(_SCAN_CACHE,(signature,id(f)),f,lambda:self.context.transform(f))
        if kind=='scan':return s.count_scan_peak.to_numpy()
        if kind=='scan_3m':return s.count_scan_memory_3m.to_numpy()
        if kind=='scan_tail':return np.minimum(s.count_scan_peak.to_numpy(),np.nan_to_num(f.traffic_tail_5m.to_numpy(float),nan=0.)*self.setting.burst_weight)
        if kind=='scan_own':return np.minimum(s.count_scan_peak.to_numpy(),np.nan_to_num(f.seq_burst_8m.to_numpy(float),nan=0.)*self.setting.burst_weight)
        if kind=='scan_agreement':return np.minimum(s.count_scan_peak.to_numpy(),s.count_scan_3m.to_numpy()*self.setting.burst_weight)
        raise ValueError(kind)
    def fit(self,normal):
        obs=normal.latency_assessed.to_numpy(bool)
        if self.setting.cold_only:obs=obs&self.cold(normal)
        point=self.point(normal);target=normal.current_source_upper.to_numpy(float)[obs]
        self.point_map=Marginal().fit(point[obs],target);self.point_maps={}
        if self.setting.point_context=='adapter':
            for adapter in normal.adapterType.unique():
                mask=obs&normal.adapterType.eq(adapter).to_numpy()
                if mask.sum()>=30 and normal.loc[mask,'case_id'].nunique()>=3:self.point_maps[adapter]=Marginal().fit(point[mask],target)
        self.burst_maps={}
        if self.setting.burst_kind!='none':
            b=self.burst(normal)
            for c in('traffic_tail_3m','traffic_tail_5m'):self.burst_maps[c]=Marginal().fit(b,normal[c].to_numpy(float))
        return self
    def transform(self,f):
        changed=f.copy();obs=f.latency_assessed.to_numpy(bool);point=self.point(f);p=self.point_map.apply(point)
        for adapter,m in getattr(self,'point_maps',{}).items():
            mask=f.adapterType.eq(adapter).to_numpy();p[mask]=m.apply(point[mask])
        p=p*self.setting.point_gain
        if getattr(self.setting,'cold_only',False):p=np.where(self.cold(f),p,f.current_source_upper.to_numpy(float))
        changed['current_source_upper']=np.where(obs,p,0.)
        if self.burst_maps:
            b=self.burst(f)
            for c,m in self.burst_maps.items():changed[c]=m.apply(b)
        return changed


class Encoder:
    def __init__(self,transport,history,fit):self.transport=transport;self.core=Evidence(history).fit(fit)
    def transform(self,f,view):return self.core.transform(self.transport.transform(f),view)


class Detector:
    def __init__(self,s,parent,transport,history,fit):
        self.setting=s;self.algorithm=s.algorithm;self.parent=parent;self.transport=transport
        self.encoder=Encoder(transport,history,fit);self.estimator=parent.estimator
        self.columns=tuple(dict.fromkeys([*parent.columns,*RAW,'log_max_duration_1m','log_mean_count_5m','adapterType','processId','native_execution_arrivals']))
        self.fit_rows=parent.fit_rows;self.fit_seconds=0.;self.threshold=None
    def fit(self,f):return self
    def score(self,f):return self.parent.score(self.transport.transform(f))
    def record(self):
        return {'algorithm':self.algorithm,'settings':self.setting.__dict__,'columns':self.columns,
            'estimator_refit':False,'estimator_count':1,'normal_marginal_feature_transport':True,
            'adapter_experts':False,'normal_reference_neighbourhood_not_a_second_fault_estimator':True,
            'formal_coverage_guarantee':False,'threshold':self.threshold}
