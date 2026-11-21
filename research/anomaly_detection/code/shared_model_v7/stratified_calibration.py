"""One estimator, normal-score cutoffs conditioned on observable context.

This is adaptive research calibration, NOT independent test performance or a
coverage guarantee. No truth task, event boundary, case index/length or future
value determines the inference group. IDs are used only as statistical keys;
an unseen Adapter falls back to its observed signal's normal reference.
"""
from dataclasses import dataclass
import numpy as np
import pandas as pd
from shared_model_v7.change_scan import Context
from shared_model_target.calibration import _surprise
from shared_model_v7.history_support import past_observations


class Groups:
    def __init__(self,encoder,mode,reference=None):self.encoder,self.mode,self.reference=encoder,mode,reference
    def fit(self,f):
        self.edges=np.unique(np.quantile(f.adapter_reference_log_duration,[.25,.5,.75]))
        if self.mode in('scan','scan_short','point_joint','joint_severity','history_scan'):
            self.scan=Context.__new__(Context);self.scan.history={}
            for key,p in self.encoder.context.pmfs.items():
                x=np.arange(len(p));mean=float(x@p);variance=float((x*x)@p-mean*mean)
                self.scan.history[key]=(mean,max(variance/max(mean,.1),1.))
            if self.mode in('scan','scan_short','history_scan'):self.scan_edges=np.unique(np.quantile(self.scan_values(f),[.5,.9,.99]))
            else:
                if self.reference is None:raise ValueError('Independent normal reference required')
                self.point_normal=np.sort(self.point_values(f))
                self.scan_normal=np.sort(self.scan_values(f))
                severity=self.severity(f);signals=np.argmax(self.encoder.transform(f,'peaks'),axis=1);self.severity_edges={}
                for g in range(5):
                    values=severity[signals==g]
                    if not len(values):values=severity
                    self.severity_edges[g]=np.unique(np.quantile(values,[.5,.9,.95,.97,.98,.99,.995,.999]))
        return self
    def scan_values(self,f):
        x=self.scan.transform(f)
        return np.minimum(x.count_scan_peak,x.count_scan_3m*4).to_numpy()if self.mode=='scan_short'else x.count_scan_peak.to_numpy()
    def point_values(self,f):
        return np.minimum(self.reference.transform(f)[:,0],np.maximum(f.current_source_upper.to_numpy(float),0.)*.5)
    def severity(self,f):
        point=_surprise(self.point_values(f),self.point_normal)
        if self.mode=='point_joint':return point
        return np.maximum(point,_surprise(self.scan_values(f),self.scan_normal))
    def values(self,f):
        signal=np.argmax(self.encoder.transform(f,'peaks'),axis=1)
        if self.mode=='adapter':category=f.adapterType.to_numpy(str)
        elif self.mode=='duration':category=np.searchsorted(self.edges,f.adapter_reference_log_duration.to_numpy(float)).astype(str)
        elif self.mode=='hour':category=(pd.to_datetime(f.window_start,format='ISO8601').dt.hour//6).to_numpy(str)
        elif self.mode=='reference':category=f.duration_reference_level.fillna('unavailable').astype(str).to_numpy()
        elif self.mode in('scan','scan_short'):category=np.searchsorted(self.scan_edges,self.scan_values(f)).astype(str)
        elif self.mode=='history':category=np.digitize(past_observations(f),[1,3,6]).astype(str)
        elif self.mode=='history_scan':
            h=np.digitize(past_observations(f),[1,3,6]);s=np.searchsorted(self.scan_edges,self.scan_values(f))
            category=np.array([str(a)+'_'+str(b)for a,b in zip(h,s)])
        elif self.mode in('point_joint','joint_severity'):
            x=self.severity(f);category=np.array([str(np.searchsorted(self.severity_edges[g],v))for g,v in zip(signal,x)])
        else:raise ValueError(self.mode)
        return signal,np.array([str(g)+'|'+c for g,c in zip(signal,category)])


@dataclass
class StratifiedReference:
    groups:object
    cutoffs:dict
    centers:dict
    fallback:dict
    support:dict
    levels:dict
    alpha:float=1.
    kind:str='normal_context_quantile'
    mode:str='observed_signal_context'
    @property
    def tables(self):return self.cutoffs
    @classmethod
    def fit(cls,cal,normal,groups,keys,levels,shrinkage=0.):
        signal,category=groups.values(cal);cuts={};centers={};support={};fallback={}
        for g in range(5):
            values=normal[signal==g]
            if len(values)<40:values=normal
            fallback[g]=(float(np.median(values)),float(np.quantile(values,.99,method='higher')))
        for key,q in zip(keys,levels):
            mask=category==key;g=int(key.split('|')[0]);local=normal[mask];global_values=normal[signal==g]
            if len(global_values)<40:global_values=normal
            cases=cal.loc[mask,'case_id'].nunique();support[key]={'minutes':int(mask.sum()),'cases':int(cases)}
            if len(local)<20 or cases<3:local=global_values;weight=0.
            else:weight=len(local)/(len(local)+shrinkage) if shrinkage else 1.
            cuts[key]=float(weight*np.quantile(local,q,method='higher')+(1-weight)*np.quantile(global_values,q,method='higher'))
            centers[key]=float(weight*np.median(local)+(1-weight)*np.median(global_values))
        return cls(groups,cuts,centers,fallback,support,dict(zip(keys,map(float,levels))))
    def apply(self,f,scores):
        signal,category=self.groups.values(f);out=np.empty(len(f),float)
        for key in np.unique(category):
            mask=category==key;g=int(key.split('|')[0]);center,cut=self.fallback[g]
            center=self.centers.get(key,center);cut=self.cutoffs.get(key,cut)
            out[mask]=1.+(scores[mask]-cut)/max(abs(cut-center),1e-5)
        return out


def counts_at_thresholds(y,scores,thresholds):
    order=np.argsort(scores,kind='stable');ss=scores[order];yy=y[order].astype(int)
    cumulative=np.r_[0,np.cumsum(yy)];split=np.searchsorted(ss,thresholds,side='right')
    fn=cumulative[split];tn=split-fn;positive=yy.sum();negative=len(yy)-positive
    return np.column_stack([tn,negative-tn,fn,positive-fn])
