"""Normal-score calibration conditioned on observable input availability."""
import numpy as np
import pandas as pd
from shared_model_target.calibration import NormalReference

def without_native(f):
    f=f.copy()
    for c in('processId','duration_p95_ms','duration_p50_ms','log_mean_count_5m'):
        if c in f:f[c]=np.nan if c!='processId'else None
    f['case_id']=f.case_id.astype(str)+'|native_unavailable'
    return f

def both_views(f):return pd.concat([f,without_native(f)],ignore_index=True)

class NativeReference(NormalReference):
    @staticmethod
    def keys(f,mode):
        if mode not in('native','native_availability','native_full'):return NormalReference.keys(f,mode)
        native=f.processId.notna().to_numpy(int)if 'processId'in f else np.zeros(len(f),int)
        if mode=='native':return native.astype(str)
        gate=f.traffic_assessed.to_numpy(int)+2*f.latency_assessed.to_numpy(int)
        if mode=='native_full':gate+=4*f.temporal_assessed.to_numpy(int)+8*f.cadence_ready.to_numpy(int)
        return np.array([str(n)+'|'+str(g)for n,g in zip(native,gate)])
