"""Strictly isolated input batches with asymmetric normal-reference statistics.

The only persistent state is the reviewed NORMAL reference. No preceding/future
monitoring batch, score, label or scenario is an input. A batch-level score is
reported for every observed hour in the supplied batch; this is not localization.
"""
from dataclasses import dataclass
from pathlib import Path
import argparse
import joblib
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
from scipy.special import logsumexp
from ..models import fill_short_gaps
from ..intervals.model import HORIZONS,HOUR_NS,checked_hours,validated_frame
from ..benchmark import CHANNELS

STAT_NAMES=('mean','median','std')
FEATURE_NAMES=tuple(f'{stat}_{direction}:{channel}' for stat in STAT_NAMES
                    for channel in ('requests','duration') for direction in ('upper','lower','density'))


def summaries(windows):
    """No fake zeros for missing observations; all-missing outputs stay NaN."""
    values=np.asarray(windows,float)
    good=np.isfinite(values)
    counts=good.sum(axis=1)
    sums=np.where(good,values,0.).sum(axis=1)
    mean=np.divide(sums,counts,out=np.full_like(sums,np.nan),where=counts>0)
    squared=np.where(good,(values-mean[:,None,:])**2,0.).sum(axis=1)
    variance=np.divide(squared,counts,out=np.full_like(squared,np.nan),where=counts>0)
    median=np.full_like(mean,np.nan)
    for i in range(len(values)):
        for j in range(values.shape[2]):
            if counts[i,j]:median[i,j]=np.median(values[i,:,j][good[i,:,j]])
    return np.stack([mean,median,np.sqrt(variance)],axis=1)


@dataclass(frozen=True)
class Distribution:
    values: np.ndarray
    median: float
    upper_scale: float
    lower_scale: float
    density: object
    density_scale: float
    nll_center: float

    @classmethod
    def fit(cls,values,quantile,density):
        ref=np.sort(np.asarray(values,float))
        if len(ref)<48 or not np.isfinite(ref).all():raise ValueError('At least 48 valid normal windows required')
        lo,med,hi=np.quantile(ref,[1-quantile,.5,quantile])
        floor=max(float(np.std(ref))*.025,1e-6)
        if density=='knn':
            k=min(8,len(ref)-1)
            distances=abs(ref[:,None]-ref[None,:])
            normal_radius=np.partition(distances,k,axis=1)[:,k]
            den_scale=max(float(np.quantile(normal_radius,.95)),float(np.std(ref))*.01,1e-6)
            nll_center=0.
        else:
            bandwidth=float(density)*max(float(np.std(ref)),1e-6)
            z=-.5*((ref[:,None]-ref[None,:])/bandwidth)**2
            np.fill_diagonal(z,-np.inf)
            normal_nll=-logsumexp(z,axis=1)+np.log(len(ref)-1)
            nll_center,tail=np.quantile(normal_nll,[.5,.95])
            den_scale=max(float(tail-nll_center),.05)
        return cls(ref,float(med),max(float(hi-med),floor),max(float(med-lo),floor),
                   density,float(den_scale),float(nll_center))

    def transform_many(self,values):
        x=np.asarray(values,float).reshape(-1)
        if self.density=='knn':
            k=min(8,len(self.values)-1)
            radius=np.partition(abs(x[:,None]-self.values[None,:]),k-1,axis=1)[:,k-1]
            density_score=radius/self.density_scale
        else:
            bandwidth=float(self.density)*max(float(np.std(self.values)),1e-6)
            nll=-logsumexp(-.5*((x[:,None]-self.values[None,:])/bandwidth)**2,axis=1)+np.log(len(self.values))
            density_score=(nll-self.nll_center)/self.density_scale
        return np.column_stack([(x-self.median)/self.upper_scale,(self.median-x)/self.lower_scale,density_score])

    def transform(self,value):
        return self.transform_many([value])[0]


@dataclass(frozen=True)
class Reference:
    next_day_forecast: np.ndarray
    distributions: dict
    end_time: pd.Timestamp
    case_id: str
    max_forecast_hours: int=336


class StrictIntervalBank:
    def __init__(self,settings,context_hours=672):
        if set(settings)!=set(HORIZONS):raise ValueError('All five calibrated lengths are required')
        for h,s in settings.items():
            if len(s['weights'])!=len(FEATURE_NAMES) or not np.isfinite(s['threshold']):
                raise ValueError(f'Invalid calibrated settings: {h}')
            if any(not np.isfinite(w) or w<0 for w in s['weights']):raise ValueError('Weights must be finite/nonnegative')
            if not any(s['weights']):raise ValueError('All-zero weights are invalid')
        self.settings={h:dict(s) for h,s in settings.items()}
        self.context_hours=int(context_hours)
        if self.context_hours<96 or self.context_hours%24:raise ValueError('Whole normal days, at least 96 hours required')

    def prepare_reference(self,normal_history):
        x,observed,available,times,case=validated_frame(normal_history)
        if len(x)!=self.context_hours:raise ValueError(f'Exactly {self.context_hours} normal hours required')
        # This is training/reference interpolation ONLY. No monitoring samples
        # can reach the reviewed reference, seasonal profile or distributions.
        context=x.copy();context[~observed]=np.nan;context[~available,1:]=np.nan
        context=fill_short_gaps(context)
        phase=np.arange(len(context))%24
        forecast=np.asarray([np.median(context[phase==h],axis=0) for h in range(24)])
        residual=context[:,:2]-forecast[phase,:2]
        distributions={}
        for h in HORIZONS:
            mask=sliding_window_view(observed&available,h).all(axis=1)
            windows=sliding_window_view(residual,h,axis=0).transpose(0,2,1)
            normal=summaries(windows[mask])
            s=self.settings[h]
            distributions[h]=tuple(Distribution.fit(normal[:,stat,ch],s.get('quantile',.95),s['density'])
                                    for stat in range(3) for ch in range(2))
        forecast.setflags(write=False)
        return Reference(forecast,distributions,times[-1],case)

    def predict_batch(self,reference,batch,analysis_hours=4):
        h=checked_hours(analysis_hours)
        x,observed,available,times,case=validated_frame(batch)
        if len(x)!=h:raise ValueError(f'analysis_hours={h} requires exactly {h} monitoring rows')
        if case!=reference.case_id:raise ValueError('Reference and batch groups differ')
        if str(times.tz)!=str(reference.end_time.tz):raise ValueError('Reference and batch timezone differs')
        delta=times[0].value-reference.end_time.value-HOUR_NS
        if delta<0 or delta%HOUR_NS:raise ValueError('Batch must follow reference at a whole-hour offset')
        offset=delta//HOUR_NS
        if offset+h>reference.max_forecast_hours:raise ValueError('Normal reference outside tested 14-day forecast range')
        scores,reasons=self.score_arrays(reference,x,observed,available,h,int(offset))
        threshold=float(self.settings[h]['threshold'])
        return pd.DataFrame({'case_id':case,'timestamp':times,'analysis_hours':h,
            'batch_start':times[0],'batch_end_exclusive':times[-1]+pd.Timedelta(hours=1),
            'available_at':times[-1]+pd.Timedelta(hours=1),'score':scores,'threshold':threshold,
            'is_anomaly':np.isfinite(scores)&(scores>threshold),'assessable':np.isfinite(scores),
            'reason':reasons,'retrospective':True,
            'score_scope':'selected_batch','monitoring_state_carried':False})

    def score_arrays(self,reference,x,observed,available,analysis_hours,offset_hours=0):
        """Vectorized replay of ISOLATED batches, same implementation as API.

        Summaries operate on axis 1 of [batches,hours,channels]. Neither previous
        batch arrays nor any subsequent batch value enter a batch's summary.
        """
        h=checked_hours(analysis_hours)
        x=np.asarray(x,float);observed=np.asarray(observed,bool);available=np.asarray(available,bool)
        if x.ndim!=2 or x.shape[1]!=3 or len(x)==0 or len(x)%h:raise ValueError('Whole selected batches required')
        if observed.shape!=(len(x),) or available.shape!=(len(x),):raise ValueError('Observation mask shape differs')
        if (available&~observed).any():raise ValueError('Latency cannot be available when collection is missing')
        if not isinstance(offset_hours,(int,np.integer)) or isinstance(offset_hours,bool) or offset_hours<0:
            raise ValueError('Nonnegative integer offset required')
        if offset_hours+len(x)>reference.max_forecast_hours:raise ValueError('Normal reference outside tested forecast range')
        if not np.isfinite(x[observed,0]).all() or not np.isfinite(x[available,1:]).all():raise ValueError('Observed values must be finite')
        r=x[:,:2]-reference.next_day_forecast[(offset_hours+np.arange(len(x)))%24,:2]
        r[~observed]=np.nan;r[~available,1]=np.nan
        current=summaries(r.reshape(-1,h,2))
        f=np.column_stack([dist.transform_many(current[:,stat,ch]) for dist,(stat,ch) in
                          zip(reference.distributions[h],((i,j) for i in range(3) for j in range(2)))])
        no_latency=~available.reshape(-1,h).any(axis=1)
        for stat in range(3):f[np.ix_(no_latency,np.arange(stat*6+3,stat*6+6))]=0.
        assessable=observed.reshape(-1,h).any(axis=1)
        values=f*np.asarray(self.settings[h]['weights'])
        if not np.isfinite(values[assessable]).all():raise ValueError('Observed aggregate could not be assessed')
        safe=np.nan_to_num(values,nan=-np.inf)
        winners=np.argmax(safe,axis=1);scores=safe[np.arange(len(safe)),winners]
        scores=np.repeat(scores,h);reasons=np.repeat(np.asarray(FEATURE_NAMES)[winners],h)
        scores[~observed]=np.nan;reasons=np.where(observed,reasons,'collection_missing')
        return scores,reasons

    def record(self):
        return {'analysis_hours':list(HORIZONS),'normal_reference_hours':self.context_hours,
            'input_frequency_hours':1,'monitoring_state_carried':False,'outside_batch_monitoring_used':False,
            'normal_reference_auto_updated':False,'feature_names':list(FEATURE_NAMES),'settings':self.settings,
            'batch_scores_not_hourly_localization':True,'point_adjustment':False}


def score_frame(bank,frame,analysis_hours=4,split_batches=False):
    h=checked_hours(analysis_hours);outputs=[]
    if frame.empty or 'case_id' not in frame:raise ValueError('No input cases')
    for _,part in frame.groupby('case_id',sort=False,dropna=False):
        validated_frame(part)
        monitor=part.iloc[bank.context_hours:]
        if not len(monitor) or len(monitor)%h or (not split_batches and len(monitor)!=h):
            raise ValueError('Supply exactly one selected batch, or whole batches with split_batches=True')
        ref=bank.prepare_reference(part.iloc[:bank.context_hours])
        outputs.extend(bank.predict_batch(ref,monitor.iloc[i:i+h],h) for i in range(0,len(monitor),h))
    return pd.concat(outputs,ignore_index=True)


def load_bank(path):
    value=joblib.load(path)  # Only load trusted joblib/pickle files.
    if not isinstance(value,StrictIntervalBank):raise TypeError('Expected StrictIntervalBank artifact')
    return value


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model',type=Path,required=True);p.add_argument('--input',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--analysis-hours',type=int,choices=HORIZONS,required=True)
    p.add_argument('--split-batches',action='store_true');args=p.parse_args()
    if args.output.exists() or args.output.resolve()==args.input.resolve():raise FileExistsError('Never overwrite inputs/existing output')
    out=score_frame(load_bank(args.model),pd.read_csv(args.input),args.analysis_hours,args.split_batches)
    args.output.parent.mkdir(parents=True,exist_ok=True);out.to_csv(args.output,index=False,encoding='utf-8-sig')
    print(f'{len(out)} hourly decisions, isolated {args.analysis_hours}h batches',flush=True)
