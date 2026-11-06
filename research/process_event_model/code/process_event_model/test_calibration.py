import joblib
import numpy as np
import pytest

from .calibration import CalibratedEventDetector
from .features import normalize
from .inference import EventMonitor
from .models import Detector,Settings
from .row_context import ExactReference,RowContext
from .test_features import rows


@pytest.mark.parametrize('algorithm',['if','kmeans','lof'])
def test_calibrated_event_prediction_preserves_identity(tmp_path,algorithm):
    train=rows(80)
    ref=ExactReference.fit(normalize(train))
    f=RowContext(ref).transform(train)
    base=Detector(Settings(algorithm,'row_compact',clusters=4,neighbors=5)).fit(f)
    # This is a numerical round-trip test, not claimed evaluation performance.
    detector=CalibratedEventDetector(base,.5).fit_calibration(f)
    path=tmp_path/'fused.joblib';joblib.dump(detector,path)
    restored=joblib.load(path)
    np.testing.assert_array_equal(detector.predict(f)[0],restored.predict(f)[0])
    a=EventMonitor(ref,restored).predict(train)
    assert len(a)==len(train)
    assert a.transactionId.tolist()==train.transactionId.tolist()
    assert np.isfinite(a.score).all()
    changed=f.assign(label=1,scenario_types='input_stop',transactionId='SECRET')
    np.testing.assert_array_equal(detector.predict(f)[0],detector.predict(changed)[0])
