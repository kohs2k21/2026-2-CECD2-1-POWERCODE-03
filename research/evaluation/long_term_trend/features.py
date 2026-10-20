"""Frozen train-only references and causal, bounded-window aggregate features.

No event truth/status/normal_confirmed/active/scenario/category identity enters X.
Every call returns the full minute trace; cadence only chooses score_mask. The
last included bin ends at tick-lag, so source endpoints are in (end-W,end].
Unknown inputs remain NaN and not_ready, never successful zero executions.
"""
from __future__ import annotations

import json
import numpy as np
import pandas as pd

from .dataset import load_protocol


BASE_NAMES = [
    "count_upper_tail_1m", "count_lower_tail_1m", "count_tail_mean_5m",
    "count_tail_mean_15m", "count_zero_run", "count_tail_ewma", "count_tail_change",
    "latency_upper_tail_1m", "latency_lower_tail_1m", "latency_upper_mean_5m",
    "latency_lower_mean_5m", "latency_upper_max_5m", "latency_upper_change",
    "latency_upper_ewma", "latency_upper_anchor", "count_tail_mean_W",
    "latency_tail_mean_W", "duration_observed", "hour_sin", "hour_cos", "weekend",
    "count_above_q95_ratio", "count_below_q05_ratio", "latency_above_q95_ratio",
    "count_excess_max_W", "latency_excess_max_W", "count_above_q95_run_minutes",
    "count_below_q05_run_minutes", "latency_above_q95_run_minutes",
    "count_exceed_fraction_W", "latency_exceed_fraction_W"]
TREND_NAMES = [
    "count_slope_per_hour", "duration_slope_per_hour", "count_front_back_difference",
    "duration_front_back_difference", "count_short_long_ratio", "duration_short_long_ratio",
    "count_volatility", "duration_volatility", "count_duration_correlation",
    "relation_absolute_residual", "relation_valid", "relation_pair_fraction"]
NEUTRAL_NAMES = ["duration_observed", "relation_valid", "relation_pair_fraction"]


def _roll(a, width, method="mean"):
    roll = pd.Series(a).rolling(int(width), min_periods=1)
    return getattr(roll, method)().to_numpy(float)


def _shift(a, amount, fill=np.nan):
    a = np.asarray(a)
    out = np.full(a.shape, fill, dtype=float)
    if amount < len(a):
        out[amount:] = a[:-amount] if amount else a
    return out


def _run(mask):
    index = np.arange(len(mask))
    last_false = np.maximum.accumulate(np.where(mask, -1, index))
    return index - last_false


def _bounded_ewma(a, width):
    """Finite trailing EWMA, no invisible pre-window recursive state."""
    a = np.nan_to_num(np.asarray(a, float))
    decay = .8
    n = np.arange(1, len(a) + 1)
    numerator = pd.Series(a).ewm(alpha=1-decay, adjust=True).mean().to_numpy() * (1-decay**n)
    numerator -= decay**width * _shift(numerator, width, 0.)
    return numerator / (1-decay**np.minimum(n, width))


def _slope(y, width):
    x = np.arange(len(y), dtype=float)
    ok = np.isfinite(y).astype(float)
    z = np.nan_to_num(y)
    n = _roll(ok, width, "sum")
    sx, sy = _roll(x*ok, width, "sum"), _roll(z, width, "sum")
    sxx, sxy = _roll(x*x*ok, width, "sum"), _roll(x*z, width, "sum")
    den = n*sxx-sx*sx
    return np.divide(60*(n*sxy-sx*sy), den, out=np.zeros(len(y)), where=den>1e-8), n >= 2


def _validate(stream):
    required = {"bin_start", "endpoint", "available_at", "group", "adapter", "interface",
                "count", "duration_ms", "status", "source_complete", "minute_index", "split"}
    if not required.issubset(stream.columns):
        raise ValueError(f"Missing source fields: {sorted(required-set(stream.columns))}")
    for _, frame in stream.groupby("group", sort=False):
        ns = pd.DatetimeIndex(frame["endpoint"]).as_unit("ns").asi8
        if len(ns)>1 and not np.all(np.diff(ns) == 60_000_000_000):
            raise ValueError("Source must contain one explicit row per contiguous minute/group")
        if not np.all(pd.DatetimeIndex(frame["endpoint"]).as_unit("ns").asi8-pd.DatetimeIndex(frame["bin_start"]).as_unit("ns").asi8 == 60_000_000_000):
            raise ValueError("Source bins must be [start,end) of exactly one minute")
        count = frame["count"].to_numpy(float)
        if np.any(np.isfinite(count) & (count < 0)):
            raise ValueError("Negative execution counts")


def _mapped(frame, registry):
    pairs = zip(frame["adapter"].astype(str), frame["interface"].astype(str))
    return np.asarray([registry["aliases"].get(json.dumps(list(pair), ensure_ascii=False)) for pair in pairs], object)


def _table(values):
    values = np.sort(np.asarray(values, float))
    values = values[np.isfinite(values)]
    if not len(values):
        raise ValueError("Normal training reference has no observed samples")
    return {"values": values, "median": float(np.median(values)),
            "q05": float(np.quantile(values,.05)), "q95": float(np.quantile(values,.95)),
            "scale": float(max(np.quantile(values,.9)-np.quantile(values,.1), .05))}


def _lookup(frame, reference, group, metric):
    ticks = pd.DatetimeIndex(frame["bin_start"])
    values = frame[metric].to_numpy(float)
    upper, lower = np.zeros(len(frame)), np.zeros(len(frame))
    med, scale, qlo, qhi = (np.empty(len(frame)) for _ in range(4))
    keys = list(zip(ticks.hour, (ticks.dayofweek >= 5).astype(int)))
    tables = reference["groups"][group][metric]
    for key in set(keys):
        ids = np.asarray([x == key for x in keys])
        tab = tables.get(f"{key[0]}:{key[1]}", tables["pooled"])
        med[ids], scale[ids], qlo[ids], qhi[ids] = tab["median"], tab["scale"], tab["q05"], tab["q95"]
        obs = ids & np.isfinite(values)
        n = len(tab["values"])
        # Conservative ranks retain a finite out-of-reference tail; raw excess
        # and strong SR keep information beyond the finite tail saturation.
        upper[obs] = -np.log((n-np.searchsorted(tab["values"],values[obs],side="left")+1)/(n+1))
        lower[obs] = -np.log((np.searchsorted(tab["values"],values[obs],side="right")+1)/(n+1))
    return {"upper":upper, "lower":lower, "median":med, "scale":scale, "q05":qlo, "q95":qhi}


def fit_reference(train_stream, protocol=None):
    """Fit once on the shared maximal-context normal S training cohort only."""
    p = load_protocol() if protocol is None else protocol
    _validate(train_stream)
    if set(train_stream["split"].unique()) != {"train"}:
        raise ValueError("Reference fitting accepts train split only")
    registry = train_stream.attrs.get("registry")
    if registry is None:
        raise ValueError("Explicit frozen category registry is required")
    reference = {"protocol":p, "registry":registry, "groups":{}, "fit_split":"train",
                 "feature_names":{"base":BASE_NAMES, "trend":BASE_NAMES+TREND_NAMES},
                 "sr_neutral_feature_names":NEUTRAL_NAMES}
    for group, frame in train_stream.groupby("group",sort=False):
        frame = frame.reset_index(drop=True)
        count, duration = frame["count"].to_numpy(float), frame["duration_ms"].to_numpy(float)
        known = frame["source_complete"].to_numpy(bool) & np.isfinite(count) & ((count==0)|np.isfinite(duration))
        cohort = known & (frame["status"].to_numpy()=="S") & (_mapped(frame,registry)==group)
        common = _roll(cohort.astype(float),p["max_window_minutes"],"sum") == p["max_window_minutes"]
        fit_mask = common & (frame["minute_index"].to_numpy()>=0)
        cutoff = pd.Timestamp(train_stream.attrs.get("scored_end", frame["endpoint"].max()))
        fit_mask &= pd.DatetimeIndex(frame["available_at"]) <= cutoff
        if fit_mask.sum()<p["reference_minimum"]:
            raise ValueError(f"not_ready: insufficient common training cohort for {group}")
        group_ref = {"fit_rows":int(fit_mask.sum())}
        ticks = pd.DatetimeIndex(frame["bin_start"])
        for metric in ("count","duration_ms"):
            values = frame[metric].to_numpy(float)
            tables = {"pooled":_table(values[fit_mask])}
            for hour in range(24):
                for weekend in (0,1):
                    mask = fit_mask & (ticks.hour==hour) & ((ticks.dayofweek>=5)==bool(weekend)) & np.isfinite(values)
                    if mask.sum()>=p["reference_minimum"]:
                        tables[f"{hour}:{weekend}"] = _table(values[mask])
            group_ref[metric]=tables
        reference["groups"][group]=group_ref
        c = _lookup(frame,reference,group,"count")
        d = _lookup(frame,reference,group,"duration_ms")
        x = count/np.maximum(c["median"],1)-1
        y = duration/np.maximum(d["median"],1)-1
        pairs = fit_mask & (count>0) & np.isfinite(y)
        if pairs.sum()<p["minimum_relation_pairs"]:
            raise ValueError(f"not_ready: insufficient count-duration train pairs for {group}")
        intercept,beta = np.linalg.lstsq(np.column_stack((np.ones(pairs.sum()),x[pairs])),y[pairs],rcond=None)[0]
        residual = y[pairs]-intercept-beta*x[pairs]
        group_ref["relation"]={"intercept":float(intercept),"beta":float(beta),
            "scale":float(max(np.quantile(residual,.9)-np.quantile(residual,.1),.05)),
            "correlation":float(np.corrcoef(x[pairs],y[pairs])[0,1]),"pairs":int(pairs.sum())}
    return reference


def _pending(frame, window, lag, source_mask):
    """Number of otherwise known bins in this window not yet available as-of tick."""
    ticks = pd.DatetimeIndex(frame["bin_start"]).as_unit("ns").asi8
    ends = pd.DatetimeIndex(frame["endpoint"]).as_unit("ns").asi8
    available = pd.DatetimeIndex(frame["available_at"]).as_unit("ns").asi8
    unit = 60_000_000_000
    start = ends+lag*unit
    finish = np.minimum(available, ends+(lag+window)*unit)
    mask = np.asarray(source_mask,bool) & (finish>start)
    diff = np.zeros(len(frame)+1,dtype=int)
    lo = np.searchsorted(ticks,start[mask],side="left")
    hi = np.searchsorted(ticks,finish[mask],side="left")
    np.add.at(diff,lo,1)
    np.add.at(diff,hi,-1)
    return np.cumsum(diff[:-1])


def build_features(stream, reference, window_hours, cadence_minutes=5):
    """No fit/update. Same minute X across C; metadata and masks preserve unknowns."""
    p = reference["protocol"]
    if window_hours not in p["window_hours"] or cadence_minutes not in p["cadence_candidates"]:
        raise ValueError("Undeclared window/cadence")
    _validate(stream)
    width, lag = int(window_hours*60), int(p["watermark_lag_minutes"])
    shift, maxw = lag+1, int(p["max_window_minutes"])
    arrays, valid_arrays, metas = [], [], []
    for group, original in stream.groupby("group",sort=False):
        frame=original.reset_index(drop=True)
        if group not in reference["groups"]:
            raise ValueError("Unregistered group: no normal reference")
        n=len(frame)
        tick=pd.DatetimeIndex(frame["bin_start"]).as_unit("ns")
        count=frame["count"].to_numpy(float)
        duration=frame["duration_ms"].to_numpy(float)
        observed=np.isfinite(duration)&(count>0)
        known=frame["source_complete"].to_numpy(bool)&np.isfinite(count)&((count==0)|observed)
        mapped=_mapped(frame,reference["registry"])
        supported=mapped==group
        source_available=pd.DatetimeIndex(frame["available_at"]).as_unit("ns").asi8 <= tick.asi8+shift*60_000_000_000
        known_now=_shift((known&source_available).astype(float),shift,0).astype(bool)
        category_now=_shift(supported.astype(float),shift,0).astype(bool)
        common_count=_roll(_shift(known.astype(float),shift,0),maxw,"sum")-_pending(frame,maxw,lag,known)
        available_count=_roll(_shift(known.astype(float),shift,0),width,"sum")-_pending(frame,width,lag,known)
        category_count=_roll(_shift(supported.astype(float),shift,0),maxw,"sum")
        warming=frame["minute_index"].to_numpy()<0
        ready=(~warming)&(common_count==maxw)&(category_count==maxw)
        c=_lookup(frame,reference,group,"count")
        d=_lookup(frame,reference,group,"duration_ms")
        s=lambda a, fill=np.nan: _shift(a,shift,fill)
        cr,dr=s(count),s(duration)
        obs=s(observed.astype(float),0)>0
        cu,cl,du,dl=(s(item) for item in (c["upper"],c["lower"],d["upper"],d["lower"]))
        cm,dm=s(c["median"]),s(d["median"])
        cs,ds=s(c["scale"]),s(d["scale"])
        c95,c05,d95=s(c["q95"]),s(c["q05"]),s(d["q95"])
        cn=(cr-cm)/np.maximum(cs,.05)
        dn=np.where(obs,(dr-dm)/np.maximum(ds,.05),np.nan)
        cx=np.maximum(cr-c95,0)/np.maximum(c95,1)
        low=np.maximum(c05-cr,0)/np.maximum(c05,1)
        dx=np.where(obs,np.maximum(dr-d95,0)/np.maximum(d95,1),np.nan)
        above,below,dabove=cr>c95,cr<c05,obs&(dr>d95)
        obs_w=_roll(obs.astype(float),width,"sum")
        obs_5=_roll(obs.astype(float),min(5,width),"sum")
        tail=np.maximum(cu,cl)
        du_observed=np.where(obs,du,np.nan)
        dl_observed=np.where(obs,dl,np.nan)
        du_ewma=np.divide(_bounded_ewma(np.where(obs,du,0),width),_bounded_ewma(obs.astype(float),width),out=np.zeros(n),where=_bounded_ewma(obs.astype(float),width)>0)
        d_mean=np.nan_to_num(_roll(du_observed,width))
        numeric=[cu,cl,_roll(tail,min(5,width)),_roll(tail,min(15,width)),
            np.minimum(_run(cr==0),width),_bounded_ewma(tail,width),np.nan_to_num(tail-_shift(tail,1)),
            du,dl,np.nan_to_num(_roll(du_observed,min(5,width))),np.nan_to_num(_roll(dl_observed,min(5,width))),
            np.nan_to_num(_roll(du_observed,min(5,width),"max")),np.nan_to_num(du-_shift(du,1)),
            du_ewma,np.nan_to_num(du-d_mean),_roll(tail,width),d_mean,obs.astype(float),
            np.sin(2*np.pi*tick.hour/24),np.cos(2*np.pi*tick.hour/24),(tick.dayofweek>=5).astype(float),
            cx,low,np.nan_to_num(dx),_roll(cx,width,"max"),np.nan_to_num(_roll(dx,width,"max")),
            np.minimum(_run(above),width),np.minimum(_run(below),width),np.minimum(_run(dabove),width),
            _roll((above|below).astype(float),width),_roll(dabove.astype(float),width)]
        c_slope,c_slope_valid=_slope(cn,width)
        d_slope,d_slope_valid=_slope(dn,width)
        half=width//2
        cfront=_shift(_roll(cn,width-half),half)
        dfront=_shift(_roll(dn,width-half),half)
        cback=_roll(cn,half)
        dback=_roll(dn,half)
        short=min(15,width)
        c_ratio=_roll(cr,short)/np.maximum(_roll(cr,width),1)-1
        d_filled=np.where(obs,dr,np.nan)
        d_ratio=np.divide(_roll(d_filled,short),np.maximum(_roll(d_filled,width),1))-1
        x=cr/np.maximum(cm,1)-1
        y=np.where(obs,dr/np.maximum(dm,1)-1,np.nan)
        pair=obs&np.isfinite(x)&np.isfinite(y)
        pair_n=_roll(pair.astype(float),width,"sum")
        px=np.where(pair,x,0); py=np.where(pair,y,0)
        sx,sy=_roll(px,width,"sum"),_roll(py,width,"sum")
        vx=pair_n*_roll(px*px,width,"sum")-sx*sx
        vy=pair_n*_roll(py*py,width,"sum")-sy*sy
        pair_valid=(pair_n>=p["minimum_relation_pairs"])&(vx>1e-10)&(vy>1e-10)
        relation=reference["groups"][group]["relation"]
        corr=np.divide(pair_n*_roll(px*py,width,"sum")-sx*sy,np.sqrt(np.maximum(vx*vy,0)),out=np.full(n,relation["correlation"]),where=pair_valid)
        residual=np.where(pair,np.abs(y-relation["intercept"]-relation["beta"]*x)/relation["scale"],np.nan)
        extra=[c_slope,d_slope,cback-cfront,np.nan_to_num(dback-dfront),c_ratio,np.nan_to_num(d_ratio),
            np.nan_to_num(_roll(cn,width,"std")),np.nan_to_num(_roll(dn,width,"std")),corr,
            np.nan_to_num(_roll(residual,width)),pair_valid.astype(float),pair_n/width]
        matrix=np.column_stack(numeric+extra).astype(np.float32)
        validity=np.ones(matrix.shape,bool)
        for index in (7,8,12,14,23): validity[:,index]=obs
        for index in (9,10,11): validity[:,index]=obs_5>0
        for index in (13,16,25,28,30,38,40): validity[:,index]=obs_w>0
        validity[:,32]=d_slope_valid
        validity[:,34]=(obs_w>=2)&np.isfinite(dfront)&np.isfinite(dback)
        validity[:,36]=np.isfinite(d_ratio)
        validity[:,39]=pair_valid
        validity[:,17]=False; validity[:,41:43]=False
        matrix[~ready]=np.nan
        validity[~ready]=False
        if np.any(ready & ~np.isfinite(matrix).all(axis=1)):
            raise ValueError("Ready rows must have finite train-filled numeric features")
        state=np.full(n,"ready",object)
        state[known_now&(cr==0)]="ready_known_zero"
        state[common_count<maxw]="source_unknown"
        pending=_pending(frame,maxw,lag,known)
        state[pending>0]="observation_late"
        state[category_count<maxw]="category_context_unsupported"
        state[~category_now]="category_unseen"
        state[warming]="warming_up"
        grid=(tick.asi8//60_000_000_000)%cadence_minutes==0
        train_s=_roll(s((frame["status"].to_numpy()=="S").astype(float),0),maxw,"sum")==maxw
        train_mask=ready&grid&train_s&(frame["split"].to_numpy()=="train")
        metadata=pd.DataFrame({"row_id":[f"{sp}|{se}|{group}|{t.isoformat()}" for sp,se,t in zip(frame["split"],frame.get("seed",pd.Series([0]*n)),tick)],
            "split":frame["split"].to_numpy(),"group":group,"scope":group,"decision_tick":tick,
            "endpoint":tick-pd.Timedelta(minutes=lag),"window_end":tick-pd.Timedelta(minutes=lag),
            "window_start":tick-pd.Timedelta(minutes=lag+width),"emitted_at":tick,
            "source_known_now":known_now,"category_supported":category_now,"ready":ready,"readiness":ready,
            "statecode":state,"stateflag":state,"on_decision_grid":grid,"score_mask":ready&grid,"train_mask":train_mask,
            "source_expected_bins":width,"source_available_bins":available_count.astype(int),
            "common_expected_bins":maxw,"common_available_bins":common_count.astype(int),
            "reference_group":group,"category_state":np.where(category_now,"known_or_alias","unseen"),
            "known_zero":known_now&(cr==0),"pair_count":np.maximum(0,pair_n-_pending(frame,width,lag,known&observed)).astype(int),
            "normal_confirmed":frame.get("normal_confirmed",pd.Series([False]*n)).to_numpy(),
            "active":frame.get("active",pd.Series([False]*n)).to_numpy(),"coverage_known":common_count==maxw})
        arrays.append(matrix);valid_arrays.append(validity);metas.append(metadata)
    matrix=np.concatenate(arrays); validity=np.concatenate(valid_arrays)
    return {"X":{"base":matrix[:,:31],"trend":matrix},"metadata":pd.concat(metas,ignore_index=True),
            "feature_names":{"base":list(BASE_NAMES),"trend":list(BASE_NAMES+TREND_NAMES)},
            "feature_validity":{"base":validity[:,:31],"trend":validity},
            "sr_neutral_feature_names":list(NEUTRAL_NAMES)}
