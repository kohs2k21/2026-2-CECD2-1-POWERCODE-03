"""Independent evidence calibration; one shared normal-fitted estimator.

Cold predictions retain the preceding confidence calibration exactly. Warm
delay evidence is not discarded merely because traffic has a larger raw value.
"""
import copy

import numpy as np

from .adaptive_models import ConcordantNativeDetector


class ParallelEvidenceDetector:
    threshold=1.

    def __init__(self,parent,normal,traffic_kind='native',delay_gain=.5):
        self.base=parent.base;self.settings=parent.settings;self.fit_rows=parent.fit_rows
        self.model_weight=parent.model_weight
        self.family_weights=np.array(getattr(parent,'family_weights',np.full(8,parent.model_weight)),copy=True)
        self.model_stats=copy.deepcopy(parent.model_stats);self.default_stats=parent.default_stats
        self.cutoffs=parent.cutoffs.copy();self.traffic_kind=traffic_kind;self.delay_gain=delay_gain
        self.scales=parent.scales.copy()
        scales=np.maximum(np.quantile(self.peaks(normal),.95,axis=0),.25)
        self.scales[1:]=scales[1:]
        self.columns=tuple(dict.fromkeys([*parent.columns,'reference_level','native_latency_lift',
                                         'predictive_burst_1m','predictive_burst_3m']))
        if traffic_kind=='ewma':self.columns=(*self.columns,'ewma_rate_lift_1m','ewma_rate_lift_3m')

    def peaks(self,f):
        p=ConcordantNativeDetector.peaks(f)
        p[:,1]*=1+self.delay_gain*np.maximum(f.native_latency_lift.to_numpy(float),0.)
        if self.traffic_kind=='geometric':p[:,3]=np.sqrt(p[:,3]*np.maximum(f.predictive_burst_1m.to_numpy(float),0.))
        elif self.traffic_kind=='hybrid':
            power=self.traffic_nb_power
            p[:,3]=p[:,3]**(1-power)*np.maximum(f.predictive_burst_1m.to_numpy(float),0.)**power
        elif self.traffic_kind=='NB':p[:,3]=np.maximum(f.predictive_burst_1m,f.predictive_burst_3m)
        elif self.traffic_kind=='ewma':p[:,3]=np.maximum(f.ewma_rate_lift_1m,f.ewma_rate_lift_3m)
        elif self.traffic_kind!='native':raise ValueError(self.traffic_kind)
        return p

    def parts(self,f,raw=None):
        if raw is None:raw=self.base.raw_score(f)
        p=self.peaks(f);stat=np.log1p(p/self.scales)
        warm=np.zeros((len(f),4))
        for k in range(4):
            center,scale=self.model_stats.get(k,self.default_stats)
            learned=np.log1p(np.maximum((raw-center)/scale,0.))
            w=self.family_weights[k]
            warm[:,k]=np.where(p[:,k]>0,w*learned+(1-w)*stat[:,k],0.)
        cold=f.history_support.to_numpy(float)<np.log1p(3)
        level=f.reference_level.to_numpy(int)
        mode=4+np.select([level==0,level==1,level<4],[0,1,2],default=3)
        point=np.log1p(np.maximum(f.row_upper_tail.to_numpy(float),0.)/self.scales[0])
        cold_score=np.zeros(len(f))
        for k in range(4,8):
            ids=mode==k;center,scale=self.model_stats.get(k,self.default_stats)
            learned=np.log1p(np.maximum((raw[ids]-center)/scale,0.));w=self.family_weights[k]
            cold_score[ids]=w*learned+(1-w)*point[ids]
        return cold,mode,warm,cold_score

    def raw_score(self,f):
        cold,mode,warm,point=self.parts(f)
        score=(warm/np.maximum(self.cutoffs[:4],1e-8)).max(axis=1)
        score[cold]=point[cold]/np.maximum(self.cutoffs[mode[cold]],1e-8)
        return score

    def state(self,f):
        cold,mode,warm,point=self.parts(f)
        normalized=warm/np.maximum(self.cutoffs[:4],1e-8)
        group=normalized.argmax(axis=1);score=normalized.max(axis=1)
        group[cold]=mode[cold];score[cold]=point[cold]/np.maximum(self.cutoffs[mode[cold]],1e-8)
        return group,score

    def predict(self,f):
        score=self.raw_score(f);return score,score>1.

    def record(self):
        return dict(normal_statistical_fusion=True,bare_estimator=False,one_fault_estimator=True,
            independent_warm_evidence_channels=True,cold_confidence_predictions_preserved=True,
            model_weight=self.model_weight,family_weights=self.family_weights.tolist(),
            cutoffs=self.cutoffs.tolist(),traffic_kind=self.traffic_kind,
            traffic_nb_power=getattr(self,'traffic_nb_power',None),delay_gain=self.delay_gain,
            no_task_routing=True)
