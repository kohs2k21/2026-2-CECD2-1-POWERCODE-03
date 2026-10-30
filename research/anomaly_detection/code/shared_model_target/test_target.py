import unittest
import pickle
import numpy as np
import pandas as pd
from shared_model_target.features import augment,NEW
from shared_model_target.calibration import NormalReference


def example(n=80):
    f=pd.DataFrame({"case_id":"a","adapterType":"DB.READER","phase":"steady",
       "window_start":pd.date_range("2026-01-01",periods=n,freq="min").astype(str),
       "native_execution_arrivals":np.repeat(5.,n),"completion_count_1m":np.repeat(5.,n),
       "log_max_duration_1m":np.repeat(5.,n),"log_p50_duration_5m":np.repeat(4.5,n),
       "residual_mean_1m":np.repeat(.2,n),"residual_mean_5m":np.repeat(.2,n),
       "traffic_assessed":True,"latency_assessed":True,"temporal_assessed":True})
    return f


class FeatureTests(unittest.TestCase):
    def test_confirmation_truth_is_keyed_not_positional(self):
        from shared_model_target.confirm import aligned_truth
        source=pd.DataFrame({"case_id":["z","a"],"window_start":["t1","t2"],"window_is_anomaly":[True,False]})
        frame=source.iloc[::-1].drop(columns="window_is_anomaly")
        np.testing.assert_array_equal(aligned_truth(frame,source),[False,True])
    def current_example(self):
        f=example()
        f["arrival_dev_1m"]=0.
        f["duration_upper_max_1m"]=.7;f["duration_lower_max_1m"]=.7
        f["duration_p50_ms"]=np.expm1(4.5);f["duration_p95_ms"]=np.expm1(4.8)
        f["log_p95_duration_5m"]=4.8;f["log_mean_count_5m"]=np.log1p(10.)
        return f

    def test_current_features_stop_lagged_recovery_alarm(self):
        from shared_model_target.current_features import augment as current
        f=self.current_example();f.loc[20:30,"residual_mean_1m"]=5.
        f.loc[20:40,"residual_mean_5m"]=5.
        result=current(f)
        self.assertGreater(result.current_prefix_delay.iloc[30],0)
        self.assertEqual(result.current_prefix_delay.iloc[31],0)
        self.assertEqual(result.current_delay_trend.iloc[31],0)

    def test_current_adapter_p95_does_not_use_stale_5m_percentile(self):
        from shared_model_target.current_features import CurrentAdapterReference
        normal=self.current_example();reference=CurrentAdapterReference.fit(normal)
        f=normal.copy();f.loc[20:30,"duration_p95_ms"]=np.expm1(7.)
        f.loc[20:40,"log_p95_duration_5m"]=7.
        result=reference.transform(f)
        self.assertGreater(result.adapter_count_p95_z.iloc[30],0)
        self.assertEqual(result.adapter_count_p95_z.iloc[31],0)
        np.testing.assert_array_equal(result.log_p95_duration_5m,f.log_p95_duration_5m)

    def test_current_reference_prefix_causal(self):
        from shared_model_target.current_features import CurrentAdapterReference,CURRENT
        from adapter_feature_ablation.features import ADAPTER
        f=self.current_example();reference=CurrentAdapterReference.fit(f)
        short=reference.transform(f.iloc[:40])
        f.loc[40:,"duration_p95_ms"]=100000
        f.loc[40:,"native_execution_arrivals"]=10000
        entire=reference.transform(f).iloc[:40]
        cols=list(dict.fromkeys((*CURRENT,*ADAPTER)))
        np.testing.assert_array_equal(short[cols].to_numpy(float),entire[cols].to_numpy(float))

    def test_future_does_not_change_past(self):
        f=example();old=augment(f).iloc[:40]
        f.loc[40:,"native_execution_arrivals"]=1000
        new=augment(f).iloc[:40]
        np.testing.assert_array_equal(old[list(NEW)].to_numpy(float),new[list(NEW)].to_numpy(float))

    def test_gap_pressure_resets_on_arrival(self):
        f=example();f.loc[20:30,"native_execution_arrivals"]=0
        r=augment(f)
        self.assertGreater(r.gap_expected_prefix.iloc[30],r.gap_expected_prefix.iloc[20])
        self.assertEqual(r.gap_expected_prefix.iloc[31],0)

    def test_duplicate_cohort_is_rejected(self):
        with self.assertRaises(ValueError):augment(pd.concat([example(),example()],ignore_index=True))

    def test_no_case_length_or_labels_used(self):
        f=example();a=augment(f)
        f["window_is_anomaly"]=True;f["event_ids"]="injection"
        np.testing.assert_array_equal(a[list(NEW)].to_numpy(float),augment(f)[list(NEW)].to_numpy(float))

    def test_unknown_calibration_group_uses_global(self):
        f=example();f["case_id"]=[str(i//5) for i in range(len(f))]
        ref=NormalReference.fit(f,np.linspace(0,1,len(f)),"adapter",1.)
        unknown=f.copy();unknown["adapterType"]="UNSEEN"
        g=NormalReference.fit(f,np.linspace(0,1,len(f)),"global",1.)
        scores=np.linspace(0,2,len(f))
        np.testing.assert_allclose(ref.apply(unknown,scores),g.apply(unknown,scores),rtol=1e-14,atol=1e-14)

    def test_calibration_transform_does_not_refit(self):
        f=example();ref=NormalReference.fit(f,np.linspace(0,1,len(f)),"global",1.)
        before=pickle.dumps(ref);ref.apply(f,np.linspace(0,2,len(f)))
        self.assertEqual(before,pickle.dumps(ref))


if __name__=="__main__":unittest.main()
