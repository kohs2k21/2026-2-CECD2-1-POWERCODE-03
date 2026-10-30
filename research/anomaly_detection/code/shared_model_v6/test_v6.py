import pickle,unittest
import numpy as np
import pandas as pd
from shared_model_v6.features import Evidence,RAW
from shared_model_v4.rank_context import RANK
from shared_model_v6.models import Setting,Detector
from shared_model_v6.calibration import SignalReference
from shared_model_v6.search import curves
from shared_model_improvement.run import _stats

def history():return {(w,h):[0,1,1,2,2,3,4]for w in (False,True)for h in range(24)}
def frame():
    rng=np.random.default_rng(4);n=180
    f=pd.DataFrame({'case_id':np.repeat([str(k)for k in range(6)],30),'window_start':np.tile(pd.date_range('2026-10-01',periods=30,freq='min').astype(str),6),
        'adapterType':['ONE']*n,'phase':['warm']*n,'traffic_assessed':[True]*n,'latency_assessed':[True]*n,
        'native_execution_arrivals':rng.integers(0,4,n),'log_max_duration_1m':rng.normal(5,.1,n)})
    for c in RAW:
        if c not in RANK:f[c]=np.maximum(rng.normal(0,1,n),0.)
    return f

class FeatureTests(unittest.TestCase):
    def test_labels_are_not_inputs(self):
        f=frame();e=Evidence(history(),tail=True).fit(f);before=e.transform(f,'radial_pair')
        f['window_is_anomaly']=True;f['scenario_types']='changed';f['generation_mode']='malicious'
        np.testing.assert_array_equal(before,e.transform(f,'radial_pair'))
    def test_future_cannot_change_past(self):
        f=frame();e=Evidence(history(),tail=True).fit(f);before=e.transform(f.iloc[:15].copy(),'peaks')
        f.loc[15:,'log_max_duration_1m']=1000;f.loc[15:,'native_execution_arrivals']=1000
        np.testing.assert_array_equal(before,e.transform(f,'peaks')[:15])
    def test_all_views_dimensions_and_finite(self):
        f=frame();e=Evidence(history()).fit(f)
        for v in ('peaks','compact','raw','radial','radial_pair','peaks_joint','compact_joint'):
            x=e.transform(f,v);self.assertEqual(x.shape,(len(f),len(e.names(v))));self.assertTrue(np.isfinite(x).all())
    def test_tail_scale_monotonic(self):
        f=frame();e=Evidence(history(),tail=True).fit(f);before=e.transform(f,'raw');f['current_source_upper']+=100
        after=e.transform(f,'raw');self.assertTrue((after[:,RAW.index('current_source_upper')]>=before[:,RAW.index('current_source_upper')]).all())
    def test_joint_is_continuous_feature_not_label_decision(self):
        f=frame();e=Evidence(history()).fit(f);x=e.transform(f,'peaks');r=e.transform(f,'radial')
        np.testing.assert_array_equal(r[:,0],x.max(axis=1))
    def test_estimators_pickle_immutable(self):
        f=frame()
        for a in ('if','kmeans','lof'):
            m=Detector(Setting(a,view='peaks_joint',tail=True,trees=10,samples=30,clusters=2,neighbors=5,cap=80),history()).fit(f)
            before=pickle.dumps(m)
            fitted_arrays={k:v.copy()for k,v in vars(m.estimator).items()if isinstance(v,np.ndarray)}
            s=m.score(f);np.testing.assert_array_equal(s,pickle.loads(before).score(f));np.testing.assert_array_equal(s,m.score(f))
            # sklearn's low-dimensional LOF uses a KDTree whose query telemetry
            # counters change. Verify fitted arrays and repeat scores instead of
            # mistaking those counters for a change to the learned model.
            for key,value in fitted_arrays.items():np.testing.assert_array_equal(value,getattr(m.estimator,key))
            if a!='lof':self.assertEqual(before,pickle.dumps(m))
            self.assertEqual(m.record()['estimator_count'],1)
    def test_signal_calibration_ignores_labels_and_unknown_fallback(self):
        f=frame();e=Evidence(history()).fit(f);s=np.linspace(0,1,len(f));r=SignalReference.fit(f,s,e);before=pickle.dumps(r)
        x=r.apply(f,s);f['scenario_types']='changed';f['window_is_anomaly']=True
        np.testing.assert_array_equal(x,r.apply(f,s));self.assertEqual(before,pickle.dumps(r));self.assertTrue(np.isfinite(x).all())
    def test_fast_curves_equal_independent_scalar_stats_including_ties(self):
        y=np.array([0,1,0,1,0,1],bool);s=np.array([0,1,1,2,2,2.]);ts=[-1,0,1,2,3]
        for th,r in zip(ts,curves(y,s,ts)):
            other=_stats(y,s,th)
            for k in r:self.assertAlmostEqual(r[k],other[k])

if __name__=='__main__':unittest.main()
