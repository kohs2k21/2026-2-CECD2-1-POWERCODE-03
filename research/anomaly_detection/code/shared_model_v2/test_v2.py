import pickle
import unittest
import numpy as np
import shared_model_target.test_target as legacy
from shared_model_v2.features import augment,MEMORY
from shared_model_v2.models import Detector,Setting
from adapter_feature_ablation.features import ADAPTER


def example():
    f=legacy.FeatureTests().current_example()
    f['current_source_delay']=.1;f['current_source_upper']=.1;f['current_prefix_delay']=0.
    f['adapter_count_p95_z']=0.;f['current_arrival_excess']=0.;f['current_prefix_burst']=0.
    f['arrival_logratio_5m']=0.;f['arrival_logratio_15m']=0.;f['own_arrival_change']=0.
    f['gap_expected_prefix']=0.;f['gap_expected_recent']=0.
    return f


class MemoryTests(unittest.TestCase):
    def test_projection_and_smoothed_reference_are_compatible(self):
        from shared_model_target.current_features import CURRENT
        f=example();rng=np.random.default_rng(44)
        for c in(*CURRENT,*ADAPTER):
            if c not in f:f[c]=rng.normal(0,1,len(f))
        model=Detector(Setting('lof',memory=False,interactions=True,projection=6,
            neighbors=5,reference_cap=64,reference_noise=.01)).fit(f)
        self.assertTrue(np.isfinite(model.score(f)).all())
    def test_score_filter_is_causal_and_resets_after_recovery(self):
        from shared_model_v2.score_filter import FilteredDetector
        f=example();filter=FilteredDetector(None,.5,3,8,True)
        raw=np.ones(len(f));raw[20]=10.;raw[21]=1.
        result=filter.filter(f,raw)
        self.assertLess(result[20],np.log1p(10))
        self.assertEqual(result[21],np.log1p(1))
        prefix=filter.filter(f.iloc[:40],raw[:40]);raw[40:]=10000
        np.testing.assert_array_equal(prefix,filter.filter(f,raw)[:40])
    def test_score_filter_keeps_paired_cohorts_separate(self):
        from shared_model_v2.score_filter import FilteredDetector
        import pandas as pd
        f=example();filter=FilteredDetector(None,.5,3,8,True)
        a=np.linspace(1,3,len(f));b=np.linspace(3,1,len(f))
        together=filter.filter(pd.concat([f,f],ignore_index=True),np.r_[a,b])
        np.testing.assert_array_equal(together[:len(f)],filter.filter(f,a))
        np.testing.assert_array_equal(together[len(f):],filter.filter(f,b))
    def test_no_temporal_history_is_invented_for_ADBench(self):
        from shared_model_v2.score_filter import FilteredDetector
        f=example().drop(columns='window_start');raw=np.arange(len(f))+1.
        np.testing.assert_array_equal(FilteredDetector(None,.5).filter(f,raw),np.log1p(raw))
    def test_future_does_not_change_past(self):
        f=example();a=augment(f.iloc[:40])
        f.loc[40:,'current_source_delay']=100
        np.testing.assert_array_equal(a[list(MEMORY)],augment(f).iloc[:40][list(MEMORY)])
    def test_observed_recovery_resets_memory_immediately(self):
        f=example();f.loc[20,'current_source_delay']=4.;f.loc[21:23,'latency_assessed']=False
        f.loc[24,'current_source_delay']=0.
        r=augment(f);self.assertGreater(r.last_source_delay_3m.iloc[22],0)
        self.assertEqual(r.last_source_delay_3m.iloc[24],0)
        self.assertEqual(r.last_source_delay_8m.iloc[24],0)
    def test_expiry_is_elapsed_minutes_not_observation_count(self):
        f=example();f.loc[20,'current_source_delay']=4.;f.loc[21:35,'latency_assessed']=False
        r=augment(f);self.assertEqual(r.last_source_delay_3m.iloc[24],0)
        self.assertEqual(r.last_source_delay_8m.iloc[29],0)
        self.assertAlmostEqual(r.last_delay_age.iloc[25],np.log1p(5))
    def test_no_history_and_labels_cannot_influence_memory(self):
        f=example();f.loc[:5,'latency_assessed']=False;a=augment(f)
        self.assertEqual(a.last_source_delay_8m.iloc[0],0)
        f['window_is_anomaly']=True;f['scenario_types']='fault'
        np.testing.assert_array_equal(a[list(MEMORY)],augment(f)[list(MEMORY)])
    def test_normal_reference_is_not_refit(self):
        from shared_model_target.calibration import NormalReference
        f=example();f['case_id']=[str(i//5)for i in range(len(f))]
        ref=NormalReference.fit(f,np.linspace(0,1,len(f)),'availability',1.,'robust')
        before=pickle.dumps(ref);ref.apply(f,np.ones(len(f))*100)
        self.assertEqual(before,pickle.dumps(ref))
    def test_interactions_use_only_features(self):
        f=example();f['current_delay_trend']=.5;f['adapter_count_p95_upper_tail']=1.
        model=Detector(Setting('kmeans',interactions=True))
        a=model.derive(f);f['window_is_anomaly']=True
        b=model.derive(f)
        cols=[c for c in a if c.startswith('coherent_')]
        self.assertEqual(len(cols),6);np.testing.assert_array_equal(a[cols],b[cols])


if __name__=='__main__':unittest.main()
