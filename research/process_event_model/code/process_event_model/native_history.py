"""Event-driven minute-summary *features*, still one original execution per row.

Only actually observed completed-minute summaries enter the local reference.
Absent minute rows, normal logs and external count distributions are NOT made up.
Updates use current/past deliveries only, never final future minute counts.
"""
import numpy as np
from scipy.special import gammaln,logsumexp

from .predictive import PredictiveRowContext

NATIVE_FEATURES=('native_latency_run','native_latency_cusum','native_latency_lift',
                 'native_traffic_llr','native_traffic_current','native_support')
POLICY_FEATURES=('native_quiet_run','native_previous_excess','native_traffic_rank','native_previous_traffic_rank')
MICRO_FEATURES=('micro_cusum','micro_density','micro_run','micro_support')
COUNT_FEATURES=('native_observed_minute_count',)


def latency(s,value):
    prefix=s['latency_prefix'];n=len(prefix)
    if n<3:return 0.,0.,0.,0
    ref=np.asarray(prefix)
    up=s['up']+1 if value>max(prefix)+1e-6 else 0
    evidence=float(gammaln(n+up+1)-gammaln(n+1)-gammaln(up+1))
    q=float(np.mean(ref<value)+.5*np.mean(ref==value))
    cusum=max(.9*s['cusum']+q-.6,0.) if q>.5 else 0.
    return evidence,cusum,max(value-float(np.median(ref)),0.),up


def traffic(s,count):
    prefix=s['count_prefix']
    if len(prefix)<3:return np.zeros(3),0.
    theta=np.array([.2,.5,1.])
    reference=tuple(prefix)
    if s.get('traffic_reference_key')!=reference:
        values=np.asarray(prefix,float)
        empirical=logsumexp(theta[:,None]*values[None,:],axis=1)-np.log(len(values))
        poisson=values.mean()*np.expm1(theta)
        s['traffic_reference_key']=reference
        s['traffic_mgf']=np.maximum(empirical,poisson)
        s['traffic_mean']=float(values.mean())
    mgf=s['traffic_mgf']
    llr=np.maximum(.9*s['traffic']+theta*count-mgf,0.)
    current=max(float(count-s['traffic_mean']),0.)/(s['traffic_mean']+.5)
    return llr,current


def traffic_rank(s,count):
    prefix=s['count_prefix'];n=len(prefix)
    if n<3:return 0.,0
    up=s['traffic_up']+1 if count>max(prefix) else 0
    evidence=float(gammaln(n+up+1)-gammaln(n+1)-gammaln(up+1))
    return evidence,up


class NativeHistory:
    def __init__(self,include_empty_reference=False):
        self.states={}
        self.include_empty_reference=include_empty_reference

    def transform(self,frame):
        f=frame.copy()
        time=f.availableTime.astype('datetime64[ns, UTC]').astype('int64').to_numpy()/1e9
        value=np.log1p(f.duration_ms.to_numpy(float))
        columns=(['cohort'] if 'cohort' in f else [])+['case_id','adapterType','processId']
        out=np.zeros((len(f),len(NATIVE_FEATURES)+len(POLICY_FEATURES)+len(MICRO_FEATURES)+len(COUNT_FEATURES)))
        for key,indices in f.groupby(columns,observed=True,sort=False).indices.items():
            ids=np.asarray(indices);ids=ids[np.argsort(time[ids],kind='stable')]
            s=self.states.setdefault(key,dict(last=-np.inf,minute=None,count=0,maximum=-np.inf,
                latency_prefix=[],count_prefix=[],up=0,cusum=0.,traffic=np.zeros(3),quiet=0,previous_excess=0.,
                traffic_up=0,previous_rank=0.,first_time=time[ids[0]],prefix_events=0.,founders=0.,
                micro=np.zeros(3),micro_run=0))
            starts=np.r_[0,np.flatnonzero(np.diff(time[ids])!=0)+1]
            stops=np.r_[starts[1:],len(ids)]
            for lo,hi in zip(starts,stops):
                batch=ids[lo:hi];t=time[batch[0]];minute=int(t//60)
                if t<=s['last']:raise ValueError('Out-of-order or split availability batch')
                if s['minute'] is not None and minute!=s['minute']:
                    _,cu,_,up=latency(s,s['maximum'])
                    s['cusum'],s['up']=cu,up
                    tr,complete_excess=traffic(s,s['count']);s['traffic']=tr
                    previous_rank,traffic_up=traffic_rank(s,s['count'])
                    s['previous_rank'],s['traffic_up']=previous_rank,traffic_up
                    gap=minute-s['minute']
                    if len(s['count_prefix'])>=3:
                        s['quiet']=s['quiet']+1 if complete_excess<=0 else 0
                        if gap>1:s['quiet']+=gap-1
                    s['previous_excess']=complete_excess if gap==1 else 0.
                    if gap>1:s['traffic_up']=0;s['previous_rank']=0.
                    if len(s['latency_prefix'])<6:s['latency_prefix'].append(s['maximum'])
                    if self.include_empty_reference and gap>20:
                        # Unknown long coverage gaps cannot supply a confident
                        # zero baseline. Relearn from subsequent actual input.
                        s['count_prefix']=[];s['traffic']=np.zeros(3)
                        s['quiet']=0
                    elif len(s['count_prefix'])<8:
                        s['count_prefix'].append(s['count'])
                        if self.include_empty_reference:
                            s['count_prefix'].extend([0]*min(gap-1,8-len(s['count_prefix'])))
                    if gap>1:
                        s['traffic']*=.9**(gap-1)
                        s['cusum']*=.9**(gap-1)
                    if gap>8:s['up']=0
                    s['count']=0;s['maximum']=-np.inf
                s['minute']=minute
                s['count']+=len(batch)
                s['maximum']=max(s['maximum'],float(value[batch].max()))
                rank,cu,lift,_=latency(s,s['maximum'])
                tr,current=traffic(s,s['count'])
                traffic_evidence,_=traffic_rank(s,s['count'])
                age=t-s['first_time'];past_events=max(s['prefix_events']-s['founders'],0.)
                rate=(past_events+.5)/max(min(age,360.),60.)
                elapsed=t-s['last'] if np.isfinite(s['last']) else 0.
                density=0.
                if age>=120 and past_events>=3:
                    theta=np.array([1.5,2.,3.])
                    increment=len(batch)*np.log(theta)-(theta-1)*rate*elapsed
                    s['micro']=np.maximum(np.exp(-elapsed/600)*s['micro']+increment,0.)
                    density=max(float(np.log(.5/max(rate*elapsed,1e-6))),0.)
                    s['micro_run']=s['micro_run']+len(batch) if density>0 else 0
                else:s['micro']=np.zeros(3);s['micro_run']=0
                if age<=360:s['prefix_events']+=len(batch)
                if not np.isfinite(s['last']):s['founders']=len(batch)
                out[batch]=(rank,cu,lift,float(tr.max()),current,len(s['latency_prefix']),
                            s['quiet'],s['previous_excess'],traffic_evidence,s['previous_rank'],
                            float(s['micro'].max()),density,s['micro_run'],np.log1p(past_events),s['count'])
                s['last']=t
        for j,c in enumerate((*NATIVE_FEATURES,*POLICY_FEATURES,*MICRO_FEATURES,*COUNT_FEATURES)):f[c]=np.clip(out[:,j],0,100)
        return f


class NativeRowContext(PredictiveRowContext):
    def __init__(self,reference):
        super().__init__(reference);self.native=NativeHistory()

    def transform(self,records):return self.native.transform(super().transform(records))


class ClockNativeRowContext(NativeRowContext):
    def __init__(self,reference):
        super().__init__(reference);self.native=NativeHistory(include_empty_reference=True)
