import numpy as np
import pandas as pd

from .hysteresis import Policy, State, transition, project
from .incidents import project_to_windows
from .test_incidents import ContextDetector, fixture


def test_requires_actual_normal_executions_not_zero_minutes():
    policy = Policy(normal_confirmations=3)
    s = State()
    transition(s, 0, True, True, 1, policy)
    transition(s, 60, False, False, 1, policy)
    assert s.active and s.normals == 1
    transition(s, 120, False, False, 1, policy)
    assert s.active and s.normals == 2
    transition(s, 180, False, False, 1, policy)
    assert not s.active


def test_expiry_and_silence_cannot_open():
    s = State()
    p = Policy(normal_confirmations=3, ttl_seconds=100)
    transition(s, 0, False, False, 1, p)
    assert not s.active
    transition(s, 1, True, True, 1, p)
    transition(s, 200, False, False, 1, p)
    assert not s.active


def test_legacy_policy_equivalence_and_causal_prefix():
    f, w = fixture()
    f['local_upper'] = 5.
    f['traffic_lift_1m'] = 0.
    f['traffic_lift_3m'] = 0.
    p = Policy(context_min=0., fast_traffic=False)
    a = project(f, [True, False], ContextDetector(), w, p)
    b = project_to_windows(f, [True, False], ContextDetector(), w)
    np.testing.assert_array_equal(a, b)
    early = project(f.iloc[:1], [True], ContextDetector(), w, p)
    np.testing.assert_array_equal(a[:4], early[:4])


def test_future_recovery_never_changes_earlier_state():
    f, w = fixture()
    f['local_upper'] = [10., 0.]
    f['traffic_lift_1m'] = 0.
    f['traffic_lift_3m'] = 0.
    policy = Policy(normal_confirmations=2)
    a = project(f, [True, False], ContextDetector(), w, policy)
    b = project(f.iloc[:1], [True], ContextDetector(), w, policy)
    np.testing.assert_array_equal(a[:4], b[:4])


def test_clock_queries_do_not_create_anomalous_executions():
    f, w = fixture()
    f['local_upper'] = 0.
    f['traffic_lift_1m'] = 0.
    f['traffic_lift_3m'] = 0.
    assert not project(f, [False, False], ContextDetector(), w, Policy(normal_confirmations=3)).any()
    assert len(f) == 2
