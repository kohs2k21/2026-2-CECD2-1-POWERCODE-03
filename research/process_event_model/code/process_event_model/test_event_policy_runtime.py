from dataclasses import asdict

import numpy as np
import pandas as pd
import pytest

from .event_policy_runtime import EventPolicyTracker
from .minute_refinement import EventPolicy,MinuteReplay
from .test_minute_refinement import replay_data


def test_live_tracker_matches_causal_offline_clock_queries():
    f,w=replay_data();w=w.iloc[:5].copy()
    p=EventPolicy(traffic_gate=99)
    scores=np.array([.1,2.,.1])
    expected=MinuteReplay(f,w).project(scores,p)
    live=EventPolicyTracker(**asdict(p))
    seen=np.zeros(len(f),bool);previous_end=-np.inf;actual=[]
    for end in w._end:
        take=(f._time.to_numpy()<end)&~seen
        hit=False
        if take.any():
            batch=f.loc[take].copy()
            batch['case_id']='A';batch['adapterType']='X';batch['processId']='P'
            batch['availableTime']=pd.to_datetime(batch._time,unit='s',utc=True)
            batch['anomaly']=scores[take]>1
            out=live.update(batch)
            hit=bool(out.confirmed_anomaly.any())
        active=not live.snapshot(pd.to_datetime(end,unit='s',utc=True)).empty
        actual.append(hit or active);seen|=take;previous_end=end
    np.testing.assert_array_equal(actual,expected)


def test_snapshot_cannot_open_input_stop_or_read_future_state():
    tracker=EventPolicyTracker()
    assert tracker.snapshot('2026-01-01T00:00:00Z').empty
    f,_=replay_data();f=f.iloc[:1].copy()
    f['case_id']='A';f['adapterType']='X';f['processId']='P'
    f['availableTime']=pd.to_datetime(f._time,unit='s',utc=True);f['anomaly']=False
    tracker.update(f)
    assert tracker.snapshot('1970-01-01T00:10:00Z').empty
    with pytest.raises(ValueError,match='historical'):
        tracker.snapshot('1970-01-01T00:00:01Z')


def test_traffic_confirmation_is_past_only_and_latency_is_immediate():
    f=pd.DataFrame(dict(_uid=['A']*6,_time=[10.,20.,25.,75.,80.,130.],
        history_support=[np.log1p(5)]*6,local_upper=[0.,0.,0.,0.,0.,5.],
        predictive_burst_1m=[3.]*6,predictive_burst_3m=[2.]*6))
    w=pd.DataFrame(dict(_uid=['A']*4,_end=[19.,26.,90.,140.]))
    p=EventPolicy(traffic_confirmations=3,traffic_confirm_seconds=20.,traffic_ttl=0.,delay_ttl=0.)
    expected=MinuteReplay(f,w).project(np.full(len(f),2.),p)
    np.testing.assert_array_equal(expected,[False,True,False,True])
    tracker=EventPolicyTracker(**asdict(p));confirmed=[];seen=np.zeros(len(f),bool);actual=[]
    for end in w._end:
        take=(f._time<end).to_numpy()&~seen
        batch=f.loc[take].copy();batch['case_id']='A';batch['adapterType']='X';batch['processId']='P'
        batch['availableTime']=pd.to_datetime(batch._time,unit='s',utc=True);batch['anomaly']=True
        out=tracker.update(batch);confirmed.extend(out.confirmed_anomaly.tolist())
        actual.append(bool(out.confirmed_anomaly.any()));seen|=take
    np.testing.assert_array_equal(actual,expected)
    assert confirmed==[False,False,True,False,False,True]


def test_rate_aware_expiry_runtime_matches_replay():
    f,w=replay_data();f['score']=[2.,4.,.1];f['past_log_count_1m']=np.log1p([9.,9.,9.])
    f['predictive_burst_1m']=3.;f['predictive_burst_3m']=2.;f['local_upper']=0.
    w=w.iloc[:5].copy()
    p=EventPolicy(traffic_ttl=180.,traffic_gap_events=2.)
    expected=MinuteReplay(f,w).project(f.score,p)
    tracker=EventPolicyTracker(**asdict(p));seen=np.zeros(len(f),bool);actual=[]
    for end in w._end:
        take=(f._time<end).to_numpy()&~seen
        batch=f.loc[take].copy();batch['case_id']='A';batch['adapterType']='X';batch['processId']='P'
        batch['availableTime']=pd.to_datetime(batch._time,unit='s',utc=True);batch['anomaly']=batch.score>1.
        out=tracker.update(batch)
        actual.append(bool(out.confirmed_anomaly.any()) or not tracker.snapshot(pd.to_datetime(end,unit='s',utc=True)).empty)
        seen|=take
    np.testing.assert_array_equal(actual,expected)


def test_calibrated_traffic_family_not_old_proxy_can_open_existing_state():
    f,w=replay_data();w=w.iloc[:5].copy();f.local_upper=0.
    scores=np.array([.1,2.,.1]);family=np.array([3,3,0])
    p=EventPolicy(traffic_gate=99.,delay_ttl=0.,traffic_ttl=300.,
        traffic_model_family=True,traffic_model_family_clear=True)
    expected=MinuteReplay(f,w).project(scores,p,families=family)
    np.testing.assert_array_equal(expected,[False,True,True,True,False])
    tracker=EventPolicyTracker(**asdict(p));seen=np.zeros(len(f),bool);actual=[]
    for end in w._end:
        take=(f._time<end).to_numpy()&~seen
        batch=f.loc[take].copy();batch['case_id']='A';batch['adapterType']='X';batch['processId']='P'
        batch['availableTime']=pd.to_datetime(batch._time,unit='s',utc=True)
        batch['score']=scores[take];batch['anomaly']=scores[take]>1.
        batch['calibrated_evidence_family']=family[take]
        out=tracker.update(batch)
        actual.append(bool(out.confirmed_anomaly.any()) or not tracker.snapshot(pd.to_datetime(end,unit='s',utc=True)).empty)
        seen|=take
    np.testing.assert_array_equal(actual,expected)
