"""Normal-fitted execution estimator plus explicit adaptive statistical evidence."""
import numpy as np

from .calibration import CalibratedEventDetector


class AdaptiveDetector(CalibratedEventDetector):
    def __init__(self,base,model_weight=.25):
        super().__init__(base,model_weight)
        self.columns=tuple(dict.fromkeys([*self.columns,'adaptive_upper','adaptive_cusum',
            'adaptive_mean_shift','adaptive_support','predictive_burst_1m','predictive_burst_3m']))

    @staticmethod
    def peaks(f):
        # Agreement of two actual historical references softens a fragile
        # first-five-execution anchor without hiding individual raw executions.
        upper=np.maximum(f.adaptive_upper.to_numpy(float),0.)
        mean=np.maximum(f.adaptive_mean_shift.to_numpy(float),0.)
        sustained=np.maximum(f.adaptive_cusum.to_numpy(float),0.)
        return np.column_stack((np.maximum(f.row_upper_tail.to_numpy(float),0.),
            np.log1p(upper),np.log1p(np.sqrt(sustained*mean)),
            np.maximum(f.predictive_burst_1m.to_numpy(float),f.predictive_burst_3m.to_numpy(float))))

    def state(self,f):
        peaks=self.peaks(f)/self.scales
        family=np.argmax(peaks,axis=1)
        cold=f.history_support.to_numpy(float)<np.log1p(3)
        return family+4*cold.astype(int),np.log1p(np.max(peaks,axis=1))

    def record(self):
        return {**super().record(),'adaptive_actual_prefix_seconds':480,
                'baseline_prefix_is_guaranteed_normal':False,
                'raw_Process_rows_preserved':True}


class NativeDetector(CalibratedEventDetector):
    def __init__(self,base,model_weight=.25):
        super().__init__(base,model_weight)
        self.columns=tuple(dict.fromkeys([*self.columns,'native_latency_run','native_latency_cusum',
            'native_traffic_llr','native_traffic_current','native_support']))

    @staticmethod
    def peaks(f):
        traffic=np.maximum(f.native_traffic_llr.to_numpy(float),0.)
        current=np.clip(f.native_traffic_current.to_numpy(float),0,1)
        return np.column_stack((np.maximum(f.row_upper_tail.to_numpy(float),0.),
            f.native_latency_run.to_numpy(float),f.native_latency_cusum.to_numpy(float),
            np.log1p(traffic)*(.5+current)/1.5))

    def record(self):
        return {**super().record(),'event_driven_observed_minute_summary_features':True,
                'no_summary_training_rows':True,'no_empty_minute_reference_rows':True}


class ConcordantNativeDetector(NativeDetector):
    @staticmethod
    def peaks(f):
        peaks=NativeDetector.peaks(f)
        warm=(f.history_support.to_numpy(float)>=np.log1p(3)) & (f.native_support.to_numpy(float)>=3)
        agreement=np.clip(f.native_latency_run.to_numpy(float)+2*f.native_latency_cusum.to_numpy(float),0,1)
        # A broad/static fallback can look extreme for a newly observed group
        # even when its own repeated actual durations have not changed at all.
        # Soft local agreement limits that evidence after context is available.
        peaks[warm,0]*=agreement[warm]
        return peaks

    def record(self):
        return {**super().record(),'warm_static_duration_evidence_requires_soft_local_agreement':True}


class CurrentBurstDetector(ConcordantNativeDetector):
    @staticmethod
    def peaks(f):
        peaks=ConcordantNativeDetector.peaks(f)
        memory=np.log1p(np.maximum(f.native_traffic_llr.to_numpy(float),0.))
        current=np.log1p(np.maximum(f.native_traffic_current.to_numpy(float),0.))
        peaks[:,3]=np.sqrt(memory*current)
        return peaks

    def record(self):
        return {**super().record(),'burst_evidence_agrees_with_current_count_excess':True}


class AdapterScaledNativeDetector(ConcordantNativeDetector):
    def fit_calibration(self,normal):
        p=self.peaks(normal)
        self.adapter_scales={}
        for adapter,ids in normal.groupby('adapterType',sort=False,observed=True).indices.items():
            if len(ids)>=30:
                self.adapter_scales[str(adapter)]=np.maximum(np.quantile(p[ids],.95,axis=0),.25)
        return super().fit_calibration(normal)

    def state(self,f):
        scales=np.tile(self.scales,(len(f),1))
        for adapter,ids in f.groupby('adapterType',sort=False,observed=True).indices.items():
            if str(adapter) in self.adapter_scales:scales[ids]=self.adapter_scales[str(adapter)]
        peaks=self.peaks(f)/scales
        cold=f.history_support.to_numpy(float)<np.log1p(3)
        return peaks.argmax(axis=1)+4*cold.astype(int),np.log1p(peaks.max(axis=1))

    def record(self):
        return {**super().record(),'adapter_normal_scale_tables':len(self.adapter_scales),
                'adapter_expert_estimators':False,'unseen_adapter_uses_global_normal_scale':True}


class ConfidenceNativeDetector(NativeDetector):
    def state(self,f):
        group,stat=super().state(f)
        cold=f.history_support.to_numpy(float)<np.log1p(3)
        level=f.reference_level.to_numpy(int)
        confidence=np.select([level==0,level==1,level<4],[0,1,2],default=3)
        group[cold]=4+confidence[cold]
        return group,stat

    def record(self):
        return {**super().record(),'cold_threshold_strata':'exact process/count, adapter/count, adapter fallback, pooled fallback',
                'warm_threshold_strata':'point, persistent delay, trend, traffic',
                'no_Adapter_expert_estimator':True}


class TrafficRankDetector(ConfidenceNativeDetector):
    @staticmethod
    def peaks(f):
        peaks=ConcordantNativeDetector.peaks(f)
        rank=np.maximum(f.native_traffic_rank.to_numpy(float),0.)
        magnitude=np.log1p(np.maximum(f.native_traffic_current.to_numpy(float),0.))
        peaks[:,3]=rank*magnitude
        return peaks

    def record(self):
        return {**super().record(),'traffic_rank_and_current_magnitude_agreement':True,
                'known_synthetic_traffic_prior_used':False}


class MicroDetector(ConfidenceNativeDetector):
    @staticmethod
    def peaks(f):
        peaks=ConcordantNativeDetector.peaks(f)
        memory=np.log1p(np.maximum(f.micro_cusum.to_numpy(float),0.))
        density=np.log1p(np.maximum(f.micro_density.to_numpy(float),0.))
        peaks[:,3]=memory*density
        return peaks

    def record(self):
        return {**super().record(),'actual_arrival_gap_change_features':True,
                'micro_feature_columns':['micro_cusum','micro_density','micro_run','micro_support'],
                'no_known_synthetic_traffic_prior':True}
