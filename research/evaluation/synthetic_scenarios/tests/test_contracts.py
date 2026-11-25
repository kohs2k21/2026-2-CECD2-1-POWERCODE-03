import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from research.evaluation.synthetic_scenarios.datasets import guten_case, process_reference, tsb_case
from research.evaluation.synthetic_scenarios.load import one_condition
from research.evaluation.synthetic_scenarios.models import fit_detectors
from research.evaluation.synthetic_scenarios.run import metrics

CONFIG=json.loads(Path(__file__).parents[1].joinpath('config.json').read_text())
TSB=Path(os.environ.get('TSB_UAD_SOURCE','/tmp/pwc-synthetic-tsb-uad'))


class Contracts(unittest.TestCase):
    def test_native_gutentag_has_exactly_one_labelled_interval_and_clean_reference(self):
        cfg={**CONFIG,'gutentag_length':2000}
        with contextlib.redirect_stderr(io.StringIO()):
            a,b,base,changed,labels,_=guten_case(11,2,100,cfg)
        locations=np.flatnonzero(labels)
        self.assertEqual(len(locations),100)
        self.assertTrue(np.all(np.diff(locations)==1))
        np.testing.assert_array_equal(base[labels==0],changed[labels==0])
        np.testing.assert_allclose(changed[labels==1]-base[labels==1],2)
        self.assertEqual((len(a),len(b)),(1500,500))

    def test_tsb_add_keeps_original_outside_one_region_and_repeats_seed(self):
        base=np.random.default_rng(1).normal(6000,100,2000)
        first,labels=tsb_case(base,23,200,6000,TSB)
        again,repeat_labels=tsb_case(base,23,200,6000,TSB)
        np.testing.assert_array_equal(first,again);np.testing.assert_array_equal(labels,repeat_labels)
        self.assertEqual(labels.sum(),200)
        np.testing.assert_array_equal(first[labels==0],base[labels==0])
        np.testing.assert_allclose(first[labels==1]-base[labels==1],6000)

    def test_order_only_flip_cannot_change_global_pointwise_alarm_count(self):
        rng=np.random.default_rng(1);base=rng.normal(size=2000)
        model=fit_detectors(base[:800],base[800:1200],11,CONFIG)[0]
        changed,_=tsb_case(base,23,200,0,TSB,kind='flip')
        np.testing.assert_array_equal(np.sort(changed),np.sort(base))
        self.assertEqual(model.predict(base).sum(),model.predict(changed).sum())

    def test_threshold_is_calibration_quantile_not_test_quantile(self):
        rng=np.random.default_rng(3);train=rng.normal(size=1000);cal=rng.normal(size=500)
        models=fit_detectors(train,cal,11,CONFIG)
        for model in models:self.assertEqual(model.threshold,float(np.quantile(model.score(cal),.99)))
        self.assertAlmostEqual(models[1].center,float(np.median(train)))

    def test_metrics_do_not_expand_one_hit_into_whole_event(self):
        class Scores:
            threshold=.5;fit_seconds=0
            def score(self,x):return x
            def predict(self,x):return x>.5
        base=np.zeros(100);changed=base.copy();changed[22]=1
        labels=np.zeros(100,dtype=int);labels[20:30]=1
        result=metrics(Scores(),base,changed,labels)
        self.assertEqual((result['tp'],result['fn'],result['injected_recall']),(1,9,.1))
        self.assertEqual(result['first_hit_delay_samples'],2)

    def test_load_accounts_for_rejection_and_unfinished_work(self):
        class Slow:
            def predict(self,x):time.sleep(.003);return np.zeros(len(x),dtype=bool)
        cfg={**CONFIG,'load_queue_capacity':2,'load_duration_seconds':.05,'load_drain_seconds':.01,'latency_budget_ms':1}
        result=one_condition(Slow(),np.ones(200),2000,1,cfg)
        self.assertGreater(result['rejected'],0)
        self.assertEqual(result['offered'],result['rejected']+result['completed_by_cutoff']+result['errors']+result['unresolved_at_cutoff'])
        self.assertLess(result['within_budget_all_offered_fraction'],1)

    def test_real_reader_preserves_source_and_orders_completed_observations(self):
        n=3000;end=pd.date_range('2026-01-01',periods=n,freq='s')
        duration=np.arange(n)+1000
        frame=pd.DataFrame({'TRANSACTION_ID':['tx'+str(i) for i in range(n)],'PROCESS_HUB_ID':'hub',
            'PROCESS_ID':'p','STATUS':'S','START_TIME':end-pd.to_timedelta(duration,unit='ms'),
            'END_TIME':end,'SUCCESS_COUNT':1,'ERROR_COUNT':0,'TOTAL_COUNT':1})
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'P.csv';frame.iloc[::-1].to_csv(path,index=False)
            raw=path.read_bytes();a,b,c,detail=process_reference(path)
            self.assertEqual(path.read_bytes(),raw)
            np.testing.assert_allclose(np.concatenate([a,b,c]),duration)
            self.assertEqual(sum(detail['split_rows'].values()),n)
            self.assertTrue(detail['source_preserved'])


if __name__=='__main__':unittest.main()
