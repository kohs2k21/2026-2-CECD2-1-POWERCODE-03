"""Adapter-conditioned NORMAL cutoff tables, not Adapter expert estimators.

IDs select a normal statistical reference; they are never numeric learned
inputs. Unknown Adapters use fixed family/history normal P99.5 fallback.
Groups absent from research observations ALSO retain P99.5, rather than being
silently disabled by label-aware optimization with no supporting cases.
"""
from dataclasses import dataclass
import numpy as np
from shared_model_v7.dense_policy import keys
from shared_model_v7.stratified_calibration import counts_at_thresholds


@dataclass
class Codec:
    encoder:object
    adapters:dict
    @classmethod
    def fit(cls,encoder,normal):return cls(encoder,{a:i for i,a in enumerate(sorted(normal.adapterType.astype(str).unique()))})
    @property
    def stride(self):return len(self.adapters)+1
    @property
    def n_groups(self):return 20*self.stride
    def values(self,f):
        adapter=f.adapterType.astype(str).map(self.adapters).fillna(len(self.adapters)).to_numpy(int)
        return keys(self.encoder,f)*self.stride+adapter


class TrafficSharedCodec(Codec):
    """Pool burst cutoffs across KNOWN Adapters to reduce calibration variance.

The traffic PMF already conditions on weekday/hour. Other duration families
retain Adapter context; unknown Adapters keep a separate original-core slot.
"""
    def values(self,f):
        observed=keys(self.encoder,f)
        adapter=f.adapterType.astype(str).map(self.adapters).fillna(len(self.adapters)).to_numpy(int)
        adapter=np.where((observed//4==3)&(adapter<len(self.adapters)),0,adapter)
        return observed*self.stride+adapter


def tables(normal,nkeys,cal,raw,groups,data,tasks,codec,legacy=None,legacy_all=False,unobserved_legacy=False):
    allcounts=[];thresholds=[];centers=[];provenance=[]
    for group in range(codec.n_groups):
        mask=nkeys==group;history_family=group//codec.stride;family=history_family//4;ref=normal[mask]
        fallback=False
        if len(ref)<20 or cal.loc[mask,'case_id'].nunique()<3:ref=normal[nkeys//codec.stride==history_family];fallback=True
        if not len(ref):ref=normal[nkeys//(4*codec.stride)==family]
        if not len(ref):ref=normal
        center=float(np.median(ref));width=max(float(np.quantile(ref,.95)-center),abs(center)*.01,1e-5)
        observed=any(np.any(groups[t]==group)for t in tasks)
        if observed:
            parent=normal[nkeys//codec.stride==history_family]
            if not len(parent):parent=ref
            parent_width=max(float(np.quantile(parent,.95)-np.median(parent)),abs(float(np.median(parent)))*.01,1e-5)
            # Include parent normal cutoffs, so adding Adapter context cannot
            # accidentally REMOVE a feasible parent-only threshold policy.
            cuts=np.unique(np.r_[ref,parent,np.max(ref)+3*width,np.max(parent)+3*parent_width])
            if legacy is not None and(legacy_all or family not in(0,3)):cuts=np.unique(np.r_[cuts,legacy])
        else:cuts=np.array([legacy if unobserved_legacy and legacy is not None else np.quantile(ref,.995,method='higher')])
        counts=np.zeros((len(tasks),len(cuts),4),int)
        for ti,t in enumerate(tasks):
            keep=groups[t]==group;counts[ti]=counts_at_thresholds(data[t]['y'][keep],raw[t][keep],cuts)
        selected={}
        for i,vector in enumerate(counts[:,:,3].T):selected[tuple(vector)]=i
        ids=np.array(sorted(selected.values()));allcounts.append(counts[:,ids]);thresholds.append(cuts[ids]);centers.append(center)
        provenance.append({'group':group,'normal_rows':int(mask.sum()),'reference_rows':len(ref),'family_history_fallback':fallback,
            'observed_in_probe':observed,'fixed_P995_if_unobserved':not observed and not unobserved_legacy,
            'fixed_legacy_if_unobserved':not observed and unobserved_legacy,
            'normal_Pareto_cutoffs':len(ids),'parent_normal_cutoff_union':observed})
    return allcounts,thresholds,np.array(centers),provenance
