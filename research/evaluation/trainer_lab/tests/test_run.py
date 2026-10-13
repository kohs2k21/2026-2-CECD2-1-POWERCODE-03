"""Runner refusal and fit/evaluation separation checks."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from research.evaluation.trainer_lab import run
from model_evaluation.models import ComparisonConfig
from process_anomaly_pipeline.data import prepare
import test_datasets as fixtures
rows = fixtures.rows


class RunnerTests(unittest.TestCase):
    setUp = fixtures.DatasetTests.setUp
    tearDown = fixtures.DatasetTests.tearDown
    csv = fixtures.DatasetTests.csv

    def args(self, source, *, train=False):
        return SimpleNamespace(input=self.csv(source), kind='csv', output=self.root/'run',
            train=train, algorithm='both', source_commit='synthetic-test')

    def test_training_blocks_known_lineage_before_fitting(self):
        from research.evaluation.trainer_lab import run
        source = rows()
        for item in source:
            item['source_template_id'] = 'shared-across-dates'
        with patch.object(run.Detector, 'fit') as fit:
            with self.assertRaisesRegex(ValueError, 'violation'):
                run.run(self.args(source, train=True))
            fit.assert_not_called()
        result = json.loads((self.root/'run'/'run.json').read_text(encoding='utf-8'))
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('errorMessage', result)

    def test_insufficient_references_rejected_before_fit(self):
        from research.evaluation.trainer_lab import run
        with patch.object(run.Detector, 'fit') as fit:
            with self.assertRaisesRegex(ValueError, 'Too few'):
                run.run(self.args(rows(), train=True))
            fit.assert_not_called()

    def test_short_history_failure_preserves_quality_evidence(self):
        from research.evaluation.trainer_lab import run
        with self.assertRaisesRegex(ValueError, 'five observed dates'):
            run.run(self.args(rows(days=2)))
        quality = json.loads((self.root/'run'/'quality.json').read_text(encoding='utf-8'))
        result = json.loads((self.root/'run'/'run.json').read_text(encoding='utf-8'))
        self.assertEqual(quality['rows'], 40)
        self.assertEqual(quality['observed_start_dates'], 2)
        self.assertEqual(result['status'], 'failed')

    def test_audit_keeps_failed_evidence_without_fit(self):
        from research.evaluation.trainer_lab import run
        source = rows()
        for item in source:
            item['observed_at'] = item['startTime']
        with patch.object(run.Detector, 'fit') as fit:
            result = run.run(self.args(source))
            fit.assert_not_called()
        self.assertEqual(result['status'], 'audited')
        quality = json.loads((self.root/'run'/'quality.json').read_text(encoding='utf-8'))
        self.assertEqual(quality['time_availability']['status'], 'failed')

    def test_training_eligibility_excludes_invalid_or_inconsistent_reference(self):
        from research.evaluation.trainer_lab.run import eligible
        source = rows()
        source[0]['totalCount'] = .5
        source[1]['errorCount'] = 1
        source[2]['processHubId'] = None
        source[3]['status'] = 'F'
        source.append(dict(source[4]))
        frame = prepare(source)
        mask = eligible(frame, training=True)
        self.assertTrue(mask.iloc[5])
        self.assertFalse(mask.iloc[[0,1,2,3,4,-1]].any())

    def test_status_is_not_accuracy_ground_truth(self):
        from research.evaluation.trainer_lab.run import evaluate
        frame = prepare(rows())
        fake = SimpleNamespace(score=lambda f: np.zeros(len(f)), threshold=1.)
        report, _ = evaluate(fake, frame)
        self.assertIsNone(report['accuracyMetrics'])
        self.assertEqual(report['labels'], 'not_provided')
        frame['row_is_anomaly'] = False
        frame.loc[0, 'row_is_anomaly'] = True
        report, _ = evaluate(fake, frame)
        self.assertIsNotNone(report['accuracyMetrics'])
        frame['row_is_anomaly'] = 'unknown'
        with self.assertRaises(ValueError):
            evaluate(fake, frame)

    def test_real_small_training_uses_disjoint_fit_and_test_cohorts(self):
        from research.evaluation.trainer_lab import run
        source = rows(per_day=150)
        seen = []
        original_fit = run.Detector.fit
        def traced_fit(algorithm, train, calibration, threshold, unit, config):
            groups = [set(part.transactionId) for part in (train, calibration, threshold)]
            self.assertFalse(groups[0] & groups[1] or groups[0] & groups[2] or groups[1] & groups[2])
            for part in (train, calibration, threshold):
                self.assertLess(part.startTime.max(), pd.Timestamp('2026-01-09'))
            seen.append((algorithm, [len(part) for part in (train, calibration, threshold)]))
            return original_fit(algorithm, train, calibration, threshold, unit,
                ComparisonConfig(n_estimators=8, kmeans_max_iter=10, kmeans_n_init=1))
        with patch.object(run.Detector, 'fit', side_effect=traced_fit):
            result = run.run(self.args(source, train=True))
        self.assertEqual(result['status'], 'completed')
        self.assertEqual([name for name, _ in seen], ['if', 'kmeans'])
        self.assertFalse(result['originalBenchmarkReproduced'])
        self.assertGreater(result['peakRssBytes'], 0)
        for model in result['models'].values():
            self.assertEqual(model['evaluation']['totalRows'], 300)
            self.assertTrue(model['roundtrip']['decisionsEqual'])


