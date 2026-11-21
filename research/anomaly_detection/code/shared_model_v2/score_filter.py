"""Causal, recovery-safe soft suppression of isolated model-score spikes."""
import numpy as np
import pandas as pd


class FilteredDetector:
    def __init__(self,base,beta=.5,window=3,ttl=8,by_availability=True,traffic_protection=0.):
        self.base,self.beta,self.window,self.ttl,self.by_availability=base,beta,window,ttl,by_availability
        self.traffic_protection=traffic_protection
        for k in('algorithm','setting','encoder','columns','estimator','fit_rows','fit_seconds','weights'):
            if hasattr(base,k):setattr(self,k,getattr(base,k))
    def filter(self,frame,raw):
        # Work in log-score units so one enormous LOF value does not dominate.
        values=np.log1p(np.maximum(raw,0));out=values.copy()
        times=pd.to_datetime(frame.window_start,format='ISO8601')if'window_start'in frame else None
        if times is None:return values # Independent ADBench points have no history.
        protect=getattr(self,'traffic_protection',0.)
        if protect:
            strength=np.maximum.reduce([np.maximum(frame[c].to_numpy(float),0)for c in
                ('current_arrival_excess','current_prefix_burst','gap_expected_prefix')])
            beta=self.beta/(1+protect*strength)
        else:beta=np.repeat(self.beta,len(frame))
        gates=frame.traffic_assessed.to_numpy(int)+2*frame.latency_assessed.to_numpy(int)
        for ids in frame.groupby('case_id',sort=False).indices.values():
            histories={};last_time=None
            for i in ids:
                t=times.iloc[i]
                # Paired observed/control replay cohorts reuse IDs. A time reset
                # starts a new causal episode, never shares state with its pair.
                if last_time is not None and t<=last_time:histories={}
                key=int(gates[i])if self.by_availability else 0
                history=[(old,v)for old,v in histories.get(key,[])if (t-old).total_seconds()<=self.ttl*60]
                history=history[-(self.window-1):]
                if len(history)>=1:
                    median=float(np.median([v for _,v in history]+[values[i]]))
                    # Never increase a score after recovery; only soften a
                    # current spike that lacks support in earlier model scores.
                    out[i]=(1-beta[i])*values[i]+beta[i]*min(values[i],median)
                histories[key]=history+[(t,values[i])];last_time=t
        return out
    def score(self,frame):return self.filter(frame,self.base.score(frame))
    def record(self):
        return {**self.base.record(),'causal_score_filter':{'beta':self.beta,'window':self.window,'ttl_minutes':self.ttl,
           'traffic_protection':getattr(self,'traffic_protection',0.),
           'by_availability':self.by_availability,'units':'log1p(raw model score)','future_used':False,
           'point_adjustment':False,'score_never_exceeds_current_unfiltered_log_score':True}}
