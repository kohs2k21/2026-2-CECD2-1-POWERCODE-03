"""One learned anomaly estimator + explicit normal-statistical score fusion.

This is NOT a bare IF/K-means/LOF score. Conditional duration-neighbourhood
and causal count-state evidence are combined at the calibration layer. There
is no second learned fault estimator, Adapter expert, truth-based routing,
future endpoint, or point adjustment. References are normal only; development
labels may select the displayed weights/normal-quantile hyperparameters.
"""
from dataclasses import dataclass
import numpy as np
from shared_model_target.calibration import _surprise,CalibratedDetector
from shared_model_v6.features import Evidence,RAW
from shared_model_v7.count_filter import Context as CountContext,scores as count_scores
from shared_model_v7.remap_models import cached,_NEAR_CACHE

_EMISSIONS={}
_STATES={}


class Encoder:
    def __init__(self,core,reference,history,filter_setting):
        self.core=core;self.reference=reference;self.count=CountContext(history);self.filter_setting=filter_setting
        self.signature=tuple(sorted(self.count.history.items()))
    def auxiliary(self,f):
        near=cached(_NEAR_CACHE,(id(self.reference),id(f)),f,lambda:self.reference.transform(f)[:,0])
        point=np.minimum(near,np.maximum(f.current_source_upper.to_numpy(float),0.)*.5)
        raw=cached(_EMISSIONS,(self.signature,id(f)),f,lambda:self.count.emissions(f))
        key=(self.signature,tuple(sorted(self.filter_setting.items())),id(f))
        state=cached(_STATES,key,f,lambda:count_scores(raw,**self.filter_setting))
        return point,state
    def fit(self,normal):
        point,state=self.auxiliary(normal)
        self.point_scale=max(float(np.quantile(point,.95)),.15)
        self.state_scale=max(float(np.quantile(state,.95)),.05)
        return self
    def transform(self,f,view):
        if view!='peaks':raise ValueError('Fusion grouping uses five observed evidence peaks')
        peaks=self.core.transform(f,'peaks').copy();point,state=self.auxiliary(f)
        peaks[:,0]=np.maximum(peaks[:,0],point/self.point_scale)
        peaks[:,3]=np.maximum(peaks[:,3],state/self.state_scale)
        return peaks


@dataclass
class Reference:
    encoder:object
    model_weight_point:float
    model_weight_burst:float
    global_scores:np.ndarray
    tables:dict
    point_scores:np.ndarray
    state_scores:np.ndarray
    alpha:float=1.
    mode:str='observed_evidence_normal_statistical_score_fusion'
    kind:str='CDF_fusion_not_fault_probability'
    preserve_other_model_scores:bool=False
    model_scaling:str='cdf'
    @classmethod
    def fit(cls,normal,raw,encoder,point_weight,burst_weight,preserve_other=False,model_scaling='cdf'):
        point,state=encoder.auxiliary(normal);family=np.argmax(encoder.transform(normal,'peaks'),axis=1);tables={}
        for g in range(5):
            mask=family==g
            if mask.sum()>=40 and normal.loc[mask,'case_id'].nunique()>=5:tables[g]=np.sort(raw[mask])
        return cls(encoder,point_weight,burst_weight,np.sort(raw),tables,np.sort(point),np.sort(state),
            preserve_other_model_scores=preserve_other,model_scaling=model_scaling)
    def model_score(self,score,ref):
        if self.model_scaling=='cdf':return _surprise(score,ref)
        if self.model_scaling!='linear':raise ValueError('Unknown model-score transport')
        center=float(np.median(ref));width=max(float(np.quantile(ref,.95)-center),1e-5)
        return 3*np.log1p(np.maximum((score-center)/width,0.))/np.log(2.)
    def apply(self,f,scores):
        point,state=self.encoder.auxiliary(f);family=np.argmax(self.encoder.transform(f,'peaks'),axis=1)
        out=self.model_score(scores,self.global_scores)
        for g,ref in self.tables.items():
            mask=family==g;out[mask]=self.model_score(scores[mask],ref)
        p=family==0;b=family==3
        out[p]=self.model_weight_point*out[p]+(1-self.model_weight_point)*_surprise(point[p],self.point_scores)
        out[b]=self.model_weight_burst*out[b]+(1-self.model_weight_burst)*_surprise(state[b],self.state_scores)
        if self.preserve_other_model_scores:out[~(p|b)]=scores[~(p|b)]
        return out


class Detector(CalibratedDetector):
    def __init__(self,base,reference):
        super().__init__(base,reference,1.)
        self.columns=tuple(dict.fromkeys([*base.columns,*RAW,'case_id','window_start','adapterType','processId',
            'log_max_duration_1m','log_mean_count_5m','native_execution_arrivals','latency_assessed']))
    def score(self,frame):
        # The backend Monitor supplies the causal Process feature grid, not
        # necessarily precomputed v6 RANK columns. Derive once, before BOTH
        # the shared estimator and auxiliary references access those columns.
        frame=self.reference.parent.encoder.core.derive(frame)
        return self.reference.apply(frame,self.base.score(frame))
    def record(self):
        fusion=self.reference.parent
        switched=hasattr(self.reference,'original_score')
        continuous=fusion.__class__.__module__=='shared_model_v7.continuous_fusion'
        return {**super().record(),'columns':list(self.columns),'normal_statistical_evidence_fusion':True,
            'not_bare_estimator_score':True,'additional_learned_fault_estimators':0,
            'model_weight_point':fusion.model_weight_point,'model_weight_burst':fusion.model_weight_burst,
            'filter_parameters':fusion.encoder.filter_setting,'filter_is_calibrated_fault_probability':False,
            'preserve_other_model_scores':fusion.preserve_other_model_scores,'model_score_scaling':fusion.model_scaling,
            'auxiliary_score_scaling':'continuous_normal_median_P95_log_tail'if continuous else'normal_ECDF_upper_tail',
            'auxiliary_fusion_class':fusion.__class__.__name__,
            'cutoff_grouping_class':self.reference.codec.__class__.__name__ if hasattr(self.reference,'codec')else None,
            'same_estimator_original_score_option':switched,
            'same_estimator_original_score_groups':int(self.reference.original_score.sum())if switched else 0,
            'observed_gap_original_core_score_retained':getattr(self.reference,'gap_core_retained',False),
            'observed_volume_margin':getattr(self.reference,'margin',0.),
            'unknown_group_fallback':'frozen original estimator score and threshold'if switched else'fixed normal-family/history P99.5 cutoff',
            'score_reference_grouping_uses_truth':False,'point_adjustment':False}
