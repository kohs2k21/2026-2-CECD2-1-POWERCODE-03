import numpy as np
import pandas as pd

from .incidents import project_to_windows
from .features import normalize
from .test_features import rows


class ContextDetector:
    def state(self,frame):
        return np.ones(len(frame),int),np.zeros(len(frame))


class PointDetector:
    def state(self,frame):
        return np.zeros(len(frame),int),np.zeros(len(frame))


def fixture():
    f=normalize(rows(2))
    f['history_support']=np.log1p(5)
    f.loc[1,'availableTime']+=pd.Timedelta(minutes=3)
    times=pd.date_range('2026-01-01 09:00',periods=8,freq='min')
    windows=pd.DataFrame(dict(case_id='runtime',window_start=times.astype(str),
                             window_end=(times+pd.Timedelta(minutes=1)).astype(str)))
    return f,windows


def test_hold_is_causal_and_clears_on_normal_execution():
    f,w=fixture()
    p=project_to_windows(f,[True,False],ContextDetector(),w)
    assert p[:4].all()
    assert not p[4:].any()
    # Removing/changing a future recovery cannot alter earlier snapshots.
    a=project_to_windows(f.iloc[:1],[True],ContextDetector(),w)
    np.testing.assert_array_equal(p[:4],a[:4])


def test_point_alert_does_not_become_a_context_incident():
    f,w=fixture()
    p=project_to_windows(f,[True,False],PointDetector(),w)
    assert p[0]
    assert not p[1:].any()


def test_silence_after_normal_cannot_open_an_incident():
    f,w=fixture()
    assert not project_to_windows(f,[False,False],ContextDetector(),w).any()


def test_finite_expiry_without_new_rows():
    f,w=fixture()
    p=project_to_windows(f.iloc[:1],[True],ContextDetector(),w,ttl_seconds=120)
    assert p[:2].all()
    assert not p[2:].any()
