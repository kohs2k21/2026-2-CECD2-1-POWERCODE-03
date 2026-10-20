"""Post-experiment regression for candidate-local MQ/relationship truth scope.

The actual generator, exporter, stress helper and metrics execute unchanged.
Only the explicit scoring-runner argument is a manual, GT-independent scorer;
this test checks orchestration rather than detector performance.
"""
from collections import Counter
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np
import pandas as pd

from research.evaluation.trend_sr_comparison import stress


def manual_score(bundle, item):
    ticks = pd.date_range(item.manifest['evaluation_start'], item.manifest['evaluation_end'],
                          freq='min', inclusive='left').as_unit('ns')
    minute = 60_000_000_000
    rows = []
    for group in ('always', 'business'):
        source = item.frame[item.frame.group.eq(group)]
        ends = pd.DatetimeIndex(source.index).as_unit('ns').asi8
        available = pd.DatetimeIndex(source.available_at).as_unit('ns').asi8
        known = (source.source_complete & source.category_supported).to_numpy(bool)
        position = np.searchsorted(ends, ticks.asi8 - 2 * minute)
        safe = np.clip(position, 0, len(ends) - 1)
        eligible = ((position < len(ends)) & (ends[safe] == ticks.asi8 - 2 * minute)
                    & known[safe] & (available[safe] <= ticks.asi8))
        all_known = pd.Series(known.astype(int)).rolling(1440, min_periods=1440).sum().to_numpy() == 1440
        pending = pd.Series(available).rolling(1440, min_periods=1440).max().to_numpy()
        common = eligible & all_known[safe] & (pending[safe] <= ticks.asi8)
        ready = eligible & common
        values = np.where(((ticks.asi8 // minute + 7 * bundle['label']) % 23) < 5, 2., 0.)
        rows.append(pd.DataFrame(dict(group=group, decision_tick=ticks,
            count=np.where(ready, values, np.nan), source_eligible=eligible,
            common_eligible=common, score_supported=ready,
            prediction=np.where(ready, values > .5, np.nan))))
    return pd.concat(rows, ignore_index=True)


class AuxiliaryScopeContracts(unittest.TestCase):
    def test_two_candidates_keep_quality_truth_separate_from_relation_truth(self):
        # The frozen implementation failed on candidate1/quality-cross/0 after
        # candidate0's last relation case overwrote the shared `potential`.
        parent = os.environ.get('TREND_SR_AUDIT_ARTIFACT_ROOT')
        if parent:
            parent = str(Path(parent).resolve())
            Path(parent).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='trend-sr-scope-', dir=parent) as temporary:
            output = Path(temporary)
            if parent:
                self.assertTrue(output.resolve().is_relative_to(Path(parent)))
            bundles = [dict(label=n, calibration={'thresholds': {'count': .5}}) for n in (0, 1)]
            records = []
            stress._run_auxiliary(9501, 'selection', bundles,
                                  SimpleNamespace(score_candidate_bundle=manual_score), output, records)
            self.assertEqual(Counter(row['candidate'] for row in records), {0: 88, 1: 88})
            self.assertEqual(len(list(output.glob('9501/candidate-*/**/evaluation.json'))), 90)
            candidates = [output / '9501' / f'candidate-{n}' for n in (0, 1)]
            for candidate in candidates:
                self.assertEqual(len(list((candidate / 'relation-contexts').glob('*/*/evaluation.json'))), 40)
                self.assertTrue((candidate / 'identifiability/paired-business-labels/alternative-normal-truth.json').exists())
            for arm in ('0', 'S', 'Q', 'SQ'):
                first, second = [candidate / 'quality-cross' / arm for candidate in candidates]
                metas = [json.loads((folder / 'source-manifest.json').read_text()) for folder in (first, second)]
                self.assertEqual(metas[0]['source_hash'], metas[1]['source_hash'])
                self.assertEqual(metas[0]['evaluation_start'], metas[1]['evaluation_start'])
                self.assertEqual(metas[0]['evaluation_end'], metas[1]['evaluation_end'])
                columns = ['group', 'start', 'end', 'potential_component_ids', 'potential_bitmask', 'minutes']
                segments = [pd.read_csv(folder / 'overlap-segments.csv') for folder in (first, second)]
                pd.testing.assert_frame_equal(segments[0][columns], segments[1][columns])
                for segment in segments[0].itertuples():
                    self.assertGreaterEqual(pd.Timestamp(segment.start), pd.Timestamp(metas[0]['evaluation_start']))
                    self.assertLessEqual(pd.Timestamp(segment.end), pd.Timestamp(metas[0]['evaluation_end']))
                with np.load(first / 'minute-predictions.npz') as a, np.load(second / 'minute-predictions.npz') as b:
                    np.testing.assert_array_equal(a['decision_tick_ns'], b['decision_tick_ns'])
                    np.testing.assert_array_equal(a['source_eligible'], b['source_eligible'])
                    np.testing.assert_array_equal(a['common_eligible'], b['common_eligible'])
                    self.assertFalse(np.array_equal(a['prediction'], b['prediction'], equal_nan=True))


if __name__ == '__main__':
    unittest.main()
