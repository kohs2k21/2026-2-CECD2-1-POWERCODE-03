import numpy as np
import pandas as pd

from .incident_runtime import IncidentTracker
from .test_incidents import ContextDetector,fixture


def test_runtime_open_close_and_no_new_silence_incident():
    f,_=fixture();f['anomaly']=[True,False]
    tracker=IncidentTracker(ttl_seconds=300)
    first=tracker.update(f.iloc[:1],ContextDetector())
    assert first.incident_action.iloc[0]=='open'
    assert len(tracker.snapshot('2026-01-01 09:02'))==1
    second=tracker.update(f.iloc[1:],ContextDetector())
    assert second.incident_action.iloc[0]=='close'
    assert tracker.snapshot('2026-01-01 09:10').empty
    clean=IncidentTracker()
    clean.update(f.assign(anomaly=False),ContextDetector())
    assert clean.snapshot('2026-01-01 10:00').empty


def test_runtime_silence_expires_existing_alarm_only():
    f,_=fixture();f['anomaly']=True
    tracker=IncidentTracker(ttl_seconds=60)
    tracker.update(f.iloc[:1],ContextDetector())
    result=tracker.snapshot('2026-01-01 09:02')
    assert result.status.iloc[0]=='expired'
    assert tracker.snapshot('2026-01-01 09:03').empty
