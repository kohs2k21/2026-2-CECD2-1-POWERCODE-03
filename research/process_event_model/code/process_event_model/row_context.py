"""Fine-grained normal references and row-preserving causal context, revision 2."""
from collections import defaultdict, deque
from dataclasses import dataclass

import numpy as np

from .features import normalize


ROW_FEATURES = ("row_log_duration", "row_log_count", "row_residual", "row_upper_tail",
    "local_upper", "local_shift", "local_log_ratio", "local_slope", "local_cusum",
    "local_persistence", "traffic_lift_1m", "traffic_lift_3m", "traffic_lift_5m",
    "traffic_density", "gap_lift", "past_log_count_1m", "past_log_count_5m",
    "history_support")


def reference_table(values):
    values = np.sort(np.asarray(values, float))
    n = len(values)
    unique, first = np.unique(values, return_index=True)
    if len(unique) > 512:
        probs = np.unique(np.r_[np.linspace(0, .95, 96), .97,.98,.99,.995,.997,.999,.9995,1.])
        knots = np.unique(np.quantile(values, probs))
        ranks = np.searchsorted(values, knots, side="left")
    else:
        knots, ranks = unique, first
    q25, median, q75 = np.quantile(values, [.25,.5,.75])
    return knots, ranks/n, float(median), float(max(q75-q25,.10)), n


@dataclass
class ExactReference:
    tables: list
    pooled: tuple
    min_support: int

    @classmethod
    def fit(cls, actual_normal, min_support=30):
        f = actual_normal.copy()
        f["_duration"] = np.log1p(f.duration_ms.to_numpy(float))
        levels = (("adapterType","processId","totalCount"), ("adapterType","totalCount"),
                  ("adapterType","processId"), ("adapterType",))
        tables = []
        for cols in levels:
            table = {}
            for key, g in f.groupby(list(cols), observed=True, sort=False):
                if len(g) >= min_support:
                    table[key if isinstance(key,tuple) else (key,)] = reference_table(g._duration)
            tables.append((cols,table))
        return cls(tables, reference_table(f._duration), min_support)

    def transform(self, f):
        result = np.zeros((len(f),4))
        logs = np.log1p(f.duration_ms.to_numpy(float))
        for (a,p,c), ids in f.groupby(["adapterType","processId","totalCount"], observed=True, sort=False).indices.items():
            lookup = dict(adapterType=a,processId=p,totalCount=c)
            sample, level = self.pooled, 4
            for j,(cols,table) in enumerate(self.tables):
                found = table.get(tuple(lookup[k] for k in cols))
                if found is not None:
                    sample, level = found, j
                    break
            knots,cdf,median,scale,n = sample
            vals = logs[ids]
            probability = np.interp(vals,knots,cdf,left=0.,right=1.)
            upper = -np.log(np.maximum(1-probability,.5/(n+1)))
            result[ids] = np.column_stack(((vals-median)/scale, upper,
                                           np.full(len(ids),level),np.full(len(ids),n)))
        return result


class RowContext:
    """Each original row gets historical statistics; no synthetic minute rows.

    Prefix rate = actual observations in first six observed minutes / actual
    elapsed exposure. Latency anchor = first five available executions. Neither
    is certified healthy, and neither uses future or anomaly labels.
    """
    def __init__(self, reference):
        self.reference = reference
        self.states = {}

    def transform(self, records):
        f = normalize(records)
        reference = self.reference.transform(f)
        logs = np.log1p(f.duration_ms.to_numpy(float))
        times = f.availableTime.astype("datetime64[ns, UTC]").astype("int64").to_numpy()/1e9
        out = np.zeros((len(f),len(ROW_FEATURES)))
        out[:,:4] = np.column_stack((logs,np.log1p(f.totalCount),reference[:,:2]))
        groups = defaultdict(list)
        identities=f.transactionId.astype(str).to_numpy()
        retries=f.retryCount.to_numpy() if 'retryCount' in f else np.zeros(len(f))
        keys = zip(f.case_id.astype(str),f.adapterType.astype(str),f.processId.astype(str))
        for i,key in enumerate(keys): groups[key].append(i)
        for key,indices in groups.items():
            ids = sorted(indices,key=lambda i:(times[i],identities[i],retries[i]))
            state = self.states.setdefault(key,dict(anchor=[],history=deque(maxlen=20),arrivals=deque(),
                gaps=deque(maxlen=20),prefix_count=0,first=None,last=None,cusum=0.))
            start = 0
            while start<len(ids):
                end=start+1;t=times[ids[start]]
                while end<len(ids) and times[ids[end]]==t:end+=1
                if state["last"] is not None and t<=state["last"]:
                    raise ValueError("Out-of-order/split availability batch; preserve state and replay in order")
                if state["first"] is None:state["first"]=t
                age=t-state["first"]
                while state["arrivals"] and state["arrivals"][0]<t-300:state["arrivals"].popleft()
                past=np.asarray(state["arrivals"],float)
                counts=np.array([len(past)-np.searchsorted(past,t-w,side="left") for w in (60,180,300)],float)
                gaps=np.asarray(state["gaps"],float)
                hist=np.asarray(state["history"],float)
                anchor=np.asarray(state["anchor"],float)
                enough=len(anchor)>=3
                center=float(np.median(anchor)) if len(anchor) else 0.
                noise=max(float(np.median(np.abs(anchor-center))*1.4826),.03) if len(anchor) else .03
                upper=float(anchor.max()) if len(anchor) else 0.
                gap=t-state["last"] if state["last"] is not None else 0.
                # Prefix event counter includes every actual row, not only stored
                # duration anchors. Exposure never extends beyond current time.
                rate=state["prefix_count"]/max(min(age,360.),1.) if age>0 else 0.
                support=len(hist)
                for i in ids[start:end]:
                    shift=max((logs[i]-center)/noise,0.) if enough else 0.
                    peak=max((logs[i]-upper)/noise,0.) if enough else 0.
                    logratio=max(logs[i]-center,0.) if enough else 0.
                    cusum=max(0.,.85*state["cusum"]+min(shift,20.)-.5) if enough else 0.
                    # An observed recovery resets accumulation, not the truth.
                    if shift<.5:cusum=0.
                    slope=persist=0.
                    if support>=3 and shift>0:
                        h=hist[-8:]
                        x=np.r_[h[:,0],t];y=np.r_[h[:,1],logs[i]]
                        x=(x-x[-1])/60;x-=x.mean()
                        slope=max(float(np.dot(x,y-y.mean())/max(np.dot(x,x),1.))*5/noise,0.)
                        persist=float(np.mean(h[:,1]>upper+.01))*min(shift,10.)
                    lift=np.zeros(3)
                    density=gap_lift=0.
                    if support>=5 and age>=60 and rate>0:
                        exposures=np.minimum(age,np.array([60,180,300]))
                        lift=np.maximum(np.log((counts+1.5)/(rate*exposures+.5)),0.)
                        expected_gap=1/rate
                        density=max(np.log((expected_gap+.05)/(gap+.05)),0.)
                        gap_lift=max(np.log((gap+.05)/(expected_gap+.05)),0.)
                    out[i,4:]=(peak,shift,logratio,slope,cusum,persist,*lift,density,gap_lift,
                               np.log1p(counts[0]),np.log1p(counts[2]),np.log1p(support))
                for i in ids[start:end]:
                    state["history"].append((t,logs[i]))
                    state["arrivals"].append(t)
                    if len(state["anchor"])<5:state["anchor"].append(logs[i])
                    if age<=360:state["prefix_count"]+=1
                if state["last"] is not None and gap>0:state["gaps"].append(gap)
                state["cusum"]=max(out[i,ROW_FEATURES.index("local_cusum")] for i in ids[start:end])
                state["last"]=t
                start=end
        for j,c in enumerate(ROW_FEATURES):f[c]=np.nan_to_num(np.clip(out[:,j],-100,100),nan=0.)
        f["reference_level"],f["reference_support"]=reference[:,2],reference[:,3]
        return f
