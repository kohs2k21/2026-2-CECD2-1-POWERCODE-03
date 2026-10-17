"""Real pinned numeric generation and bounded artifact persistence checks."""
import copy
from collections import Counter
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import jsonschema
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trainer.synthetic_adbench import generate, score
from trainer.synthetic_storage import store
from trainer import synthetic_execution
from trainer.metrics import measurement
from trainer.service_contract import evaluation_envelope


class SyntheticRuntimeTests(unittest.TestCase):
    def test_failed_generation_family_is_unavailable_with_null_metrics_and_matches_schema(self):
        request = {'profileId': 'esb-process-research-v1', 'profileVersion': '1',
                   'scenarios': [{'scenario': 'P04', 'severity': 1}], 'seed': 11, 'targetRows': 3, 'repetitions': 1}
        spec = {'syntheticEvaluation': request, 'syntheticProfileBinding': {}, 'snapshotId': 'fixture', 'purpose': 'test'}
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (False,)
        rows = [(uuid.uuid4(), {'status': 'S'}, 'test', None)]
        with patch.object(synthetic_execution, 'validate_source', return_value={}), \
             patch.object(synthetic_execution, '_model_manifest', return_value=({}, {'trainingSnapshotId': 'fixture'})), \
             patch.object(synthetic_execution, '_train_rows', return_value=[]), \
             patch.object(synthetic_execution, '_esb', return_value=({}, {'generated': measurement(0, 'count')}, [], Counter({'generation_failed': 1}))), \
             patch.object(synthetic_execution, 'model_reference'), patch.object(synthetic_execution, 'store', return_value={}):
            family = synthetic_execution.evaluate(connection, spec, {}, rows, Path('.'), uuid.uuid4())
        self.assertEqual(family['status'], 'unavailable')
        self.assertEqual(family['reason'], 'synthetic_generation_failed')
        self.assertIsNone(family['metrics'])
        envelope = evaluation_envelope(); envelope['synthetic'] = family
        schema = json.loads((Path(__file__).resolve().parents[2] / 'contracts/trainer-evaluation-envelope.schema.json').read_text())
        jsonschema.validate(envelope, schema)

    def test_numeric_modes_reproduce_keep_constants_and_distribution_labels_separate(self):
        train = np.column_stack((np.arange(30) / 10, np.sin(np.arange(30)), np.ones(30)))
        original = train.copy()
        state = np.random.get_state()
        for mode in ('local', 'cluster', 'global'):
            result = generate(train, mode=mode, seed=11, target_rows=3)
            self.assertEqual(result, generate(train, mode=mode, seed=11, target_rows=3))
            self.assertEqual(result['labels'], [0] * 30 + [1] * 3)
            self.assertEqual(len(result['values']), 33)
            self.assertEqual(result['constantFeatureIndices'], [2])
            self.assertTrue(all(row[2] == 1 for row in result['values']))
            self.assertEqual(result['generationStage'], 'after_frozen_feature_transform_before_frozen_scaler')
            self.assertIn('not_ESB_fault', result['truthMeaning'])
        self.assertTrue(np.array_equal(train, original))
        after = np.random.get_state()
        self.assertEqual(state[0], after[0]); self.assertTrue(np.array_equal(state[1], after[1]))
        self.assertEqual(state[2:], after[2:])

    def test_numeric_requires_train_variation_finite_values_and_bounded_modes(self):
        for train, mode in [(np.ones((10, 2)), 'local'), (np.zeros((8, 2)), 'cluster'),
                            (np.array([[float('nan')]] * 10), 'global'), (np.ones((10, 2)), 'dependency')]:
            with self.assertRaises(ValueError): generate(train, mode=mode, seed=11, target_rows=3)

    def test_distribution_score_has_no_PROCESS_or_ESB_normal_claim(self):
        self.assertEqual(score([0, 0, 1, 1], [False, True, True, True]), {
            '0': {'rows': 2, 'predictedAnomalyCount': 1, 'predictedAnomalyRate': .5},
            '1': {'rows': 2, 'predictedAnomalyCount': 2, 'predictedAnomalyRate': 1}})
        with self.assertRaises(ValueError): score([0], [1])

    def test_content_addressed_store_reuses_exact_bytes_and_detects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            job = uuid.uuid4()
            document = {'원본': [1, 2, 3], 'manifest': {'version': '1'}}
            reference = store(directory, job, document)
            self.assertEqual(reference, store(directory, job, copy.deepcopy(document)))
            path = Path(directory) / reference['relativePath']
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), document)
            path.write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'hash_invalid'): store(directory, job, document)

    def test_storage_rejects_unsafe_id_nonfinite_and_oversized_result(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError): store(directory, '../outside', {})
            with self.assertRaises(ValueError): store(directory, uuid.uuid4(), {'n': float('nan')})
            with patch('trainer.synthetic_storage.MAX_BYTES', 5):
                with self.assertRaisesRegex(ValueError, 'size_exceeded'): store(directory, uuid.uuid4(), {'data': 'too-big'})

    def test_storage_checks_symlink_before_creating_outside_job_directory(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            try: (Path(directory) / 'evaluations').symlink_to(outside, target_is_directory=True)
            except OSError: self.skipTest('symlink creation unavailable')
            with self.assertRaisesRegex(ValueError, 'path_invalid'): store(directory, uuid.uuid4(), {})
            self.assertEqual(list(Path(outside).iterdir()), [])


if __name__ == '__main__': unittest.main()
