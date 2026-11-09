import pickle
import unittest
import numpy as np
import pandas as pd
from shared_model_v4.rank_context import Context,RANK,no_context
from shared_model_v4.rank_model import Detector,Setting
from shared_model_v4.manifold import COLUMNS

def example():
    n=40
    f=pd.DataFrame({'case_id':['one']*n,'window_start':pd.date_range('2026-10-01',periods=n,freq='min').astype(str),
        'native_execution_arrivals':np.ones(n)*3,'log_max_duration_1m':np.ones(n)*np.log1p(100),
        'latency_assessed':np.ones(n,bool),'adapterType':['KNOWN']*n})
    rng=np.random.default_rng(42)
    for c in COLUMNS:
        if c not in f:f[c]=rng.normal(size=n)
    for c in ('cadence_ready','traffic_assessed','temporal_assessed'):f[c]=True
    return f

def history():return {(weekend,h):np.array([0,1,2,3,3,3,4,5])for weekend in (False,True)for h in range(24)}
def context():return Context(history())

class CausalTests(unittest.TestCase):
    def test_future_cannot_change_past(self):
        f=example();a=context().transform(f.iloc[:20]);f.loc[20:,'log_max_duration_1m']=1e6;f.loc[20:,'native_execution_arrivals']=1000
        np.testing.assert_array_equal(a,context().transform(f).iloc[:20])
    def test_labels_ignored(self):
        f=example();a=context().transform(f);f['scenario_bits']=999;f['window_is_anomaly']=True
        np.testing.assert_array_equal(a,context().transform(f))
    def test_paired_streams_separate(self):
        f=example();g=example();g.loc[12:,'log_max_duration_1m']+=1
        r=context().transform(pd.concat([f,g],ignore_index=True))
        np.testing.assert_array_equal(r.iloc[:40],context().transform(f));np.testing.assert_array_equal(r.iloc[40:],context().transform(g))
    def test_missing_history_is_not_fabricated(self):
        f=example();r=context().transform(no_context(f));np.testing.assert_array_equal(r,np.zeros(r.shape))
        f=f[list(COLUMNS)];r=context().transform(f);np.testing.assert_array_equal(r,np.zeros(r.shape))
    def test_missing_grid_rejected(self):
        with self.assertRaises(ValueError):context().transform(example().drop(index=12))
    def test_run_and_recovery(self):
        f=example();f.loc[10:16,'log_max_duration_1m']+=1;r=context().transform(f)
        self.assertGreater(r.rank_upper_run.iloc[16],r.rank_upper_run.iloc[10]);self.assertEqual(r.rank_upper_run.iloc[17],0)
        self.assertEqual(r.rank_upper_memory.iloc[17],0)
    def test_no_completions_only_retains_bounded_memory(self):
        f=example();f.loc[10,'log_max_duration_1m']+=1;f.loc[11:24,'latency_assessed']=False;f.loc[11:24,'log_max_duration_1m']=np.nan
        r=context().transform(f);self.assertGreater(r.rank_upper_memory.iloc[11],0);self.assertEqual(r.rank_upper_memory.iloc[19],0)
        self.assertTrue(np.isfinite(r.to_numpy()).all())
    def test_empty_run_and_burst(self):
        f=example();f.loc[10:15,'native_execution_arrivals']=0;f.loc[20,'native_execution_arrivals']=100
        r=context().transform(f);self.assertGreater(r.traffic_zero_logprob.iloc[15],r.traffic_zero_logprob.iloc[10]);self.assertEqual(r.traffic_zero_logprob.iloc[16],0)
        self.assertGreater(r.traffic_tail_1m.iloc[20],r.traffic_tail_1m.iloc[19])
    def test_models_identical_derived_and_raw_paths(self):
        f=example();f.loc[25:30,'log_max_duration_1m']+=1
        for a in ('if','kmeans','lof'):
            m=Detector(Setting(a,clusters=2,neighbors=5,trees=20,samples=20,trim=0.),history())
            m.fit(f);before=pickle.dumps(m);s=m.score(f)
            np.testing.assert_array_equal(s,m.score(m.derive(f)));self.assertEqual(before,pickle.dumps(m))
            loaded=pickle.loads(before);np.testing.assert_array_equal(s,loaded.score(f))
            self.assertFalse(set(m.columns)&{'case_id','window_is_anomaly','scenario_bits','generation_mode','label'})

class TabularTests(unittest.TestCase):
    def test_quantile_encoder_has_no_label_or_mode_input(self):
        from shared_model_v4.adbench_quantile import Detector as Tabular,Setting as TabularSetting
        f=example();rng=np.random.default_rng(41)
        for c in COLUMNS:
            if c not in ('cadence_ready','traffic_assessed','latency_assessed','temporal_assessed'):f[c]=rng.normal(size=len(f))
        m=Tabular(TabularSetting('kmeans',clusters=2)).fit(f);s=m.score(f);before=pickle.dumps(m)
        f['label']=True;f['generation_mode']='dependency'
        np.testing.assert_array_equal(s,m.score(f));self.assertEqual(before,pickle.dumps(m));np.testing.assert_array_equal(s,pickle.loads(before).score(f))
    def test_assigned_cluster_does_not_use_remote_broad_radius(self):
        from shared_model_v4.adbench_cluster import Detector as Cluster,Setting as ClusterSetting
        class Encoder:
            def transform(self,f):return f[['x','y']].to_numpy(float)
        class Centers:
            cluster_centers_=np.array([[0.,0.],[100.,100.]])
            def predict(self,x):return np.square(x[:,None,:]-self.cluster_centers_[None,:,:]).sum(axis=2).argmin(axis=1)
        m=Cluster(ClusterSetting(clusters=2,radius_power=1.),Encoder());m.estimator=Centers();m.radius=np.array([1.,1e6])
        self.assertEqual(m.score(pd.DataFrame({'x':[2.],'y':[0.]}))[0],2.)
    def test_exact_normal_FPR_upper_bound_is_conservative(self):
        from shared_model_v4.calibrate_adbench import upper_bound
        self.assertGreater(upper_bound(2,840),2/840);self.assertGreater(upper_bound(2,840),upper_bound(0,840))
    def test_empty_oracle_columns_not_selected(self):
        from shared_model_v4.manifold import COLUMNS
        self.assertEqual(len(COLUMNS),35);self.assertFalse(set(COLUMNS)&{'label','generation_mode','case_id','scenario_bits'})

if __name__=='__main__':unittest.main()
