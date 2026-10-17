"""The finite 24-method SR registry; frozen fit/score separation and shared caches."""
from __future__ import annotations

from dataclasses import dataclass, field
import copy
import hashlib
import time
import warnings
import numpy as np
import pandas as pd
from scipy.stats import poisson, nbinom, binom, betabinom
from scipy.special import logsumexp

from . import features as ft
from .references import statistics, fit_normal, scaled_distance

METHODS = {f'R{i:02}': name for i, name in enumerate((
    'typed_sr', 'z', 'mad', 'iqr', 'empirical_tail', 'excess_ratio',
    'ewma', 'finite_cusum', 'front_back_median', 'ols_slope', 'known_zero',
    'poisson', 'negative_binomial', 'binomial', 'beta_binomial',
    'pearson', 'spearman', 'linear_residual', 'mahalanobis', 'ks',
    'wasserstein', 'holt_winters', 'stl_template', 'arima_calendar'))}


def variants(method):
    if method == 'R03': return [{'k':1.5},{'k':3.}]
    if method == 'R07': return [{'k':.25},{'k':.5},{'k':1.}]
    if method == 'R18': return [{'ridge':.01},{'ridge':.1}]
    if method == 'R21': return [{'trend':'none'},{'trend':'damped'}]
    if method == 'R23': return [{'order':[1,0,0]},{'order':[1,0,1]}]
    return [{}]


@dataclass(frozen=True)
class Configuration:
    method: str
    window: int
    aggregation: int = 1
    baseline: str = 'B1'
    variant: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.method not in METHODS or self.window < self.aggregation:
            raise ValueError('invalid_method_configuration')

    @property
    def width(self):
        return int(self.window//self.aggregation)


@dataclass
class MethodReference:
    config: Configuration
    normal: dict
    groups: dict
    fit_seconds: float
    legacy: dict | None = None
    forecast: dict | None = None


@dataclass
class ScoreTrace:
    aggregate: ft.Aggregate
    values: dict
    reasons: dict
    parameter_kind: str

    @property
    def support(self):
        return {key:np.isfinite(value) for key,value in self.values.items()}


class FeatureCache:
    """Owned by exactly one source/reference pair. No train/test state sharing."""
    def __init__(self, aggregates, normal):
        self.aggregates, self.normal = aggregates, normal
        self.z = {group:normal[group].residuals(a) for group,a in aggregates.items()}
        self.cache = {}

    def window(self, group, width):
        key = ('window', group, width)
        if key in self.cache: return self.cache[key]
        a = self.aggregates[group]
        result = {}
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', RuntimeWarning)
            for metric in ('count','duration_ms'):
                z = self.z[group][metric]
                observed = np.isfinite(z)
                minimum = width if metric=='count' else max(1, min(width, 2))
                result[metric] = {'mean':ft.rolling(z,width,minimum=minimum),
                                  'std':ft.rolling(z,width,'std',minimum=max(2,minimum)),
                                  'n':ft.rolling(observed.astype(float),width,'sum')}
        pair = np.isfinite(self.z[group]['count']) & np.isfinite(self.z[group]['duration_ms'])
        result['pair_n'] = ft.rolling(pair.astype(float),width,'sum')
        available = a.available_at.as_unit('ns').asi8.astype(float)
        pending = ft.rolling(available,width,'max') > a.index.as_unit('ns').asi8+2*ft.MINUTE_NS
        source = (ft.rolling(a.valid.astype(float),width,'sum') == width) & ~pending
        result['source'] = source
        self.cache[key] = result
        return result

    def correlation(self, group, width, rank=False):
        key=('spearman' if rank else 'pearson',group,width)
        if key not in self.cache:
            x,y=self.z[group]['count'],self.z[group]['duration_ms']
            self.cache[key] = (ft.spearman(x,y,width) if rank else
                               ft.pearson(x,y,width)) if width>=30 else np.full(len(x),np.nan)
        return self.cache[key]

    def distances(self, group, width, metric):
        key=('distances',group,width,metric)
        if key not in self.cache:
            reference=self.normal[group].metrics.get('_residuals',{}).get(metric)
            if reference is None:
                raise ValueError('train_residual_distribution_missing')
            self.cache[key] = ft.distribution_distances(self.z[group][metric], width, reference,
                                                       minimum=width if metric=='count' else max(2,width//2))
        return self.cache[key]


def _mask_training(a):
    context=ft.rolling(a.valid.astype(float),max(1,1440//a.minutes),'sum')==max(1,1440//a.minutes)
    return (a.index>=a.start)&(a.index<a.end)&a.valid&context&(a.available_at<=a.end)


def _legacy_stream(aggregates, split, seed=0):
    frames=[]; aliases={}
    for group,a in aggregates.items():
        frame=a.source.copy()
        for key in ('bin_start','endpoint','available_at'):
            frame[key]=pd.DatetimeIndex(frame[key]).tz_convert('UTC')
        frame['adapter']=f'transport:{group}'
        frame['interface']=np.where(frame.category_supported, f'interface:{group}', 'unseen')
        import json
        aliases[json.dumps([f'transport:{group}',f'interface:{group}'],ensure_ascii=False)]=group
        frame['status']='S'
        frame['split']=split
        frame['seed']=int(seed)
        frame['minute_index']=((pd.DatetimeIndex(frame.bin_start)-a.start).total_seconds()/60).astype(int)
        frames.append(frame)
    result=pd.concat(frames,ignore_index=True)
    result.attrs['registry']={'aliases':aliases,'version':'synthetic_group_whitelist_v1'}
    result.attrs['scored_end']=max(a.end for a in aggregates.values())
    return result


def _fit_legacy(aggregates, config, cache, seed):
    from research.evaluation.long_term_trend import features as legacy
    from research.evaluation.long_term_trend.dataset import load_protocol
    if config.aggregation!=1 or config.baseline!='B1':
        return {'status':'adapted_required','reason':'legacy_recipe_only_A1_B1'}
    if seed is None:raise ValueError('R00_requires_explicit_sampling_seed')
    stream=_legacy_stream(aggregates,'train',seed)
    if 'legacy_reference' not in cache.cache:
        protocol=copy.deepcopy(load_protocol())
        protocol.update(max_window_minutes=1440,window_hours=[x/60 for x in (5,10,15,30,45,60,90,120,180,240,300,360,420,480,540,600,660,720,1080,1440)],cadence_candidates=[1,2,5,10,15,30,60])
        cache.cache['legacy_reference']=legacy.fit_reference(stream,protocol)
    reference=cache.cache['legacy_reference']
    pack=legacy.build_features(stream,reference,config.window/60,5)
    matrix=pack['X']['trend']; valid=pack['feature_validity']['trend']; names=pack['feature_names']['trend']
    from research.evaluation.trend_family_comparison.detectors import family_indices
    families=family_indices(names)
    meta=pack['metadata'];train=meta.ready.to_numpy(bool)&meta.train_mask.to_numpy(bool)
    indices=np.flatnonzero(train);precap=len(indices)
    if len(indices)>4096:indices=np.sort(np.random.default_rng(seed).choice(indices,4096,replace=False))
    from research.evaluation.long_term_trend.models import StatisticalReference
    fitted=StatisticalReference.fit(matrix[indices],valid[indices],names,legacy.NEUTRAL_NAMES)
    stats=[{'status':'ok' if fitted.active[j] else 'unsupported','median':float(fitted.median[j]),
            'range90':float(fitted.scale[j]),'active':bool(fitted.active[j])} for j in range(len(names))]
    return {'status':'ok','reference':reference,'statistics':stats,'families':families,
            'names':names,'recipe':'exact_legacy43_same_maximal_context_1440',
            'float_dtype':'legacy_float32','legacy_calendar_timezone':'UTC',
            'sampling_seed':int(seed),'sampling_cadence':5,'sampling_cap':4096,
            'pre_cap_rows':precap,'sampled_rows':len(indices),
            'sampled_row_id_sha256':hashlib.sha256('\n'.join(meta.iloc[indices].row_id).encode()).hexdigest(),
            'comparison_limit':'new methods use KST; differences combine formula and calendar interpretation'}


def _adapted_components(cache,group,width):
    """Explicit A/B sensitivity recipe, never labelled legacy43-equivalent."""
    a=cache.aggregates[group];pack={}
    for family,metric in (('count','count'),('latency','duration_ms')):
        z=cache.z[group][metric];short=min(5,width);medium=min(15,width)
        ref=cache.normal[group].metrics['_residuals'][metric]
        dist=np.sort(ref);n=len(dist)
        upper=-np.log((n-np.searchsorted(dist,z,side='left')+1)/(n+1)) if n else np.full(len(z),np.nan)
        lower=-np.log((np.searchsorted(dist,z,side='right')+1)/(n+1)) if n else np.full(len(z),np.nan)
        upper[~np.isfinite(z)]=np.nan;lower[~np.isfinite(z)]=np.nan
        tail=np.maximum(upper,lower)
        half=width//2
        change=(ft.rolling(z,half,'mean')-ft.shift(ft.rolling(z,width-half,'mean'),half)) if half else np.full(len(z),np.nan)
        disclosed=a.valid&(a.available_at.as_unit('ns').asi8<=a.index.as_unit('ns').asi8+2*ft.MINUTE_NS)
        causal_tail=np.where(disclosed,tail,np.nan)
        values=[upper,lower,ft.rolling(tail,short,minimum=1),ft.rolling(tail,medium,minimum=1),
                ft.rolling(tail,width,minimum=1),np.minimum(ft.run_length(a.values['count']==0),width),
                ft.ewma(causal_tail,max(1,width/5)),tail-ft.shift(tail,1),
                ft.rolling(z,width,minimum=1),ft.rolling(z,width,'std',minimum=2),
                ft.rolling(z,width,'max',minimum=1),ft.rolling(z,width,'min',minimum=1),
                ft.rolling_slope(z,width)*width,change]
        names=['upper_tail','lower_tail','tail_mean5','tail_mean15','tail_meanW','known_zero_run',
               'tail_ewma','tail_change','residual_meanW','residual_stdW','residual_maxW','residual_minW',
               'residual_slopeW','front_back_mean']
        if family=='latency':
            values.pop(5);names.pop(5)
        pack[family]=(np.column_stack(values),names)
    relation=cache.normal[group].relation
    residual=(cache.z[group]['duration_ms']-relation.get('intercept',0)-relation.get('beta',0)*cache.z[group]['count'])
    pack['relation']=(np.column_stack([cache.correlation(group,width),
                        ft.rolling(np.abs(residual),width,minimum=min(width,30))]),
                      ['count_duration_correlation','absolute_linear_residual'])
    return pack


def fit_method(aggregates, config, normal=None, cache=None, *, split='train', forecast=None, seed=None):
    """Only train aggregates may fit. C is deliberately absent from this API."""
    if split!='train': raise ValueError('method_fit_train_only')
    started=time.perf_counter()
    normal=fit_normal(aggregates,config.baseline) if normal is None else normal
    cache=FeatureCache(aggregates,normal) if cache is None else cache
    for group,a in aggregates.items():
        normal[group].metrics['_residuals']={m:cache.z[group][m][_mask_training(a)&np.isfinite(cache.z[group][m])]
                                             for m in ('count','duration_ms')}
    groups={}; legacy=None
    if config.method=='R00':
        legacy=_fit_legacy(aggregates,config,cache,seed)
    for group,a in aggregates.items():
        w=cache.window(group,config.width)
        mask=_mask_training(a)&w['source']
        fitted={'status':'ok','statistics':{},'reason':None}
        for metric in ('count','duration_ms'):
            skey=('statistics',group,config.width,metric)
            if skey not in cache.cache:
                cache.cache[skey]=statistics(w[metric]['mean'][mask])
            fitted['statistics'][metric]=copy.copy(cache.cache[skey])
            if config.method!='R04':fitted['statistics'][metric].pop('values',None)
        if config.method=='R00' and legacy['status']!='ok':
            adapted=_adapted_components(cache,group,config.width)
            fitted['adapted']={}
            for family,(matrix,names) in adapted.items():
                tables=[]
                for column in matrix.T:
                    table=statistics(column[mask]);table.pop('values',None);tables.append(table)
                fitted['adapted'][family]={'names':names,'statistics':tables}
            fitted['adapted_recipe']='typed_sr_A_B_adapted_v1_29features_not_legacy43'
        if config.method in ('R15','R16'):
            fitted['statistics']['relation']=statistics(cache.correlation(group,config.width,config.method=='R16')[mask])
            fitted['statistics']['relation'].pop('values',None)
        if config.method=='R12':
            sums=ft.rolling(a.values['count'],config.width,'sum')
            expected=ft.rolling(normal[group].lookup(a,'count','mean'),config.width,'sum')
            good=mask&np.isfinite(sums)&(expected>0)
            alpha=np.mean(((sums[good]-expected[good])**2-expected[good])/expected[good]**2) if good.any() else np.nan
            fitted['alpha']=max(float(alpha),0.) if np.isfinite(alpha) else None
        if config.method in ('R13','R14'):
            total=a.values['total_count']; error=a.values['error_count']
            good=_mask_training(a)&(total>0)&np.isfinite(error)
            # Jeffreys smoothing is a named frozen rare-event prior.
            p=(error[good].sum()+.5)/(total[good].sum()+1) if good.any() else np.nan
            rate=error[good]/total[good]
            variance=float(np.var(rate,ddof=1)) if len(rate)>1 else 0.
            invn=float(np.mean(1/total[good])) if good.any() else np.nan
            rho=max(0.,(variance/(p*(1-p))-invn)/max(1-invn,1e-15)) if good.any() else np.nan
            rho=min(rho,.999999) if np.isfinite(rho) else rho
            concentration=(1/rho-1) if rho>0 else 1e8
            fitted.update(p=float(p),alpha_beta=[float(p*concentration),float((1-p)*concentration)],
                          beta_binomial_policy='train_mom_between_bin_overdispersion_with_Jeffreys_rare_event_mean')
        if config.method=='R18':
            vector=np.column_stack([w['count']['mean'],w['duration_ms']['mean'],w['count']['std'],w['duration_ms']['std']])
            good=mask&np.isfinite(vector).all(axis=1)&(w['pair_n']>=30)
            v=vector[good]
            if len(v)<max(30,10*vector.shape[1]):
                fitted.update(status='unsupported',reason='covariance_train_pairs_below_40')
            else:
                covariance=np.cov(v,rowvar=False); rank=int(np.linalg.matrix_rank(covariance))
                diagonal=np.diag(covariance)
                if rank<vector.shape[1] or np.any(diagonal<=0):
                    fitted.update(status='unsupported',reason='covariance_rank_deficient',rank=rank)
                else:
                    regularized=covariance+config.variant.get('ridge',.01)*np.diag(diagonal)
                    fitted.update(mean=v.mean(axis=0),inverse=np.linalg.inv(regularized),rank=rank,
                                  condition=float(np.linalg.cond(regularized)),train_rows=len(v))
        groups[group]=fitted
    return MethodReference(config,normal,groups,time.perf_counter()-started,legacy,forecast)


def _tail(distribution, value, *args, two_sided=True):
    # Exact inclusive discrete tails; scipy survival avoids 1-CDF cancellation.
    logupper=distribution.logsf(np.asarray(value)-1,*args)
    if distribution is betabinom:
        broadcast=np.broadcast_arrays(np.asarray(value,float),*[np.asarray(x,float) for x in args])
        good=np.logical_and.reduce([np.isfinite(x) for x in broadcast])
        broken=np.isnan(logupper)&good
        if np.any(broken):
            logupper=np.asarray(logupper).copy()
            for index in np.flatnonzero(broken):
                k,n,a,b=(x.ravel()[index] for x in broadcast)
                # SciPy's 1-CDF path can round slightly negative. Sum exact PMFs
                # in log space only on those entries, retaining all tail mass.
                points=np.arange(max(0,int(np.ceil(k))),int(n)+1,dtype=float)
                logupper.ravel()[index]=logsumexp(betabinom.logpmf(points,n,a,b)) if len(points) else -np.inf
    floor=np.log(np.finfo(np.float64).tiny)
    if not two_sided: return -np.maximum(logupper,floor)
    loglower=distribution.logcdf(value,*args)
    return -np.maximum(floor,np.minimum(np.log(1.),np.log(2.)+np.minimum(logupper,loglower)))


def _legacy_score(reference, aggregates):
    from research.evaluation.long_term_trend import features as legacy
    stream=_legacy_stream(aggregates,'selection',reference.legacy.get('sampling_seed',0))
    pack=legacy.build_features(stream,reference.legacy['reference'],reference.config.window/60,5)
    x=pack['X']['trend'];valid=pack['feature_validity']['trend'];meta=pack['metadata']
    centers=np.array([s.get('median',0.) for s in reference.legacy['statistics']])
    scales=np.array([max(s.get('range90',0.),.05) for s in reference.legacy['statistics']])
    raw=np.abs((x-centers)/scales)
    result={}
    for group,a in aggregates.items():
        mask=meta.group.eq(group).to_numpy(); out={}; reasons={}
        for key,columns in reference.legacy['families'].items():
            available=(valid[mask][:,columns]&np.isfinite(x[mask][:,columns])
                       &np.array([reference.legacy['statistics'][j]['active'] for j in columns])[None,:])
            supported=(available.all(axis=1) if key=='sr_relation' else available.any(axis=1))
            values=np.max(np.where(available,raw[mask][:,columns],-np.inf),axis=1)
            values[~supported]=np.nan
            family={'sr_count':'count','sr_latency':'latency','sr_relation':'relation'}[key]
            # Legacy rows are publication ticks. Reindex their evidence endpoints
            # before the shared publisher adds watermark once, never twice.
            s=pd.Series(values,index=pd.DatetimeIndex(meta.loc[mask,'window_end']))
            out[family]=s.reindex(a.index).to_numpy(float)
            reasons[family]=np.where(np.isfinite(out[family]),'ok','legacy_not_ready')
        result[group]=ScoreTrace(a,out,reasons,'legacy_observation_window')
    return result


def score_method(reference, aggregates, cache=None):
    cfg=reference.config; method=cfg.method; width=cfg.width
    if method=='R00' and reference.legacy and reference.legacy['status']=='ok':
        return _legacy_score(reference,aggregates)
    cache=FeatureCache(aggregates,reference.normal) if cache is None else cache
    result={}
    for group,a in aggregates.items():
        ref=reference.normal[group]; fitted=reference.groups[group]
        w=cache.window(group,width); values={}; reasons={}
        if method=='R00':
            adapted=_adapted_components(cache,group,width)
            for family,(matrix,names) in adapted.items():
                state=fitted['adapted'][family]
                if names!=state['names']:raise ValueError('adapted_feature_names_changed')
                tables=state['statistics']
                supported=np.isfinite(matrix)&np.array([s['status']=='ok' for s in tables])
                centers=np.array([s.get('median',0.) for s in tables])
                scales=np.array([max(s.get('range90',0.),.05) for s in tables])
                deviation=np.where(supported,np.abs((matrix-centers)/scales),-np.inf)
                out=np.max(deviation,axis=1)
                ready=(supported.all(axis=1) if family=='relation' else supported.any(axis=1))&w['source']
                if family=='relation':ready &= w['pair_n']>=30
                out[~ready]=np.nan;values[family]=out
                reasons[family]=np.where(ready,'ok_R00_adapted_not_legacy43','adapted_not_ready')
            result[group]=ScoreTrace(a,values,reasons,'adapted_observation_window_not_legacy43')
            continue
        family_metrics=(('count','count'),('latency','duration_ms'))
        for family,metric in family_metrics:
            s=fitted['statistics'][metric]; x=w[metric]['mean']; z=cache.z[group][metric]
            out=np.full(len(x),np.nan)
            if s['status']!='ok':
                values[family]=out; reasons[family]=np.full(len(x),'no_finite_train_window',object);continue
            if method=='R00':
                # A/B sensitivity is explicitly adapted, not legacy-equivalent.
                components=np.column_stack([x,w[metric]['std'],ft.rolling(z,width,'max'),ft.rolling(z,width,'min')])
                out=np.nanmax(np.abs(components-s['median'])/max(s['range90'],.05),axis=1)
            elif method=='R01': out=scaled_distance(x,s['mean'],s['std'])
            elif method=='R02': out=scaled_distance(x,s['median'],1.4826*s['mad'])
            elif method=='R03':
                k=cfg.variant.get('k',1.5)
                upper=x-(s['q75']+k*s['iqr']); lower=(s['q25']-k*s['iqr'])-x
                out=np.maximum(0,np.maximum(upper,lower)) if s['iqr']==0 else np.maximum(0,np.maximum(upper,lower))/s['iqr']
            elif method=='R04':
                n=s['n']; dist=s['values']
                lower=(np.searchsorted(dist,x,side='right')+1)/(n+1)
                upper=(n-np.searchsorted(dist,x,side='left')+1)/(n+1)
                out=-np.log(np.minimum(1,2*np.minimum(lower,upper)))
            elif method=='R05':
                rawmean=ft.rolling(a.values[metric],width,minimum=width if metric=='count' else max(1,min(2,width)))
                hi=ref.lookup(a,metric,'q95');lo=ref.lookup(a,metric,'q05')
                # Count has integer resolution 1; duration has declared ms resolution 1.
                out=np.maximum(np.divide(rawmean-hi,hi,out=np.where(rawmean>hi,1.,0.),where=hi>0),
                               (lo-rawmean)/np.maximum(lo,1.))
                out=np.maximum(out,0.)
            elif method=='R06':
                disclosed=a.valid&(a.available_at.as_unit('ns').asi8<=a.index.as_unit('ns').asi8+2*ft.MINUTE_NS)
                causal=np.where(disclosed,z,np.nan)
                out=np.abs(ft.ewma(causal,cfg.window/a.minutes))
                out[ft.run_length(np.isfinite(causal))<width]=np.nan
            elif method=='R07':
                k=cfg.variant.get('k',.5)
                disclosed=a.valid&(a.available_at.as_unit('ns').asi8<=a.index.as_unit('ns').asi8+2*ft.MINUTE_NS)
                z=np.where(disclosed,z,np.nan)
                func=ft.unbounded_cusum if cfg.variant.get('unbounded') else ft.finite_cusum
                out=(np.maximum(func(z,k=k),func(-z,k=k)) if cfg.variant.get('unbounded') else
                     np.maximum(func(z,width,k),func(-z,width,k)))
            elif method=='R08':
                half=width//2
                if half>0:
                    front=ft.shift(ft.rolling(z,width-half,'median'),half)
                    back=ft.rolling(z,half,'median')
                    out=np.abs(back-front)
            elif method=='R09': out=np.abs(ft.rolling_slope(z,width))*width
            elif method in ('R19','R20'):
                out=cache.distances(group,width,metric)[0 if method=='R19' else 1]
            elif method in ('R21','R22','R23'):
                if reference.forecast and group in reference.forecast:
                    from .forecasting import forecast_score
                    out=forecast_score(reference.forecast[group],a,metric,width)
            else: continue
            out=np.array(out,dtype=float,copy=True); out[~w['source']]=np.nan
            if method in ('R01','R02','R03','R04'):
                out[~np.isfinite(x)]=np.nan
            if family=='latency':out[w[metric]['n']<max(1,min(2,width))]=np.nan
            values[family]=out;reasons[family]=np.where(np.isfinite(out),'ok','metric_or_reference_unsupported')
        if method in ('R10','R11','R12'):
            count=a.values['count']; mu=ref.lookup(a,'count','mean')
            n=ft.rolling(count,width,'sum'); expectation=ft.rolling(mu,width,'sum')
            if method=='R10':
                # Known idle is not an outage. Missing bins break a run.
                known=a.valid&a.activity&np.isfinite(count)
                out=np.minimum(ft.run_length(known&(count==0)),width)*mu
                out[~known]=np.nan
            elif method=='R11':out=_tail(poisson,n,expectation)
            else:
                alpha=fitted.get('alpha')
                out=(_tail(poisson,n,expectation) if alpha==0 else
                     _tail(nbinom,n,1/alpha,(1/alpha)/(1/alpha+expectation))) if alpha is not None else np.full(len(n),np.nan)
            out[~w['source']]=np.nan
            values={'count':out};reasons={'count':np.where(np.isfinite(out),'ok','count_not_supported')}
        if method in ('R13','R14'):
            n=ft.rolling(a.values['total_count'],width,'sum');k=ft.rolling(a.values['error_count'],width,'sum')
            out=(_tail(binom,k,n,fitted['p'],two_sided=False) if method=='R13' else
                 _tail(betabinom,k,n,*fitted['alpha_beta'],two_sided=False))
            out[(n<=0)|~w['source']]=np.nan
            values={'error':out};reasons={'error':np.where(np.isfinite(out),'ok','zero_total_or_unknown')}
        if method in ('R15','R16','R17','R18'):
            out=np.full(len(a.index),np.nan); pair=w['pair_n']>=30
            if method in ('R15','R16'):
                s=fitted['statistics']['relation']
                if s['status']=='ok':
                    out=scaled_distance(cache.correlation(group,width,method=='R16'),s['median'],s['range90'])
            elif method=='R17' and ref.relation['status']=='ok' and width>=30:
                residual=cache.z[group]['duration_ms']-ref.relation['intercept']-ref.relation['beta']*cache.z[group]['count']
                scale=ref.relation['scale']['std']
                out=ft.rolling(np.abs(residual),width,minimum=30)/scale if scale>0 else out
            elif method=='R18' and fitted['status']=='ok':
                vector=np.column_stack([w['count']['mean'],w['duration_ms']['mean'],w['count']['std'],w['duration_ms']['std']])
                diff=vector-fitted['mean'];out=np.einsum('ij,jk,ik->i',diff,fitted['inverse'],diff)
            out[~pair|~w['source']]=np.nan
            values={'relation':out}; reasons={'relation':np.where(np.isfinite(out),'ok',fitted.get('reason') or 'fewer_than_30_variable_pairs')}
        result[group]=ScoreTrace(a,values,reasons,'halflife_minutes' if method=='R06' else
            'unbounded_cumulative_memory' if method=='R07' and cfg.variant.get('unbounded') else
            'maximum_suffix_minutes' if method=='R07' else
            'residual_window_minutes' if method in ('R21','R22','R23') else 'observation_window_minutes')
    return result
