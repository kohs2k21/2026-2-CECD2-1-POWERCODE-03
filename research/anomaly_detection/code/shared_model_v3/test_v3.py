import unittest
import pickle
import numpy as np
import pandas as pd
from shared_model_v3.features import augment,SEQUENCE
from shared_model_v3.models import Detector,Setting

def example():
    n=40
    return pd.DataFrame({'case_id':['one']*n,'window_start':pd.date_range('2026-10-01',periods=n,freq='min').astype(str),
        'native_execution_arrivals':np.ones(n)*3,'duration_p50_ms':np.ones(n)*100,
        'residual_mean_1m':np.zeros(n),'log_mean_count_5m':np.ones(n)*2,
        'latency_assessed':np.ones(n,bool)})

class FeaturesTests(unittest.TestCase):
    def test_future_invariance(self):
        f=example();a=augment(f.iloc[:20]);f.loc[20:,'duration_p50_ms']=1e9
        np.testing.assert_array_equal(a[list(SEQUENCE)],augment(f).iloc[:20][list(SEQUENCE)])
    def test_labels_do_not_affect_features(self):
        f=example();a=augment(f);f['window_is_anomaly']=True;f['scenario_types']='fault'
        np.testing.assert_array_equal(a[list(SEQUENCE)],augment(f)[list(SEQUENCE)])
    def test_control_replay_state_is_separate(self):
        f=example();g=example();g.loc[20:,'duration_p50_ms']=10000
        r=augment(pd.concat([f,g],ignore_index=True))
        np.testing.assert_array_equal(r.iloc[:40][list(SEQUENCE)],augment(f)[list(SEQUENCE)])
        np.testing.assert_array_equal(r.iloc[40:][list(SEQUENCE)],augment(g)[list(SEQUENCE)])
    def test_current_recovery_resets_latency_memory(self):
        f=example();f.loc[12:18,'duration_p50_ms']=10000
        a=augment(f)
        self.assertGreater(a.seq_delay_cusum.iloc[18],0)
        self.assertEqual(a.seq_last_shift.iloc[19],0)
        self.assertEqual(a.seq_delay_cusum.iloc[19],0)
    def test_no_completion_does_not_use_nan_or_zero_duration(self):
        f=example();f.loc[12,'duration_p50_ms']=10000;f.loc[13:25,'latency_assessed']=False
        f.loc[13:25,'duration_p50_ms']=np.nan
        a=augment(f);self.assertGreater(a.seq_last_shift.iloc[14],0)
        self.assertEqual(a.seq_last_shift.iloc[21],0)
        self.assertEqual(a.seq_duration_shift.iloc[14],0)
        self.assertTrue(np.isfinite(a[list(SEQUENCE)]).all().all())
    def test_missing_minute_rejected(self):
        with self.assertRaises(ValueError):augment(example().drop(index=12))
    def test_constant_positive_arrivals_have_no_gap(self):
        # This layer consumes already hard-rule-checked aggregates.
        f=example();self.assertEqual(augment(f).seq_gap_surprise.max(),0)
    def test_estimators_ignore_oracle_columns(self):
        from adapter_feature_ablation.features import ADAPTER
        from shared_model_target.current_features import CURRENT
        f=augment(example());rng=np.random.default_rng(42)
        for c in (*ADAPTER,*CURRENT):
            if c not in f:f[c]=rng.normal(size=len(f))
        for a in ('if','kmeans','lof'):
            m=Detector(Setting(a,view='compact',clusters=2,neighbors=5,reference_cap=30)).fit(f)
            before=pickle.dumps(m);s=m.score(f);f['scenario_bits']=999
            np.testing.assert_array_equal(s,m.score(f));self.assertEqual(before,pickle.dumps(m))
            self.assertEqual(len(set(m.columns)&{'case_id','window_is_anomaly','scenario_bits'}),0)
    def test_sensitive_and_adaptive_future_invariance(self):
        from shared_model_v3.sensitive import augment as small,SMALL
        from shared_model_v3.adaptive import augment as adaptive,ADAPTIVE
        f=example();f['process_rows_started']=3.;f['completed_rows']=3.
        f.loc[10:15,'duration_p50_ms']=300
        a=small(adaptive(augment(f.iloc[:20])))
        f.loc[20:,'duration_p50_ms']=1e9;f.loc[20:,'native_execution_arrivals']=1000
        b=small(adaptive(augment(f)))
        np.testing.assert_array_equal(a[[*SMALL,*ADAPTIVE]],b.iloc[:20][[*SMALL,*ADAPTIVE]])
    def test_temporal_score_future_and_oracle_invariance(self):
        from shared_model_v3.temporal_score import Detector,Setting
        class Stub:
            algorithm='if';columns=();estimator=None;fit_rows=1;fit_seconds=0.;encoder=None;setting=None
        m=Detector(Stub(),Setting(hold=1.),0.,1.)
        f=example();s=np.linspace(0,2,len(f));a=m.filter(f.iloc[:20],s[:20])
        s[20:]=1e9;f['window_is_anomaly']=True
        np.testing.assert_array_equal(a,m.filter(f,s)[:20])
        with self.assertRaises(ValueError):m.filter(f.drop(columns='window_start'),s)
    def test_rank_encoder_training_and_scoring_paths_identical(self):
        from shared_model_v3.evidence import Detector,Setting
        from shared_model_v3.adaptive import augment as adaptive
        from adapter_feature_ablation.features import ADAPTER
        from shared_model_target.current_features import CURRENT
        f=example();f['process_rows_started']=3.;f['completed_rows']=3.
        f=adaptive(augment(f));rng=np.random.default_rng(42)
        for c in (*ADAPTER,*CURRENT):
            if c not in f:f[c]=rng.normal(size=len(f))
        for view in ('rank','summary','state'):
            m=Detector(Setting('kmeans',view=view,clusters=2)).fit(f)
            np.testing.assert_array_equal(m.encode(f),m.encode(m.derive(f)))
            self.assertEqual(m.record()['adapter_feature_count'],9)

if __name__=='__main__':unittest.main()
