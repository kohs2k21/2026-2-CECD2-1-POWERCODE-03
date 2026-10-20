"""Bounded hourly statistical baselines. No observed test updates or refits."""
from __future__ import annotations

import multiprocessing as mp
import os
import time
import warnings
import numpy as np
import pandas as pd

from .features import rolling


def calendar(index):
    index=pd.DatetimeIndex(index).tz_convert('Asia/Seoul')
    hour=index.hour.to_numpy()
    return np.column_stack([np.sin(2*np.pi*hour/24),np.cos(2*np.pi*hour/24),
                            *[(index.dayofweek.to_numpy()==i).astype(float) for i in range(1,7)]])


def _fit(y,index,method,variant):
    if method=='R21':
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
        damped=variant.get('trend')=='damped'
        model=ExponentialSmoothing(y,trend='add' if damped else None,damped_trend=damped,
                                   seasonal='add',seasonal_periods=24,initialization_method='estimated')
        return model.fit(optimized=True,minimize_kwargs={'options':{'maxiter':100}})
    if method=='R22':
        from statsmodels.tsa.seasonal import STL
        fitted=STL(y,period=24,seasonal=13,robust=True).fit()
        hour=index.hour.to_numpy()
        template=np.array([np.mean(fitted.seasonal[hour==h]) for h in range(24)])
        level=float(np.median((y-template[hour])[-168:]))
        return {'template':template,'level':level}
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    center=float(np.mean(y));scale=float(np.std(y))
    if scale<=0:return {'point_mass':center,'center':center,'scale':0.}
    standardized=(np.asarray(y)-center)/scale
    model=SARIMAX(standardized,exog=calendar(index),order=tuple(variant.get('order',[1,0,0])),
                  seasonal_order=(0,0,0,0),trend='c',enforce_stationarity=True,
                  enforce_invertibility=True)
    fitted=model.fit(disp=False,maxiter=200)
    if not fitted.mle_retvals.get('converged',False):
        raise ValueError('arima_optimizer_not_converged')
    return {'result':fitted,'center':center,'scale':scale}


def _predict(model,index,method):
    if method=='R22':
        return model['level']+model['template'][pd.DatetimeIndex(index).hour.to_numpy()]
    if method=='R23':
        if 'point_mass' in model:return np.full(len(index),model['point_mass'])
        return model['center']+model['scale']*np.asarray(model['result'].get_forecast(len(index),exog=calendar(index)).predicted_mean,float)
    return np.asarray(model.forecast(len(index)),float)


def _worker(connection,payload):
    started=time.process_time()
    try:
        from threadpoolctl import threadpool_limits
        with threadpool_limits(limits=1), warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            index=pd.DatetimeIndex(payload['index'])
            method,variant=payload['method'],payload['variant']
            result={'status':'ok','metrics':{},'fit_scope':'train_hourly_only',
                    'prediction_policy':'frozen_open_loop_no_observed_test_updates',
                    'hourly_resolution':60,'train_end':str(index[-1]+pd.Timedelta(hours=1))}
            for metric,original in payload['metrics'].items():
                y=np.asarray(original,float)
                observed=np.isfinite(y)
                if observed.sum()<168:
                    result['metrics'][metric]={'status':'unsupported','reason':'fewer_than_168_observed_hours'}
                    continue
                # Only structurally idle train hours receive a frozen train
                # hour-of-day expectation for algorithms requiring dense input.
                def fill_prefix(values,ticks):
                    dense=values.copy();goodvalues=np.isfinite(values)
                    hours=ticks.hour.to_numpy();pooled=float(np.nanmedian(values))
                    for hour in range(24):
                        good=goodvalues&(hours==hour)
                        replacement=float(np.median(values[good])) if good.any() else pooled
                        dense[~goodvalues&(hours==hour)]=replacement
                    return dense
                filled=fill_prefix(y,index)
                errors=[]; folds=[]
                for stop in range(168,len(y)-23,168):
                    finish=min(stop+168,len(y))
                    trained=_fit(fill_prefix(y[:stop],index[:stop]),index[:stop],method,variant)
                    prediction=_predict(trained,index[stop:finish],method)
                    valid=observed[stop:finish]&np.isfinite(prediction)
                    errors.extend((y[stop:finish][valid]-prediction[valid]).tolist())
                    folds.append({'train_hours':stop,'prediction_hours':finish-stop,'observed_residuals':int(valid.sum())})
                if len(errors)<30:
                    result['metrics'][metric]={'status':'unsupported','reason':'insufficient_rolling_origin_residuals'}
                    continue
                scale=float(np.std(errors,ddof=1))
                trained=_fit(filled,index,method,variant)
                result['metrics'][metric]={'status':'ok','model':trained,'scale':scale,
                    'hourly_known_idle_imputations':int((~observed).sum()),
                    'imputation_policy':'train_hour_median_then_train_pooled_median; never_observation_support',
                    'rolling_origin':folds,'residual_count':len(errors),
                    'fit_method':method,'variant':variant,'clamp_policy':'nonnegative_physical_expectation'}
            result['warnings']=sorted({f'{type(w.message).__name__}:{str(w.message)}' for w in caught})
            result['cpu_seconds']=time.process_time()-started
            connection.send(result)
    except Exception as exc:
        connection.send({'status':'failed','reason':f'{type(exc).__name__}:{exc}',
                         'cpu_seconds':time.process_time()-started})
    finally:
        connection.close()


def fit_forecasts(aggregates,method,variant,*,cpu_limit=120.,rss_limit=1024**3):
    """Each group fit is isolated and terminated on the declared CPU/RSS bounds."""
    import psutil
    output={}
    context=mp.get_context('spawn')
    for group,a in aggregates.items():
        f=a.source.copy()
        f=f[(f.bin_start>=a.start)&(f.bin_start<a.end)&f.source_complete&f.category_supported]
        hourly=f.set_index('bin_start').resample('60min').agg(count=('count','sum'),duration_ms=('duration_ms','mean'))
        payload={'index':hourly.index,'metrics':{m:hourly[m].to_numpy(float) for m in ('count','duration_ms')},
                 'method':method,'variant':variant}
        parent,child=context.Pipe(duplex=False)
        process=context.Process(target=_worker,args=(child,payload))
        started=time.perf_counter();process.start();child.close();peak=0
        status=None
        while process.is_alive() and not parent.poll(.1):
            try:
                p=psutil.Process(process.pid);peak=max(peak,p.memory_info().rss)
                cpu=p.cpu_times().user+p.cpu_times().system
                if cpu>cpu_limit or peak>rss_limit:
                    status={'status':'resource_limited','reason':'fit_cpu_limit' if cpu>cpu_limit else 'fit_rss_limit',
                            'cpu_seconds':cpu,'peak_rss':peak}
                    process.terminate();break
            except psutil.NoSuchProcess:break
        if status is None:
            status=parent.recv() if parent.poll(1) else {'status':'failed','reason':'forecast_worker_no_result'}
        process.join(5)
        if process.is_alive():process.terminate();process.join()
        parent.close()
        status.update(wall_seconds=time.perf_counter()-started,peak_rss=peak)
        output[group]=status
    return output


def forecast_score(bundle,a,metric,width):
    out=np.full(len(a.index),np.nan)
    if bundle.get('status')!='ok': return out
    fitted=bundle['metrics'].get(metric,{})
    if fitted.get('status')!='ok' or fitted['scale']<=0:return out
    origin=pd.Timestamp(bundle['train_end'])
    # Observation E covers [E-A,E); the hour of that interval owns its expectation.
    hours=(a.index-pd.Timedelta(minutes=a.minutes)).floor('h')
    first=hours.min();last=hours.max()
    if last<origin:return out
    prediction_index=pd.date_range(origin,last+pd.Timedelta(hours=1),freq='h',inclusive='left')
    method=fitted['fit_method'];key=(metric,str(last))
    cache=bundle.setdefault('_prediction_cache',{})
    if key not in cache:
        cache[key]=np.maximum(0.,_predict(fitted['model'],prediction_index,method))
    position=((hours-origin).total_seconds()/3600).astype(int)
    expected=np.full(len(position),np.nan)
    supported=position>=0
    expected[supported]=cache[key][position[supported]]
    scale=fitted['scale']
    if metric=='count':expected=expected/60*a.minutes;scale=scale/60*a.minutes
    residual=np.abs(a.values[metric]-expected)/scale
    residual[~a.valid]=np.nan
    return rolling(residual,width,minimum=width if metric=='count' else max(1,min(width,2)))
