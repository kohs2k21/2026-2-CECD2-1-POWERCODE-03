"""Behavioural checks for the SR prototype interface.

Run from the repository root:
    .venv/Scripts/python.exe -m unittest discover -s research/evaluation/process_sr_prototype/tests -v
"""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sr_prototype import methods  # noqa: E402


def train_frame(n=200, adapters=('A', 'B')):
    rng = np.random.default_rng(0)
    rows = []
    for i in range(n):
        adapter = adapters[i % len(adapters)]
        base = 100 if adapter == 'A' else 400
        rows.append({'adapter_type': adapter, 'status': 'S', 'total_count': 10 + i % 5,
                     'success_count': 10 + i % 5, 'error_count': 0, 'retry_count': 0,
                     'duration_ms': base + rng.normal(0, 10)})
    return pd.DataFrame(rows)


def _reason_core(reason):
    return reason.split(';', 1)[0]


class InterfaceTests(unittest.TestCase):
    def test_catalog_lists_every_method_with_schema_and_defaults(self):
        cat = methods.catalog()
        ids = {item['methodId'] for item in cat['items']}
        self.assertEqual(ids, {f'sr:{k}' for k in methods.METHODS})
        for item in cat['items']:
            for key in ('parameterSchema', 'defaults', 'defaultsSource', 'inputRequirements', 'fitStatistics', 'limitations'):
                self.assertIn(key, item)
        self.assertIn('error_and_retry_ratio_exceeded', cat['reasonCodes'])
        self.assertNotIn('ok', cat['reasonCodes'])
        self.assertIn('fallback_reason', cat['interface']['score'])

    def test_unknown_parameter_rejected(self):
        with self.assertRaises(ValueError):
            methods.fit('robust_z', train_frame(), {'nope': 1})

    def test_fit_uses_only_supplied_frame(self):
        """Fitted statistics equal a manual median/MAD/quantile computation on the supplied frame,
        and fitting a different frame afterwards does not alter the first result."""
        train = train_frame()
        rz = methods.fit('robust_z', train)
        iqr = methods.fit('iqr_fence', train)
        qt = methods.fit('quantile_tail', train, {'minGroupRows': 30})
        for adapter in ('A', 'B'):
            vals = train.loc[train.adapter_type.eq(adapter), 'duration_ms'].to_numpy(dtype=float)
            med = float(np.median(vals))
            mad = float(np.median(np.abs(vals - med)))
            meanad = float(np.mean(np.abs(vals - med)))
            self.assertEqual(rz['groups'][adapter]['n'], len(vals))
            self.assertAlmostEqual(rz['groups'][adapter]['median'], med, places=12)
            self.assertAlmostEqual(rz['groups'][adapter]['mad'], mad, places=12)
            self.assertAlmostEqual(rz['groups'][adapter]['meanad'], meanad, places=12)
            q1, q3 = np.quantile(vals, [0.25, 0.75])
            self.assertAlmostEqual(iqr['groups'][adapter]['q1'], float(q1), places=12)
            self.assertAlmostEqual(iqr['groups'][adapter]['q3'], float(q3), places=12)
            self.assertAlmostEqual(iqr['groups'][adapter]['iqr'], float(q3 - q1), places=12)
            self.assertAlmostEqual(qt['groups'][adapter]['threshold'], float(np.quantile(vals, 0.99)), places=12)
        pooled = train.duration_ms.to_numpy(dtype=float)
        self.assertEqual(rz['global']['n'], len(pooled))
        self.assertAlmostEqual(rz['global']['median'], float(np.median(pooled)), places=12)
        self.assertEqual(rz['train'], {'rows': 200, 'statusCounts': {'S': 200}, 'usedRows': 200})
        # a second fit on a polluted frame changes its own statistics but not the first fit
        snapshot = {k: dict(v) for k, v in rz['groups'].items()}
        polluted = methods.fit('robust_z', pd.concat([train, train.assign(duration_ms=10_000)]))
        self.assertNotEqual(snapshot, polluted['groups'])
        self.assertEqual(rz['groups'], snapshot)
        self.assertEqual(rz['groups']['A']['n'], 100)

    def test_score_row_matches_batch(self):
        train = train_frame()
        for key in methods.METHODS:
            fitted = methods.fit(key, train)
            batch = methods.score(key, train.head(5), fitted)
            self.assertEqual(list(batch.columns), ['flag', 'score', 'reason', 'basis', 'fallback_reason'])
            for i in range(5):
                single = methods.score_row(key, train.iloc[i].to_dict(), fitted)
                flag = batch.flag.iloc[i]
                self.assertEqual(single['flag'], None if pd.isna(flag) else bool(flag), key)
                self.assertEqual(single['reason'], batch.reason.iloc[i], key)
                self.assertEqual(single['fallback_reason'], batch.fallback_reason.iloc[i], key)

    def test_reason_vocabulary(self):
        """Every reason emitted on a mixed frame is a catalogued code (plus the ratio suffix grammar)."""
        train = train_frame()
        probe = pd.DataFrame([
            {'adapter_type': 'A', 'total_count': 10, 'success_count': 9, 'error_count': 1, 'retry_count': 0, 'duration_ms': 100},
            {'adapter_type': 'Z', 'total_count': 0, 'success_count': 0, 'error_count': 0, 'retry_count': 0, 'duration_ms': -1},
            {'adapter_type': None, 'total_count': None, 'success_count': None, 'error_count': None, 'retry_count': None, 'duration_ms': None},
        ])
        for key in methods.METHODS:
            out = methods.score(key, probe, methods.fit(key, train))
            for reason in out.reason:
                self.assertIn(_reason_core(reason), methods.REASON_CODES, (key, reason))
            for fb in out.fallback_reason:
                self.assertTrue(fb is None or fb in methods.FALLBACK_REASON_CODES, (key, fb))


class GroupBehaviourTests(unittest.TestCase):
    def test_unseen_adapter_unjudged_without_fallback_and_judged_with_global(self):
        train = train_frame()
        row = pd.DataFrame([{'adapter_type': 'Z', 'duration_ms': 5000}])
        strict = methods.score('robust_z', row, methods.fit('robust_z', train))
        self.assertTrue(pd.isna(strict.flag.iloc[0]))
        self.assertEqual(strict.reason.iloc[0], 'group_unseen')
        self.assertIsNone(strict.fallback_reason.iloc[0])
        loose = methods.score('robust_z', row, methods.fit('robust_z', train, {'fallback': 'global'}))
        self.assertTrue(bool(loose.flag.iloc[0]))
        self.assertEqual(loose.basis.iloc[0], 'global')
        self.assertEqual(loose.reason.iloc[0], 'above_threshold')

    def test_fallback_reason_preserved_with_global_basis(self):
        """basis=global keeps why the group was unusable: unseen / missing / insufficient (decision D2)."""
        train = train_frame()
        small = pd.concat([train, pd.DataFrame([{'adapter_type': 'S', 'status': 'S', 'duration_ms': 100.0}] * 5)],
                          ignore_index=True)
        fitted = methods.fit('iqr_fence', small, {'fallback': 'global', 'minGroupRows': 30})
        rows = pd.DataFrame([{'adapter_type': 'Z', 'duration_ms': 100}, {'adapter_type': None, 'duration_ms': 100},
                             {'adapter_type': 'S', 'duration_ms': 100}, {'adapter_type': 'A', 'duration_ms': 100},
                             {'adapter_type': 'Z', 'duration_ms': None}])
        out = methods.score('iqr_fence', rows, fitted)
        self.assertEqual(out.basis.tolist(), ['global', 'global', 'global', 'group', 'global'])
        self.assertEqual(out.fallback_reason.tolist(),
                         ['group_unseen', 'group_missing', 'group_support_insufficient', None, 'group_unseen'])
        self.assertEqual(out.reason.tolist()[:4], ['within_threshold'] * 4)
        self.assertEqual(out.reason.iloc[4], 'value_missing')
        self.assertTrue(pd.isna(out.flag.iloc[4]))
        single = methods.score_row('iqr_fence', {'adapter_type': None, 'duration_ms': 100}, fitted)
        self.assertEqual((single['basis'], single['fallback_reason']), ('global', 'group_missing'))

    def test_insufficient_group_support_reported(self):
        train = train_frame(40)
        fitted = methods.fit('quantile_tail', train, {'minGroupRows': 1000, 'fallback': 'none'})
        out = methods.score('quantile_tail', train.head(1), fitted)
        self.assertEqual(out.reason.iloc[0], 'group_support_insufficient')
        self.assertTrue(pd.isna(out.flag.iloc[0]))

    def test_mad_zero_policy(self):
        train = train_frame().assign(duration_ms=100.0)
        row = pd.DataFrame([{'adapter_type': 'A', 'duration_ms': 150}])
        strict = methods.score('robust_z', row, methods.fit('robust_z', train))
        self.assertEqual(strict.reason.iloc[0], 'scale_zero')
        self.assertTrue(pd.isna(strict.flag.iloc[0]))
        # meanAD is also 0 for a constant group, so still unjudged: no silent verdict.
        loose = methods.score('robust_z', row, methods.fit('robust_z', train, {'madZeroPolicy': 'meanad'}))
        self.assertTrue(pd.isna(loose.flag.iloc[0]))

    def test_upper_side_ignores_fast_rows(self):
        train = train_frame()
        fitted = methods.fit('robust_z', train)
        fast = methods.score('robust_z', pd.DataFrame([{'adapter_type': 'A', 'duration_ms': 1}]), fitted)
        self.assertFalse(bool(fast.flag.iloc[0]))
        both = methods.score('robust_z', pd.DataFrame([{'adapter_type': 'A', 'duration_ms': 1}]),
                             methods.fit('robust_z', train, {'side': 'both'}))
        self.assertTrue(bool(both.flag.iloc[0]))

    def test_centered_tail_uses_group_median_and_pooled_threshold(self):
        train = train_frame()
        fitted = methods.fit('centered_tail', train)
        self.assertIn('threshold', fitted['pooled'])
        rows = pd.DataFrame([{'adapter_type': 'A', 'duration_ms': 100}, {'adapter_type': 'B', 'duration_ms': 400},
                             {'adapter_type': 'B', 'duration_ms': 400 + fitted['pooled']['threshold'] + 1}])
        out = methods.score('centered_tail', rows, fitted)
        self.assertEqual(out.flag.tolist(), [False, False, True])
        self.assertEqual(out.fallback_reason.tolist(), [None, None, None])


class FormulaRuleTests(unittest.TestCase):
    def test_ratio_rule_total_zero_unjudged_and_error_detected(self):
        fitted = methods.fit('ratio_rule', train_frame())
        rows = pd.DataFrame([{'total_count': 0, 'error_count': 0, 'retry_count': 0},
                             {'total_count': 10, 'error_count': 1, 'retry_count': 0},
                             {'total_count': 10, 'error_count': 0, 'retry_count': 1},
                             {'total_count': 10, 'error_count': 0, 'retry_count': 0},
                             {'total_count': 10, 'error_count': 1, 'retry_count': 1}])
        out = methods.score('ratio_rule', rows, fitted)
        self.assertTrue(pd.isna(out.flag.iloc[0]))
        self.assertEqual(out.reason.iloc[0], 'total_zero_or_missing')
        self.assertEqual(out.reason.tolist()[1:], ['error_ratio_exceeded', 'retry_ratio_exceeded', 'within_threshold',
                                                   'error_and_retry_ratio_exceeded'])
        self.assertEqual(out.flag.tolist()[1:], [True, True, False, True])

    def test_ratio_rule_partial_verdict_when_one_threshold_unavailable(self):
        """Decision D (partial verdict): error threshold alone is enough to judge the error ratio;
        the unavailable retry threshold is reported in the reason suffix, never silently dropped."""
        short = train_frame(40)                      # < minTrainRows=200 -> fitted retry threshold unavailable
        fitted = methods.fit('ratio_rule', short)
        self.assertEqual(fitted['thresholds']['retryRatioMax'], {'value': None, 'basis': 'unavailable'})
        rows = pd.DataFrame([{'total_count': 10, 'error_count': 1, 'retry_count': 0},
                             {'total_count': 10, 'error_count': 0, 'retry_count': 9},
                             {'total_count': 10, 'error_count': None, 'retry_count': 0},
                             {'total_count': 0, 'error_count': 1, 'retry_count': 0}])
        out = methods.score('ratio_rule', rows, fitted)
        self.assertEqual([None if pd.isna(v) else bool(v) for v in out.flag], [True, False, None, None])
        self.assertEqual(out.reason.tolist(), ['error_ratio_exceeded;retry_unjudged:threshold_unavailable',
                                               'within_threshold;retry_unjudged:threshold_unavailable',
                                               'value_missing', 'total_zero_or_missing'])
        self.assertEqual(out.basis.iloc[0], 'error:parameter,retry:unavailable')
        # neither threshold available -> no verdict at all
        none = methods.score('ratio_rule', rows, methods.fit('ratio_rule', short, {'errorRatioMax': None}))
        self.assertTrue(none.flag.isna().all())
        self.assertEqual(none.reason.tolist()[:3], ['threshold_unavailable'] * 3)
        # both thresholds available but one value missing -> partial on the other side
        full = methods.score('ratio_rule', rows.iloc[[2]], methods.fit('ratio_rule', train_frame()))
        self.assertEqual(full.reason.iloc[0], 'within_threshold;error_unjudged:value_missing')
        self.assertFalse(bool(full.flag.iloc[0]))

    def test_count_conservation(self):
        fitted = methods.fit('count_conservation', train_frame())
        self.assertEqual(fitted['train']['violations'], 0)
        rows = pd.DataFrame([{'total_count': 10, 'success_count': 9, 'error_count': 1},
                             {'total_count': 11, 'success_count': 9, 'error_count': 1},
                             {'total_count': None, 'success_count': 9, 'error_count': 1}])
        out = methods.score('count_conservation', rows, fitted)
        self.assertEqual([None if pd.isna(v) else bool(v) for v in out.flag], [False, True, None])
        self.assertEqual(out.score.iloc[1], 1.0)
        self.assertEqual(out.basis.tolist(), ['formula'] * 3)


if __name__ == '__main__':
    unittest.main()
