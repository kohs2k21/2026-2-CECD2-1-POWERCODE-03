"""Backend-facing bank: refined current-hour model, preserved other lengths."""
from dataclasses import dataclass
from pathlib import Path
import argparse
import joblib
import numpy as np
import pandas as pd
from scipy.special import logsumexp
from ..intervals.model import validated_frame,checked_hours,HORIZONS,HOUR_NS
from ..strict_intervals.model import score_frame
from .ratio import prepare,FEATURE_NAMES


def evidence_score(features,weights,available,temperature):
    f=np.asarray(features,float);w=np.broadcast_to(np.asarray(weights,float),f.shape).copy()
    w[~np.asarray(available,bool),3:]=0.
    with np.errstate(divide='ignore'):logw=np.log(w)
    if temperature<=0:
        score=np.max(f+logw,axis=1)-np.max(logw,axis=1)
    else:
        score=temperature*(logsumexp((f+logw)/temperature,axis=1)-logsumexp(logw/temperature,axis=1))
    score[~np.isfinite(f).any(axis=1)]=np.nan
    return score


@dataclass(frozen=True)
class PointReference:
    profile: np.ndarray
    center: np.ndarray
    scale: np.ndarray
    rho: np.ndarray
    slope: np.ndarray
    midpoint: float
    context_hours: int


@dataclass(frozen=True)
class CombinedReference:
    ordinary: object
    point: PointReference


class SharedARPoint:
    def __init__(self,grid,buckets,curves,config):
        self.grid=np.asarray(grid,float);self.buckets=np.asarray(buckets,float)
        self.curves=np.asarray(curves,float);self.config=dict(config)
        if self.curves.shape!=(6,len(self.buckets),len(self.grid)):raise ValueError('Six conditional evidence curves required')
        if not np.isfinite(self.curves).all() or not np.all(np.diff(self.grid)>0):raise ValueError('Invalid fitted density curves')
        if len(config['weights'])!=6 or any(not np.isfinite(w) or w<0 for w in config['weights']):raise ValueError('Invalid fitted evidence weights')
        if not any(config['weights'][:3]):raise ValueError('Request-only support required')
        if not np.isfinite(config['threshold']):raise ValueError('Calibrated threshold required')

    def prepare_reference(self,x,observed,available):
        profile,ref,slope,mid=prepare(x,observed,available,.5,.5)
        center=np.mean(ref[:,:2],axis=0);scale=np.maximum(np.std(ref[:,:2],axis=0),1e-4)
        z=(ref[:,:2]-center)/scale;good=observed&available;pair=good[:-1]&good[1:]
        rho=[]
        for ch in range(2):
            left,right=z[:-1,ch][pair],z[1:,ch][pair]
            value=(float(np.corrcoef(left,right)[0,1]) if np.std(left)>1e-12 and np.std(right)>1e-12 else 0.)
            rho.append(float(np.clip(value,0,.95)))
        return PointReference(profile,center,scale,np.asarray(rho),slope,mid,len(x))

    def score_arrays(self,reference,x,observed,available,offset_hours=0):
        x=np.asarray(x,float);observed=np.asarray(observed,bool);available=np.asarray(available,bool)
        if x.ndim!=2 or x.shape[1]!=3 or not len(x):raise ValueError('Nonempty [hour,3] array required')
        if observed.shape!=(len(x),) or available.shape!=(len(x),) or (available&~observed).any():raise ValueError('Invalid observation masks')
        if not isinstance(offset_hours,(int,np.integer)) or isinstance(offset_hours,bool) or offset_hours<0 or offset_hours+len(x)>336:
            raise ValueError('Nonnegative integer offset inside tested 14-day range required')
        if not np.isfinite(x[observed,0]).all() or not np.isfinite(x[available,1:]).all():raise ValueError('Observed channels must be finite')
        hours=offset_hours+np.arange(len(x));r=x[:,:2]-reference.profile[hours%24,:2]
        r-=(reference.context_hours+hours-reference.midpoint)[:,None]*reference.slope[:2]
        z=(r-reference.center)/reference.scale
        columns=[]
        for field in range(6):
            ch=field//3
            upper=min(len(self.buckets)-1,int(np.searchsorted(self.buckets,reference.rho[ch],side='right')))
            lower=max(0,upper-1)
            fraction=(reference.rho[ch]-self.buckets[lower])/max(self.buckets[upper]-self.buckets[lower],1e-10)
            curve=self.curves[field,lower]*(1-fraction)+self.curves[field,upper]*fraction
            columns.append(np.interp(z[:,ch],self.grid,curve,left=100,right=100))
        f=np.column_stack(columns);f[np.ix_(~available,[3,4,5])]=0.;f[~observed]=np.nan
        score=evidence_score(f,self.config['weights'],available,self.config.get('temperature',0.))
        with np.errstate(divide='ignore'):
            contribution=f+np.log(np.asarray(self.config['weights']))
        contribution[np.ix_(~available,[3,4,5])]=-np.inf
        winner=np.argmax(np.nan_to_num(contribution,nan=-np.inf),axis=1)
        reasons=np.asarray(FEATURE_NAMES)[winner];reasons=np.where(observed,reasons,'collection_missing')
        return score,reasons


class RefinedBank:
    """Only the one-hour provider changes; 4/8/12/24h providers are unchanged."""
    def __init__(self,ordinary,point,provenance=None):
        self.ordinary=ordinary;self.point=point;self.context_hours=ordinary.context_hours
        self.provenance=dict(provenance or {})

    def prepare_reference(self,normal_history):
        x,o,a,_,_=validated_frame(normal_history)
        return CombinedReference(self.ordinary.prepare_reference(normal_history),self.point.prepare_reference(x,o,a))

    def score_arrays(self,reference,x,o,a,analysis_hours,offset_hours=0):
        h=checked_hours(analysis_hours)
        if h==1:return self.point.score_arrays(reference.point,x,o,a,offset_hours)
        return self.ordinary.score_arrays(reference.ordinary,x,o,a,h,offset_hours)

    def threshold(self,h):
        return float(self.point.config['threshold'] if h==1 else self.ordinary.settings[h]['threshold'])

    def predict_batch(self,reference,batch,analysis_hours=4):
        h=checked_hours(analysis_hours)
        if h!=1:
            out=self.ordinary.predict_batch(reference.ordinary,batch,h)
            out['model_type']='normal_window_statistics';return out
        x,o,a,times,case=validated_frame(batch)
        if len(x)!=h:raise ValueError('The selected one-hour batch requires exactly one row')
        base=reference.ordinary
        if case!=base.case_id:raise ValueError('Reference and monitoring groups differ')
        if str(times.tz)!=str(base.end_time.tz):raise ValueError('Reference timezone differs')
        delta=times[0].value-base.end_time.value-HOUR_NS
        if delta<0 or delta%HOUR_NS:raise ValueError('Monitoring must follow normal reference at a whole-hour offset')
        scores,reasons=self.point.score_arrays(reference.point,x,o,a,int(delta//HOUR_NS));threshold=self.threshold(1)
        return pd.DataFrame({'case_id':case,'timestamp':times,'analysis_hours':1,
            'batch_start':times[0],'batch_end_exclusive':times[-1]+pd.Timedelta(hours=1),
            'available_at':times[-1]+pd.Timedelta(hours=1),'score':scores,'threshold':threshold,
            'is_anomaly':np.isfinite(scores)&(scores>threshold),'assessable':np.isfinite(scores),'reason':reasons,
            'retrospective':True,'score_scope':'selected_batch','monitoring_state_carried':False,
            'model_type':'normal_AR_innovation_density'})

    def record(self):
        return {'analysis_hours':list(HORIZONS),'normal_reference_hours':self.context_hours,
            'monitoring_state_carried':False,'outside_batch_monitoring_used':False,
            'normal_reference_auto_updated':False,'point_adjustment':False,
            'one_hour_config':self.point.config,'other_lengths_unchanged':True,'provenance':self.provenance}


def load_bank(path):
    value=joblib.load(path)  # Only load trusted local artifacts.
    if not isinstance(value,RefinedBank):raise TypeError('Expected a RefinedBank artifact')
    return value


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model',type=Path,required=True);p.add_argument('--input',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--analysis-hours',type=int,choices=HORIZONS,required=True)
    p.add_argument('--split-batches',action='store_true');args=p.parse_args()
    if args.output.exists() or args.output.resolve()==args.input.resolve():raise FileExistsError('Do not overwrite existing files')
    result=score_frame(load_bank(args.model),pd.read_csv(args.input),args.analysis_hours,args.split_batches)
    args.output.parent.mkdir(parents=True,exist_ok=True);result.to_csv(args.output,index=False,encoding='utf-8-sig')
    print(f'{len(result)} hourly decisions, no monitoring-state carry',flush=True)
