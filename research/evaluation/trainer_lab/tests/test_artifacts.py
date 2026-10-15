"""Artifact integrity, pairing, and inference equivalence checks."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from research.evaluation.trainer_lab import artifacts
from model_evaluation.models import ComparisonConfig, Detector
from process_anomaly_pipeline.data import prepare
from process_anomaly_pipeline.features import raw_features
from test_datasets import rows


class ArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = prepare(rows())
        cls.train = cls.frame.iloc[:100]
        cls.calibration = cls.frame.iloc[100:140]
        cls.threshold = cls.frame.iloc[140:180]
        cls.test = cls.frame.iloc[180:]
        config = ComparisonConfig(n_estimators=8, kmeans_max_iter=10, kmeans_n_init=1)
        cls.detectors = {algorithm: Detector.fit(algorithm, cls.train, cls.calibration,
            cls.threshold, 'row', config) for algorithm in ('if', 'kmeans')}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / 'bundle'
        self.manifest = artifacts.save_bundle(self.detectors['if'], self.path, unit='row',
                                              provenance={'fixture': 'synthetic'})

    def tearDown(self):
        self.temp.cleanup()

    def update_manifest(self, modify):
        manifest = json.loads((self.path / 'manifest.json').read_text(encoding='utf-8'))
        modify(manifest)
        artifacts.write_json(self.path / 'manifest.json', manifest)

    def test_both_model_roundtrips_preserve_feature_score_and_decision(self):
        for algorithm, original in self.detectors.items():
            with self.subTest(algorithm=algorithm):
                path = self.root / algorithm
                manifest = artifacts.save_bundle(original, path, unit='row', provenance={})
                restored, _ = artifacts.load_bundle(path, trusted=True, expected_unit='row',
                    expected_features=original.encoder.columns, expected_provenance_hash=manifest['provenanceHash'])
                result = artifacts.compare_roundtrip(original, restored, self.test)
                self.assertTrue(result['featuresEqual'] and result['scoresEqual'] and result['decisionsEqual'])

    def test_untrusted_load_blocked_before_deserialization(self):
        with patch.object(artifacts.joblib, 'load') as loader:
            with self.assertRaisesRegex(ValueError, 'trusted'):
                artifacts.load_bundle(self.path)
            loader.assert_not_called()

    def test_corrupted_artifact_rejected_before_deserialization(self):
        with (self.path / 'model.joblib').open('ab') as stream:
            stream.write(b'corruption')
        with patch.object(artifacts.joblib, 'load') as loader:
            with self.assertRaisesRegex(ValueError, 'hash'):
                artifacts.load_bundle(self.path, trusted=True)
            loader.assert_not_called()

    def test_wrong_fit_pair_rejected_even_with_updated_file_hash(self):
        other = self.root / 'other'
        artifacts.save_bundle(self.detectors['if'], other, unit='row', provenance={})
        shutil.copyfile(other / 'fit.joblib', self.path / 'fit.joblib')
        self.update_manifest(lambda m: m['files'].update({'fit.joblib': artifacts.sha256(self.path / 'fit.joblib')}))
        with self.assertRaisesRegex(ValueError, 'pairing'):
            artifacts.load_bundle(self.path, trusted=True)

    def test_expected_contract_checks(self):
        for kwargs in ({'expected_unit': 'window'},
                       {'expected_features': list(reversed(self.detectors['if'].encoder.columns))},
                       {'expected_provenance_hash': 'wrong'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                artifacts.load_bundle(self.path, trusted=True, **kwargs)

    def test_environment_and_provenance_tamper_rejected_before_load(self):
        original = (self.path / 'manifest.json').read_text(encoding='utf-8')
        for modify in (lambda m: m['environment'].update(python='0.0.0'),
                       lambda m: m['provenance'].update(fixture='different'),
                       lambda m: m.update(featureColumns=list(reversed(m['featureColumns'])))):
            (self.path / 'manifest.json').write_text(original, encoding='utf-8')
            self.update_manifest(modify)
            with self.assertRaises(ValueError):
                artifacts.load_bundle(self.path, trusted=True)

    def test_existing_bundle_cannot_be_overwritten(self):
        before = artifacts.sha256(self.path / 'model.joblib')
        with self.assertRaises(FileExistsError):
            artifacts.save_bundle(self.detectors['kmeans'], self.path, unit='row', provenance={})
        self.assertEqual(before, artifacts.sha256(self.path / 'model.joblib'))

    def test_test_distribution_does_not_refit_encoder(self):
        detector = self.detectors['if']
        before_medians = detector.encoder.medians.copy()
        before_center = detector.encoder.scaler.center_.copy()
        extreme = self.test.copy()
        extreme['totalCount'] = 1e12
        extreme['duration_ms'] = 1e12
        detector.score(extreme)
        pd.testing.assert_series_equal(before_medians, detector.encoder.medians)
        np.testing.assert_array_equal(before_center, detector.encoder.scaler.center_)
        pd.testing.assert_series_equal(raw_features(self.train).median().fillna(0), detector.encoder.medians)
