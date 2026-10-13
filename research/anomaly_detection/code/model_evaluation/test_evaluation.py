"""Small correctness tests; no original/evaluation CSV is written or modified."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score

from model_evaluation.data import KEYS, normal_windows, unix_seconds, window_features
from model_evaluation.metrics import Counts, metric_record
from model_evaluation.models import ComparisonConfig, METHODS, SixModels
from process_anomaly_pipeline.data import prepare
from model_evaluation.windowing import aggregate_windows, window_reference_split


def window_fixture(counts, case_id="C1"):
    dates = pd.date_range("2026-10-13T12:00:00",periods=len(counts),freq="min")
    return pd.DataFrame({"case_id":case_id,"scope":"TARGET","adapterType":"A","processId":"P",
                         "window_start":dates.astype(str),"window_end":(dates+pd.Timedelta(minutes=1)).astype(str),
                         "execution_arrivals":counts,"duration_p50_ms":100.,"duration_p95_ms":120.,
                         "history_median_arrivals":2.,"history_p95_arrivals":4.,"history_support_windows":600})


class EvaluationTests(unittest.TestCase):
    def test_long_windows_keep_labels_causal_and_five_minutes_not_double_counted(self):
        class History:
            def lookup(self,dates,widths): return np.array([[w,2*w,100] for w in widths],float)
        base = window_fixture([1,2,3,4,5,6])
        base['window_is_anomaly'] = [0,0,1,0,0,0]
        raw = pd.DataFrame({'case_id':['C1']*6,'end_seconds':unix_seconds(base.window_end)-1,'duration':[10,20,30,40,50,60]})
        baseline = {'process':{},'adapter':{'A':100},'global':100}
        result = aggregate_windows(base,raw,3,History(),baseline)
        np.testing.assert_array_equal(result.window_is_anomaly,base.window_is_anomaly)
        np.testing.assert_allclose(result.execution_arrivals,[1,3,6,9,12,15])
        self.assertEqual(result.execution_arrivals_5m.iloc[-1],20)
        self.assertEqual(result.duration_p50_ms.iloc[-1],50)
        changed = raw.copy(); changed.loc[5,'duration'] = 999
        other = aggregate_windows(base,changed,3,History(),baseline)
        np.testing.assert_array_equal(result.duration_p95_ms[:5],other.duration_p95_ms[:5])

    def test_long_window_never_crosses_cases_or_completion_boundary(self):
        class History:
            def lookup(self,dates,widths): return np.array([[1,2,100] for w in widths],float)
        base = pd.concat([window_fixture([1,1]),window_fixture([9,9],'C2')],ignore_index=True)
        end = unix_seconds(base.window_end)
        raw = pd.DataFrame({'case_id':['C1','C1','C2'],'end_seconds':[end[0]-1,end[0],end[2]-1],'duration':[10,100,20]})
        result = aggregate_windows(base,raw,2,History(),{'process':{},'adapter':{'A':100},'global':100})
        self.assertEqual(result.duration_p95_ms.iloc[0],10)
        self.assertEqual(result.duration_p95_ms.iloc[2],20)
        self.assertEqual(result.execution_arrivals.iloc[2],9)

    def test_validation_source_is_excluded_from_all_window_references(self):
        part = prepare(pd.DataFrame({'source_template_id':['keep','holdout'],'adapterType':['A','A'],
                                    'processId':['P','P'],'startTime':['2026-01-01','2026-01-02'],
                                    'endTime':['2026-01-01T00:00:00.100','2026-01-02T00:00:00.900']}))
        _,sets,_,baseline = window_reference_split({n:part for n in ('train','calibration','threshold')},{'holdout'})
        self.assertTrue(all(s=={'keep'} for s in sets.values()))
        self.assertEqual(baseline['global'],100)

    def test_timestamp_resolution_and_detection_delay(self):
        text = pd.Series(['2026-10-13T12:00:00.000','2026-10-13T12:01:00.000'])
        expected = unix_seconds(text)
        self.assertEqual(expected[1]-expected[0],60.)
        for unit in ('us','ns','ms'):
            values = pd.to_datetime(text).astype(f'datetime64[{unit}]')
            np.testing.assert_array_equal(unix_seconds(values),expected)

    def test_metrics_match_sklearn(self):
        y = np.array([0,0,1,1,1,0])
        p = np.array([0,1,1,0,1,0])
        counts = Counts(); counts.update(y,np.repeat(p[:,None],6,axis=1))
        for row in counts.records():
            self.assertEqual(row["accuracy"],accuracy_score(y,p))
            self.assertEqual(row["precision"],precision_score(y,p))
            self.assertEqual(row["recall"],recall_score(y,p))
            self.assertEqual(row["f1"],f1_score(y,p))
        self.assertIsNone(metric_record(10,0,0,0)["f1"])
        self.assertEqual(metric_record(10,0,5,0)["precision"],0.)
        self.assertIsNone(metric_record(0,0,0,0)["accuracy"])

    def test_rolling_does_not_cross_cases_or_use_future(self):
        baselines = {"process":{},"adapter":{"A":100.},"global":100.}
        frame = pd.concat([window_fixture([1,2,3]),window_fixture([10,20,30],"C2")],ignore_index=True)
        original = window_features(frame,baselines)
        changed = frame.copy(); changed.loc[2,"execution_arrivals"] = 999
        changed = window_features(changed,baselines)
        np.testing.assert_allclose(original.loc[:1,"log_arrivals_5m"],changed.loc[:1,"log_arrivals_5m"])
        self.assertAlmostEqual(original.loc[3,"log_arrivals_5m"],np.log1p(10))
        self.assertAlmostEqual(original.loc[2,"log_arrivals_5m"],np.log1p(6))

    def test_missing_execution_restored_in_normal_windows(self):
        labels = pd.DataFrame({"transactionId":["E1"],"processId":["P"],"retryCount":["0"],"case_id":["C1"]}).set_index(KEYS,drop=False)
        cases = pd.DataFrame({"case_id":["C1"],"target_adapter":["A"],"target_process_id":["P"]})
        blueprint = window_fixture([1,0,0])
        raw = pd.DataFrame({"transactionId":["E1","E2"],"adapterType":["A","A"],"processId":["P","P"],
                            "startTime":["2026-10-13T12:00:10.000","2026-10-13T12:01:10.000"],
                            "endTime":["2026-10-13T12:00:10.100","2026-10-13T12:01:10.100"]})
        events = pd.DataFrame({"case_id":["C1"],"scenario":["input_stop"],"evidence_json":['{"missing_transaction_ids":["E2"]}']})
        audit = blueprint[["case_id","scope","window_start","window_end"]].copy()
        audit["normal_execution_arrivals"] = [1,1,0]
        def reader(path,**kwargs):
            if Path(path).name == "events.csv": return events
            if Path(path).name == "normal_processes.csv": return iter([raw])
            return audit
        with patch("model_evaluation.data.pd.read_csv",side_effect=reader):
            actual = normal_windows(Path("unused"),blueprint,labels,cases)
        self.assertEqual(list(actual.execution_arrivals),[1,1,0])
        self.assertEqual(list(actual.duration_p95_ms[:2]),[100.,100.])
        self.assertTrue(pd.isna(actual.duration_p95_ms.iloc[2]))

    def test_strict_threshold_and_fallback(self):
        class Fake:
            def __init__(self,value,threshold): self.value,self.threshold=value,threshold
            def score(self,frame): return np.full(len(frame),self.value)
        suite = SixModels("row",ComparisonConfig())
        for algorithm in ("if","kmeans"):
            suite.global_models[algorithm] = Fake(2.,2.)
            suite.experts[algorithm] = {"A":Fake(4.,3.)}
            suite.ensemble_thresholds[algorithm] = {"A":2.5}
        margins,pred,covered = suite.score(pd.DataFrame({"adapterType":["A","NEW"]}))
        self.assertFalse(pred[0,0]); self.assertTrue(pred[0,1]); self.assertTrue(pred[0,2])
        self.assertTrue((margins[1]==0).all()); self.assertFalse(pred[1].any())
        self.assertEqual(list(covered),[True,False])

    def test_both_algorithms_share_supported_cohort_and_ignore_oracles(self):
        rng = np.random.default_rng(42)
        partitions = {}
        for name,n,offset in (("train",1100,0),("calibration",240,2000),("threshold",240,4000)):
            starts = pd.date_range("2026-01-01",periods=n,freq="10min")+pd.Timedelta(days=offset)
            duration = rng.integers(30,250,size=n)
            raw = pd.DataFrame({"adapterType":["A","B"]*(n//2),"startTime":starts,
                                "endTime":starts+pd.to_timedelta(duration,unit="ms"),"totalCount":rng.integers(1,6,size=n),"retryCount":0})
            partitions[name] = prepare(raw)
        suite = SixModels("row",ComparisonConfig()).fit(partitions)
        self.assertEqual(set(suite.experts["if"]),set(suite.experts["kmeans"]))
        self.assertEqual(set(suite.experts["if"]),{"A","B"})
        frame = partitions["threshold"].iloc[:10].copy()
        first = suite.score(frame)[0]
        frame["row_is_anomaly"] = 1; frame["scenario_types"] = "injected_label"
        np.testing.assert_array_equal(first,suite.score(frame)[0])

    def test_invalid_settings(self):
        for settings in ({"quantile":1.},{"global_weight":0.},{"k":True},{"min_train":-1}):
            with self.assertRaises(ValueError): ComparisonConfig(**settings)


if __name__ == "__main__":
    unittest.main()
