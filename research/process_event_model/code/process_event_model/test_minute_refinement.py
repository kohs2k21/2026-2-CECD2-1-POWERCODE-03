import numpy as np
import pandas as pd

from .minute_refinement import MinuteReplay,EventPolicy


def replay_data():
    f=pd.DataFrame({'_uid':['A']*3,'_time':[10.,70.,250.],
                    'history_support':[np.log1p(5)]*3,'local_upper':[0.,5.,0.],
                    'predictive_burst_1m':[0.]*3,'predictive_burst_3m':[0.]*3})
    w=pd.DataFrame({'_uid':['A']*5+['B']*5,'_end':[60.,120.,180.,240.,300.]*2})
    return f,w


def test_existing_incident_persists_but_absence_cannot_open_one():
    f,w=replay_data();replay=MinuteReplay(f,w)
    p=replay.project(np.array([.1,2.,.1]),EventPolicy(traffic_gate=99))
    np.testing.assert_array_equal(p[:5],[False,True,True,True,False])
    assert not p[5:].any()
    assert not replay.project(np.zeros(3),EventPolicy()).any()


def test_future_delivery_cannot_change_earlier_window_decisions():
    f,w=replay_data()
    full=MinuteReplay(f,w).project(np.array([.1,2.,.1]),EventPolicy(traffic_gate=99))
    before=MinuteReplay(f.iloc[:2],w).project(np.array([.1,2.]),EventPolicy(traffic_gate=99))
    np.testing.assert_array_equal(full[:4],before[:4])


def test_simultaneous_onset_wins_over_clear_order():
    f,w=replay_data()
    x=f.iloc[[1]].copy();x.local_upper=0.
    f=pd.concat([f,x],ignore_index=True)
    p=MinuteReplay(f,w).project(np.array([.1,2.,.1,.1]),EventPolicy(traffic_gate=99))
    assert p[1] and p[2]
