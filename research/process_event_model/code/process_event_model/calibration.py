"""Explicit normal-statistical fusion, not a bare sklearn estimator score.

The preceding release also had statistical fusion. This version preserves
individual rows and uses only observed feature strata, never task labels to route.
One sklearn fault estimator remains per algorithm; development labels tune its
fusion weight and alert cutoffs, not a second supervised classifier.
"""
import numpy as np


class CalibratedEventDetector:
    threshold = 1.

    def __init__(self, base, model_weight=.5):
        self.base=base
        self.model_weight=model_weight
        self.settings=base.settings
        self.fit_rows=base.fit_rows
        self.columns=tuple(dict.fromkeys([*base.columns,'row_upper_tail','local_upper',
                          'local_cusum','traffic_lift_1m','traffic_lift_3m','history_support']))

    @staticmethod
    def peaks(f):
        return np.column_stack((np.maximum(f.row_upper_tail.to_numpy(float),0),
            np.log1p(np.maximum(f.local_upper.to_numpy(float),0)),
            np.log1p(np.maximum(f.local_cusum.to_numpy(float),0)),
            3*np.maximum(f.traffic_lift_1m.to_numpy(float),f.traffic_lift_3m.to_numpy(float))))

    def fit_calibration(self, normal):
        p=self.peaks(normal)
        self.scales=np.maximum(np.quantile(p,.95,axis=0),.25)
        raw=self.base.raw_score(normal)
        group,stat=self.state(normal)
        self.model_stats={}
        center=float(np.median(raw));scale=max(float(np.quantile(raw,.95)-center),.01)
        self.default_stats=(center,scale)
        for g in range(8):
            v=raw[group==g]
            if len(v)>=30:
                center=float(np.median(v));scale=max(float(np.quantile(v,.95)-center),.01)
                self.model_stats[g]=(center,scale)
        self.cutoffs=np.ones(8)
        return self

    def state(self,f):
        scaled=self.peaks(f)/self.scales
        family=np.argmax(scaled,axis=1)
        cold=f.history_support.to_numpy(float)<np.log1p(3)
        return family+4*cold.astype(int),np.log1p(np.max(scaled,axis=1))

    def components(self,f,raw=None):
        group,stat=self.state(f)
        if raw is None:raw=self.base.raw_score(f)
        learned=np.zeros(len(f))
        for g in range(8):
            mask=group==g
            center,scale=self.model_stats.get(g,self.default_stats)
            learned[mask]=np.log1p(np.maximum((raw[mask]-center)/scale,0.))
        return group,self.model_weight*learned+(1-self.model_weight)*stat

    def raw_score(self,f):
        group,score=self.components(f)
        return score/np.maximum(self.cutoffs[group],1e-8)

    def predict(self,f):
        score=self.raw_score(f)
        return score,score>self.threshold

    def record(self):
        return dict(normal_statistical_fusion=True,bare_estimator=False,
                    model_weight=self.model_weight,feature_strata=8,
                    cutoffs=self.cutoffs.tolist(),one_fault_estimator=True,
                    development_labels_used_for_selection=True,
                    strata='dominant point/delay/accumulation/traffic evidence x observed cold/warm context',
                    inference_task_or_label_routing=False)
