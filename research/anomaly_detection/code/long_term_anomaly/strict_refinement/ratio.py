"""Explicit normal-vs-generic-perturbation density ratios.

No labels or monitoring history enter the normal reference or point features.
Moving/broadening the fitted normal distribution is a generic alternative,
not a claim to know which fault scenario or severity is happening at inference.
"""
from dataclasses import dataclass
import numpy as np
from scipy.special import logsumexp,log_ndtr
from ..models import fill_short_gaps

FEATURE_NAMES=tuple(f'{kind}:{channel}' for channel in ('requests','duration')
                    for kind in ('upward_shift','downward_shift','variance_broadening'))


def log_cdf_difference(high,low):
    """Stable log(Phi(high)-Phi(low)), including positive extreme tails."""
    a,b=np.asarray(high),np.asarray(low)
    top=np.where(b>0,log_ndtr(-b),log_ndtr(a))
    bottom=np.where(b>0,log_ndtr(-a),log_ndtr(b))
    with np.errstate(divide='ignore',invalid='ignore'):
        return top+np.log(-np.expm1(np.minimum(bottom-top,0.)))


def prepare(context,observed,available,seasonal_strength=1.,trend_strength=0.):
    x=np.asarray(context,float).copy();x[~observed]=np.nan;x[~available,1:]=np.nan
    x=fill_short_gaps(x)
    if len(x)%24 or len(x)<96:raise ValueError('Whole normal days, at least 96 hours required')
    phase=np.arange(len(x))%24
    base=np.median(x,axis=0)
    profile=np.asarray([np.median(x[phase==h],axis=0) for h in range(24)])
    profile=base+float(seasonal_strength)*(profile-base)
    residual=x-profile[phase]
    # Normal-only low-frequency trend, never updated from monitoring samples.
    day_centers=np.median(residual.reshape(-1,24,3),axis=1)
    days=np.arange(len(day_centers))
    slope=np.polyfit(days-day_centers.shape[0]+1,day_centers,1)[0]*float(trend_strength)/24
    midpoint=(len(x)-1)/2
    residual=residual-(np.arange(len(x))-midpoint)[:,None]*slope
    return profile,residual,slope,midpoint


def ratio_features(normal_residual,current_residual,observed,available,bandwidth=.2,shift_range=(.25,4.)):
    columns=[]
    lo,hi=shift_range
    if not 0<=lo<hi or bandwidth<=0:raise ValueError('Invalid generic perturbation parameters')
    for channel in range(2):
        ref=np.asarray(normal_residual[:,channel],float)
        scale=max(float(np.std(ref)),.03)
        ref=ref/scale
        x=np.nan_to_num(current_residual[:,channel]/scale,nan=0.)
        diff=x[:,None]-ref[None,:]
        bw=float(bandwidth)
        log_p0=logsumexp(-.5*(diff/bw)**2,axis=1)-np.log(len(ref)*bw*np.sqrt(2*np.pi))
        upward=logsumexp(log_cdf_difference((diff-lo)/bw,(diff-hi)/bw),axis=1)-np.log(len(ref)*(hi-lo))
        downward=logsumexp(log_cdf_difference((diff+hi)/bw,(diff+lo)/bw),axis=1)-np.log(len(ref)*(hi-lo))
        broadened=[]
        for noise_sigma in (.5,1.,2.):
            width=np.sqrt(bw*bw+noise_sigma*noise_sigma)
            broadened.append(logsumexp(-.5*(diff/width)**2,axis=1)-np.log(len(ref)*width*np.sqrt(2*np.pi)))
        log_q_variance=logsumexp(broadened,axis=0)-np.log(len(broadened))
        columns.extend([upward-log_p0,downward-log_p0,log_q_variance-log_p0])
    f=np.clip(np.column_stack(columns),-100.,100.)
    # Missing duration is disabled by mix_score too; not a fabricated zero.
    f[np.ix_(~available,[3,4,5])]=0.
    f[~observed]=np.nan
    return f


def mix_score(features,weights,available,mode='mixture'):
    f=np.asarray(features,float);w=np.asarray(weights,float)
    if f.shape[-1]!=6 or w.shape!=(6,) or (w<0).any() or not np.isfinite(w).all() or not w[:3].any():
        raise ValueError('Six nonnegative finite weights with request support required')
    effective=np.broadcast_to(w,f.shape).copy()
    effective[~np.asarray(available,bool),3:]=0.
    with np.errstate(divide='ignore'):
        log_w=np.log(effective)
    if mode=='mixture':s=logsumexp(f+log_w,axis=1)-np.log(effective.sum(axis=1))
    elif mode=='maximum':s=np.max(f+log_w,axis=1)-np.max(log_w,axis=1)
    else:raise ValueError('Unknown ratio combination')
    s[~np.isfinite(f).any(axis=1)]=np.nan
    return s
