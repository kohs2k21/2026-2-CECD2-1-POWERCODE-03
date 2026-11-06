import numpy as np
import pandas as pd

from .features import normalize
from .native_history import NativeHistory,MICRO_FEATURES
from .test_features import rows


def micro_records():
    baseline=rows(15)
    origin=baseline.startTime.iloc[0]
    burst=rows(20).assign(transactionId=[f'B{i}' for i in range(20)])
    burst['startTime']=origin+pd.Timedelta(minutes=15)+pd.to_timedelta(np.arange(20),unit='s')
    burst['endTime']=burst.startTime+pd.Timedelta(milliseconds=100)
    recovery=rows(1).assign(transactionId='R')
    recovery['startTime']=origin+pd.Timedelta(minutes=20)
    recovery['endTime']=recovery.startTime+pd.Timedelta(milliseconds=100)
    return normalize(pd.concat([baseline,burst,recovery],ignore_index=True))


def test_short_observed_gaps_accumulate_without_future_or_clock_rows():
    f=micro_records();out=NativeHistory().transform(f)
    assert len(out)==len(f)
    assert out.micro_cusum.iloc[34]>out.micro_cusum.iloc[14]+5
    assert out.micro_density.iloc[34]>0
    assert out.micro_density.iloc[35]==0
    before=NativeHistory().transform(f.iloc[:25])
    np.testing.assert_allclose(before[list(MICRO_FEATURES)],out.iloc[:25][list(MICRO_FEATURES)])


def test_micro_streaming_matches_batch():
    f=micro_records();expected=NativeHistory().transform(f);processor=NativeHistory()
    actual=pd.concat([processor.transform(f.iloc[:20]),processor.transform(f.iloc[20:])])
    np.testing.assert_allclose(actual[list(MICRO_FEATURES)],expected[list(MICRO_FEATURES)])
