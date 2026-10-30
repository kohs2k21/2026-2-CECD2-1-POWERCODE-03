"""Safety and arithmetic checks for post-hoc evidence ablation."""
import unittest
from unittest.mock import patch

import numpy as np
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score

from shared_model_v6.features import Evidence, RAW, BLOCKS
from shared_model_v7.pmf_filter import Encoder as PMFEncoder
from shared_model_v7.causal_hold import _SCORES
from shared_model_v7.feature_importance import (
    neutralized, interventions, confusion, metrics, per_case_confusion, bootstrap_drop,
)


class ImportanceTests(unittest.TestCase):
    def test_confusion_and_metrics_match_sklearn(self):
        y = np.array([False, False, True, True, True])
        score = np.array([0., 2., 2., 1., .5])
        c = confusion(y, score)
        np.testing.assert_array_equal(c, [1, 1, 2, 1])
        actual = metrics(c)
        for name, fn in [('accuracy', accuracy_score), ('precision', precision_score),
                         ('recall', recall_score), ('f1', f1_score)]:
            self.assertAlmostEqual(actual[name], fn(y, score > 1))
        self.assertEqual(actual['fpr'], .5)

    def test_zero_denominators_stay_finite(self):
        self.assertTrue(all(np.isfinite(v) for v in metrics(np.zeros(4)).values()))

    def test_group_is_masked_before_summary_without_mutating_input(self):
        encoder = object.__new__(Evidence)
        x = np.ones((3, len(RAW)))
        original = x.copy()
        before = encoder.summarize(x, 'compact_joint')
        with neutralized(BLOCKS['delay']) as calls:
            after = encoder.summarize(x, 'compact_joint')
            raw = encoder.summarize(x, 'raw')
            self.assertGreater(calls['summary'], 0)
        # The two delay summaries and their joint geometry are recomputed.
        np.testing.assert_array_equal(after[:, 2:4], 0)
        np.testing.assert_array_equal(raw[:, [RAW.index(c) for c in BLOCKS['delay']]], 0)
        np.testing.assert_array_equal(x, original)
        np.testing.assert_array_equal(encoder.summarize(x, 'compact_joint'), before)

    def test_auxiliary_arrays_are_not_mutated(self):
        point, burst = np.ones(4), np.full(4, 2.)
        encoder = object.__new__(PMFEncoder)
        with patch.object(PMFEncoder, 'auxiliary', return_value=(point, burst)):
            with neutralized(auxiliary=['point']):
                a, b = encoder.auxiliary(None)
                np.testing.assert_array_equal(a, 0)
                np.testing.assert_array_equal(b, 2)
        np.testing.assert_array_equal(point, 1)
        np.testing.assert_array_equal(burst, 2)

    def test_patches_restore_on_error_and_score_cache_is_cleared(self):
        original = Evidence.summarize
        _SCORES['stale'] = 'score'
        with self.assertRaisesRegex(RuntimeError, 'test'):
            with neutralized(['seq_last_shift']):
                self.assertEqual(_SCORES, {})
                _SCORES['interrupted'] = 'score'
                raise RuntimeError('test')
        self.assertIs(Evidence.summarize, original)
        self.assertEqual(_SCORES, {})

    def test_interventions_cover_five_groups_and_seventeen_features(self):
        values = interventions()
        self.assertEqual(len(values), 22)
        self.assertEqual(len({v['name'] for v in values}), 22)
        groups = [v for v in values if v['kind'] == 'group']
        self.assertEqual(len(groups), 5)
        self.assertEqual(set(c for v in groups for c in v['raw_columns']), set(RAW))
        self.assertEqual(next(v for v in groups if v['name'] == 'point')['auxiliary'], ['point'])
        self.assertEqual(next(v for v in groups if v['name'] == 'burst')['auxiliary'], ['burst'])

    def test_unknown_feature_is_rejected(self):
        with self.assertRaises(ValueError):
            with neutralized(['scenario_label']):
                pass

    def test_bootstrap_preserves_normal_anomaly_pair(self):
        case = np.array([0, 0, 1, 1, 0, 0, 1, 1])
        y = np.array([True, True, True, True, False, False, False, False])
        score = np.array([2., 0., 2., 2., 0., 0., 0., 2.])
        ids, counts = per_case_confusion(case, y, score)
        np.testing.assert_array_equal(ids, [0, 1])
        np.testing.assert_array_equal(counts.sum(axis=0), confusion(y, score))
        np.testing.assert_array_equal(counts.sum(axis=1), [4, 4])
        self.assertEqual(bootstrap_drop([counts], [counts], repetitions=20), [0., 0.])


if __name__ == '__main__':
    unittest.main()
