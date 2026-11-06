import pandas as pd
import pytest
import numpy as np

from .test_features import rows
from .train import fit_normal
from .inference import EventMonitor


def test_new_original_training_needs_no_benchmark():
    train=rows(80)
    cal=rows(80).assign(transactionId=[f'C{i}' for i in range(80)])
    cal[['startTime','endTime']]+=pd.Timedelta(days=1)
    p=fit_normal(train,cal)
    assert not p['published_metrics_inherited']
    for model in p['models'].values():
        result=EventMonitor(p['reference'],model).predict(cal)
        assert len(result)==len(cal)
        assert np.isfinite(result.score).all()


def test_retrain_refuses_temporal_overlap():
    with pytest.raises(ValueError,match='later observed dates'):
        fit_normal(rows(40),rows(40))


def test_predictive_retrain_is_independent_and_portable(tmp_path):
    import joblib
    from .inference import load
    train=rows(80)
    cal=rows(80).assign(transactionId=[f'C{i}' for i in range(80)])
    cal[['startTime','endTime']]+=pd.Timedelta(days=1)
    p=fit_normal(train,cal,predictive_traffic=True)
    assert p['processor']=='predictive_row_context'
    path=tmp_path/'predictive.joblib';joblib.dump(p,path)
    for a,model in p['models'].items():
        assert model.base.estimator is model.parent.base.estimator
        assert type(model).__module__!='__main__'
        result=load(path,a).predict(cal)
        assert len(result)==len(cal)
        assert np.isfinite(result.score).all()
