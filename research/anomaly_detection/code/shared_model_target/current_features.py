"""Current-state evidence with causal history; do not carry obsolete fault levels."""
from dataclasses import dataclass
import numpy as np
import pandas as pd
from adapter_feature_ablation.features import AdapterReferences,ADAPTER
from shared_model_target.features import augment as cadence

CURRENT=("current_arrival_excess","current_prefix_burst","gap_expected_prefix","gap_expected_recent",
         "empty_expected_rate","current_source_delay","current_source_upper","current_source_lower",
         "current_prefix_delay","current_recent_delay","current_delay_trend","current_population_delay",
         "current_duration_spread","cadence_ready","traffic_assessed","latency_assessed","temporal_assessed")


def augment(original):
    f=cadence(original)
    f["current_arrival_excess"]=f.arrival_dev_1m.clip(lower=0)
    counts=f.native_execution_arrivals.to_numpy(float)
    residual=f.residual_mean_1m.to_numpy(float)
    observed=f.latency_assessed.to_numpy(bool)&np.isfinite(residual)
    out=np.zeros((len(f),4))
    for ids in f.groupby("case_id",sort=False).indices.values():
        prefix_counts=[];prefix_residual=[];past=[]
        for age,i in enumerate(ids):
            if len(prefix_counts)>=3:
                rate=float(np.mean(prefix_counts))
                out[i,0]=max(float(np.log((counts[i]+1)/(rate+1))),0.)
            if observed[i]:
                value=residual[i]
                if len(prefix_residual)>=3:
                    center=float(np.median(prefix_residual));scale=max(1.4826*float(np.median(np.abs(np.asarray(prefix_residual)-center))),.25)
                    out[i,1]=max((value-center)/scale,0.)
                    recent=[(t,v)for t,v in past if age-t<=15]
                    if len(recent)>=3:
                        values=np.array([v for _,v in recent]);center2=float(np.median(values))
                        scale2=max(1.4826*float(np.median(np.abs(values-center2))),.25)
                        out[i,2]=max((value-center2)/scale2,0.)
                        points=recent+[(age,value)];times=np.array([t for t,_ in points],float);v=np.array([v for _,v in points])
                        x=times-times.mean();slope=float(np.dot(x,v-v.mean())/max(np.dot(x,x),1.))
                        active=out[i,1]/(1+out[i,1])
                        out[i,3]=max(slope*10/scale2,0.)*active
                if len(prefix_residual)<8:prefix_residual.append(value)
                past.append((age,value))
            if len(prefix_counts)<8:prefix_counts.append(counts[i])
    for j,c in enumerate(("current_prefix_burst","current_prefix_delay","current_recent_delay","current_delay_trend")):
        f[c]=np.minimum(out[:,j],100)
    f["current_source_delay"]=np.arcsinh(f.residual_mean_1m.clip(lower=0)).fillna(0)
    f["current_source_upper"]=f.duration_upper_max_1m.where(f.latency_assessed,0)
    f["current_source_lower"]=f.duration_lower_max_1m.where(f.latency_assessed,0)
    f["current_population_delay"]=f.current_source_delay*np.sqrt(np.log1p(f.completion_count_1m))
    f["current_duration_spread"]=(f.log_max_duration_1m-np.log1p(f.duration_p50_ms)).clip(lower=0).fillna(0)
    return f


@dataclass
class CurrentAdapterReference:
    reference: object

    @staticmethod
    def view(f):
        f=f.copy()
        # Adapter P95 reference is now one-minute completed duration, not a stale
        # five-minute percentile. Workload context remains the past 5m mean count.
        f["log_p95_duration_5m"]=np.log1p(f.duration_p95_ms)
        return f

    @classmethod
    def fit(cls,train):return cls(AdapterReferences.fit(cls.view(train)))

    def transform(self,original):
        f=augment(original)
        current=self.reference.transform(self.view(f))
        for column in ADAPTER:f[column]=current[column]
        for column in ("adapter_reference_level","adapter_reference_support"):f[column]=current[column]
        # Continuous feature interaction, not a rule/label: historical latency
        # accumulation is relevant only while current self-relative delay remains.
        active=f.current_prefix_delay/(1+f.current_prefix_delay)
        for column in ("adapter_delay_short_long","adapter_delay_ewma"):
            f[column]=f[column]*active
        return f
