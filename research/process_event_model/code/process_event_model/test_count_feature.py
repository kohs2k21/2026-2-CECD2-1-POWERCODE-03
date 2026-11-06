import numpy as np
import pandas as pd

from .features import normalize
from .native_history import NativeHistory
from .test_features import rows


def test_running_minute_count_is_atomic_and_preserves_future_causality():
    raw=rows(6)
    raw['availableTime']=pd.to_datetime(['2026-01-01 09:00:10','2026-01-01 09:00:20',
        '2026-01-01 09:00:20','2026-01-01 09:00:50','2026-01-01 09:01:10','2026-01-01 09:01:30'])
    raw['endTime']=raw.availableTime-pd.Timedelta(milliseconds=10)
    raw['startTime']=raw.endTime-pd.Timedelta(milliseconds=100)
    f=normalize(raw)
    out=NativeHistory().transform(f)
    np.testing.assert_array_equal(out.native_observed_minute_count,[1,3,3,4,1,2])
    prefix=NativeHistory().transform(f.iloc[:4])
    np.testing.assert_array_equal(prefix.native_observed_minute_count,out.native_observed_minute_count.iloc[:4])
