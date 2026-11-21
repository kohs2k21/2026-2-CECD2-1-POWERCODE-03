"""Train-only one-sided rank evidence views followed by a single estimator.

These are transformed numeric inputs, not hard rules or model ensembles.
Normal feature CDFs are frozen; no test labels or cohorts are used in fitting.
"""
from dataclasses import dataclass,asdict
import numpy as np
from shared_model_v3.models import Detector as Parent,Setting as ParentSetting
from shared_model_v3.adaptive import ADAPTIVE
from shared_model_v3.features import SEQUENCE
from shared_model_target.current_features import CURRENT
from adapter_feature_ablation.features import ADAPTER

@dataclass(frozen=True)
class Setting(ParentSetting):
    view:str='rank'
    evidence_weight:float=1.
    flow:bool=False

class Detector(Parent):
    def fit(self,f):
        s=self.setting
        self.inputs=(*CURRENT,*(c for c in ADAPTER if c!='adapter_workload_z'),*SEQUENCE,
            *(ADAPTIVE if s.flow else ADAPTIVE[:-2]))
        self.tables={c:np.sort(np.nan_to_num(f[c].to_numpy(float)))for c in self.inputs}
        self.rawscale={c:max(float(np.nanquantile(f[c].to_numpy(float),.99)),.5)for c in self.inputs}
        # Parent fits only one estimator in this derived numeric view.
        derived=self.derive(f)
        self.derived_columns=tuple(derived.columns)
        result=super().fit(derived)
        return result
    def derive(self,f):
        import pandas as pd
        out=pd.DataFrame(index=f.index);e=[];names=[]
        for c in self.inputs:
            x=np.nan_to_num(f[c].to_numpy(float));ref=self.tables[c];n=len(ref)
            # Strict upper tail: a mass of normal zero values stays non-anomalous.
            p=(n-np.searchsorted(ref,x,side='left')+1)/(n+1)
            r=-np.log(p)
            # Retain extrapolation outside the finite empirical tail.
            excess=np.arcsinh(np.maximum(x-float(ref[-1]),0)/self.rawscale[c])
            v=r+excess
            if c.endswith('assessed')or c=='cadence_ready':v=x*.1
            name=c if c in ADAPTER else 'rank_'+c
            out[name]=v
            if c not in ('seq_last_age','seq_recovery_duration','seq_recovery_arrival','state_delay_exposure','adapter_reference_log_duration')and not c.endswith('assessed')and c!='cadence_ready':
                e.append(v);names.append(c)
        matrix=np.column_stack(e)
        for label,mask in (
            ('traffic',np.array([('arrival'in c or 'burst'in c or 'gap'in c or c=='empty_expected_rate')for c in names])),
            ('latency',np.array([not('arrival'in c or 'burst'in c or 'gap'in c or c=='empty_expected_rate')for c in names]))):
            v=np.sort(matrix[:,mask],axis=1)
            out['evidence_'+label+'_max']=v[:,-1]
            out['evidence_'+label+'_second']=v[:,-2]
        if self.setting.view=='summary':
            out=out[[c for c in out if c in ADAPTER or c.startswith('evidence_')]]
        elif self.setting.view=='state':
            out=out[[c for c in out if c in ADAPTER or c.startswith('evidence_')or c.startswith('rank_state_')or c in ('rank_seq_arrival_lift','rank_seq_gap_lift','rank_current_source_upper','rank_current_source_lower')]]
        return out
    def encode(self,f):
        if not all(c in f for c in self.columns):f=self.derive(f)
        x=super().encode(f)
        for j,c in enumerate(self.columns):
            if c.startswith('evidence_'):x[:,j]*=self.setting.evidence_weight
        return x
