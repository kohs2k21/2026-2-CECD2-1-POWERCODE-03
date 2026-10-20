"""Independent clocks, union credit and paired-support stress checks."""
import unittest
import numpy as np
import pandas as pd
from research.evaluation.trend_sr_comparison import dataset, scenarios, stress


def predictions(item):
    start, end = map(pd.Timestamp, (item.manifest['evaluation_start'], item.manifest['evaluation_end']))
    ticks = pd.date_range(start, end, freq='min', inclusive='left')
    return pd.concat([pd.DataFrame(dict(group=group, decision_tick=ticks, prediction=0.,
        source_eligible=True, common_eligible=True, score_supported=True)) for group in scenarios.GROUPS], ignore_index=True)


class StressContracts(unittest.TestCase):
    def test_all_recipes_share_full_potential_clock_even_zero_arm(self):
        cases = scenarios.compound_cases(9501, 'selection')
        self.assertEqual(len(cases), 16)
        for case in cases:
            stop = max(c.end for c in case.components)
            full = dataset.materialize_compound(case, case.arms[-1])
            zero = dataset.materialize_compound(case, '0')
            a, b = stress.crop(full, case.anchor, stop), stress.crop(zero, case.anchor, stop)
            pd.testing.assert_frame_equal(a.frame[['bin_start','group','available_at']], b.frame[['bin_start','group','available_at']])
            self.assertEqual(pd.Timestamp(a.manifest['evaluation_start']) - a.frame.bin_start.min(), pd.Timedelta(minutes=1442))
            result = stress.assess(a, predictions(a), potential_events=full.events)
            for group, rows in result['segments'].groupby('group'):
                self.assertEqual(rows.minutes.sum(), (pd.Timestamp(a.manifest['evaluation_end'])-pd.Timestamp(a.manifest['evaluation_start'])).total_seconds()/60)
                for row in rows.itertuples():
                    active = {c.label for c in case.components if c.group == group and c.start <= row.start < c.end}
                    self.assertEqual(set(row.potential_component_ids), active)
            expected = 2 if case.recipe in ('MX05','MX06') else 1
            self.assertEqual(len(result['union_truth']), expected)
            if case.recipe == 'MX08':
                self.assertIn(7, result['segments'].potential_bitmask.tolist())

    def test_binary_alarm_has_union_credit_and_no_component_onset_credit(self):
        case = next(c for c in scenarios.compound_cases(9501,'selection') if c.recipe == 'MX08')
        full = dataset.materialize_compound(case, 'ABC')
        item = stress.crop(full, case.anchor, max(c.end for c in case.components))
        frame = predictions(item)
        for event in item.events.itertuples():
            frame.loc[frame.group.eq(event.group) & frame.decision_tick.ge(event.start) & frame.decision_tick.lt(event.end), 'prediction'] = 1.
        result = stress.assess(item,frame)
        self.assertEqual(len(result['evaluation']['events']),1)
        self.assertEqual(len(result['components']),3)
        self.assertTrue(result['components'].onset_hit_credit.isna().all())
        self.assertFalse(result['components'].evidence_supported.any())
        union = result['union_truth'].iloc[0]
        self.assertEqual(union.end, max(c.end for c in case.components))

    def test_paired_support_intersection_and_different_clock_rejection(self):
        case = scenarios.compound_cases(9501,'selection')[0]
        full = dataset.materialize_compound(case, 'AB')
        item = stress.crop(full,case.anchor,max(c.end for c in case.components))
        a, b = predictions(item), predictions(item)
        target = a.group.eq(case.group) & a.decision_tick.ge(case.anchor) & a.decision_tick.lt(case.anchor+pd.Timedelta(minutes=5))
        b.loc[target,'prediction'] = 1.
        a.loc[target,'prediction'] = np.nan
        result = stress.paired_differences({'0':a,'AB':b},full.events,item.manifest['evaluation_start'],item.manifest['evaluation_end'])
        self.assertEqual(result.right_minus_left_positive_minutes.sum(),0)
        with self.assertRaises(ValueError):
            stress.paired_differences({'0':a,'AB':b.iloc[1:]},full.events,item.manifest['evaluation_start'],item.manifest['evaluation_end'])
        self.assertTrue(all(r['rejected'] for r in stress.invalid_request_checks()))

    def test_quality_cross_keeps_service_and_source_failures_orthogonal(self):
        arms = stress._quality_arms(9501,'selection',False)
        self.assertEqual(set(arms),{'0','S','Q','SQ'})
        self.assertEqual(len(arms['0'].events),0)
        self.assertEqual(len(arms['Q'].events),0)
        pd.testing.assert_frame_equal(arms['S'].quality,arms['0'].quality)
        pd.testing.assert_frame_equal(arms['SQ'].quality,arms['Q'].quality)
        measured = arms['Q'].frame.source_complete.to_numpy(bool)
        pd.testing.assert_frame_equal(arms['Q'].frame.loc[measured,['count','duration_ms','total_count','error_count']], arms['0'].frame.loc[measured,['count','duration_ms','total_count','error_count']])


if __name__ == '__main__': unittest.main()
