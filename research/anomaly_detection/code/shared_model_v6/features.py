"""Normal-only evidence scaling and compact channel summaries, not hard rules.

No labels, scenario identifiers, case duration or future values are features.
Existing causal features are grouped to reduce duplicated correlated inputs.
"""
import numpy as np
import pandas as pd
from shared_model_v4.rank_context import Context,RANK

BLOCKS={
    'point':('current_source_upper','current_source_delay'),
    'delay':('seq_last_shift','seq_delay_cusum','rank_upper_memory'),
    'trend':('rank_duration_cusum','seq_delay_slope','seq_delay_ewma'),
    'burst':('traffic_tail_3m','traffic_tail_5m','current_prefix_burst','current_arrival_excess'),
    'gap':('gap_expected_prefix','traffic_zero_logprob','gap_expected_recent'),
}
RAW=tuple(dict.fromkeys(c for cols in BLOCKS.values()for c in cols))

class Evidence:
    def __init__(self,history,conditional=False,transform='linear',tail=False):
        self.context=Context(history,6);self.conditional=conditional;self.transform_kind=transform;self.tail=tail
    def derive(self,f):
        return f if set(RANK)<=set(f)else pd.concat([f,self.context.transform(f)],axis=1)
    def keys(self,f):return (f.traffic_assessed.to_numpy(int)+2*f.latency_assessed.to_numpy(int)).astype(str)
    def fit(self,f):
        f=self.derive(f);x=np.nan_to_num(f[list(RAW)].to_numpy(float),nan=0.)
        self.global_stats=self.stats(x);self.tables={}
        if self.tail:self.tail_references=[np.sort(x[:,j])for j in range(x.shape[1])]
        if self.conditional:
            keys=self.keys(f)
            for key in np.unique(keys):
                mask=keys==key
                if mask.sum()>=300 and f.loc[mask,'case_id'].nunique()>=5:self.tables[key]=self.stats(x[mask])
        return self
    @staticmethod
    def stats(x):
        q50,q95=np.quantile(x,[.5,.95],axis=0)
        return q50,np.maximum(q95-q50,.15)
    def transform(self,f,view):
        f=self.derive(f);raw=np.nan_to_num(f[list(RAW)].to_numpy(float),nan=0.)
        if getattr(self,'tail',False):
            # Empirical upper-tail rarity learned only from normal fit data.
            # Equalizes different channel tail scales before pooling evidence.
            x=np.column_stack([-np.log((len(ref)-np.searchsorted(ref,raw[:,j],side='left')+1)/(len(ref)+1))for j,ref in enumerate(self.tail_references)])
            return self.summarize(x,view)
        center,scale=self.global_stats;x=np.maximum((raw-center)/scale,0.)
        if self.conditional:
            keys=self.keys(f)
            for key,(center,scale)in self.tables.items():
                ix=keys==key;x[ix]=np.maximum((raw[ix]-center)/scale,0.)
        x=np.minimum(x,100.)
        if self.transform_kind=='asinh':x=np.arcsinh(x)
        elif self.transform_kind=='sqrt':x=np.sqrt(x)
        return self.summarize(x,view)
    def summarize(self,x,view):
        blocks={key:x[:,[RAW.index(c)for c in cols]]for key,cols in BLOCKS.items()}
        out={}
        if view in ('radial','radial_pair'):
            peak=np.column_stack([z.max(axis=1)for z in blocks.values()]);ranked=np.sort(peak,axis=1)
            if view=='radial':return ranked[:,-1:]
            return np.column_stack([ranked[:,-1],ranked[:,-2],np.sqrt(np.mean(peak*peak,axis=1))])
        for key,z in blocks.items():
            out['evidence_'+key+'_peak']=z.max(axis=1)
            if view not in ('peaks','peaks_joint'):out['evidence_'+key+'_mean']=z.mean(axis=1)
        if view=='raw':return x
        if view=='compact':
            # Agreement is a continuous input, not a decision or status rule.
            out['evidence_delay_agreement']=np.sqrt(out['evidence_point_peak']*out['evidence_delay_peak'])
        if view.endswith('_joint'):
            peak=np.column_stack([out['evidence_'+key+'_peak']for key in blocks])
            out['evidence_joint_max']=peak.max(axis=1)
            out['evidence_joint_radius']=np.sqrt(np.mean(peak*peak,axis=1))
        return np.column_stack(list(out.values()))
    def names(self,view):
        if view=='raw':return ['normal_scaled_'+c for c in RAW]
        if view=='radial':return ['evidence_joint_max']
        if view=='radial_pair':return ['evidence_joint_max','evidence_joint_second','evidence_joint_radius']
        cols=[]
        for key in BLOCKS:
            cols.append('evidence_'+key+'_peak')
            if view not in ('peaks','peaks_joint'):cols.append('evidence_'+key+'_mean')
        if view=='compact':cols.append('evidence_delay_agreement')
        if view.endswith('_joint'):cols.extend(['evidence_joint_max','evidence_joint_radius'])
        return cols
