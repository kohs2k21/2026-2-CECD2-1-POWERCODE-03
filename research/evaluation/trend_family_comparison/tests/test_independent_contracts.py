"""Independent hand-computed contracts; no main-seed data or forest fitting."""
import copy
import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

from research.evaluation.long_term_trend import evaluation as ev
from research.evaluation.long_term_trend.features import BASE_NAMES, TREND_NAMES
from research.evaluation.long_term_trend.models import StatisticalReference
from research.evaluation.trend_family_comparison import detectors as d


NAMES = BASE_NAMES + TREND_NAMES


def metadata(size, groups=('continuous',)):
    return pd.concat([pd.DataFrame({
        'row_id': [f'{group}|{i}' for i in range(size)], 'group': group,
        'decision_tick': pd.date_range('2026-01-01', periods=size, freq='min', tz='UTC'),
        'state': 'ready', 'eligible': True, 'source_known': True, 'train_mask': True,
    }) for group in groups], ignore_index=True)


def protocol():
    return {'groups': ['continuous'], 'cadence_candidates': [1,5,15],
            'alarm_merge_gap_minutes': 10, 'calibration_alarm_budget_per_group_hour': .5,
            'calibration_positive_tick_fraction_per_group': .05, 'training_cap': 4096}


class IndependentOrAndBudget(unittest.TestCase):
    def test_ternary_or_truth_table_and_strict_ties(self):
        a = np.array([2,0,0,np.nan,np.nan,1,np.nan], float)
        b = np.array([np.nan,2,0,0,np.nan,1,2], float)
        actual = d.or_flags({'a':a,'b':b}, [
            {'score_key':'a','threshold':1}, {'score_key':'b','threshold':1}])
        np.testing.assert_equal(actual, [1,1,0,np.nan,np.nan,0,1])

    def test_known_count_positive_survives_unsupported_relation(self):
        actual = d.or_flags({'count':np.array([4.,0.]),'relation':np.array([np.nan,np.nan])}, [
            {'score_key':'count','threshold':3}, {'score_key':'relation','threshold':2}])
        np.testing.assert_equal(actual, [1,np.nan])

    def test_ineligible_arm_is_entirely_unknown(self):
        actual = d.arm_flags({'a':np.array([0.,4.])}, {'eligible':False,'branches':[]})
        self.assertTrue(np.isnan(actual).all())

    def test_actual_union_checks_all_cadences(self):
        md = metadata(150); flags = np.zeros(150); flags[0] = 1
        result = d.guard_flags(flags, md, protocol())
        rows = {r['cadence']:r for r in result['groups']}
        self.assertEqual(set(rows), {1,5,15})
        self.assertEqual(rows[5]['positive_normal_minutes'], 5)
        self.assertAlmostEqual(rows[5]['normal_monitored_hours'], 2.5)
        self.assertAlmostEqual(rows[5]['false_alarm_episodes_per_hour'], .4)
        self.assertTrue(rows[5]['passed'])
        self.assertFalse(rows[15]['passed'])
        self.assertEqual(rows[15]['positive_normal_minutes'], 15)
        self.assertFalse(result['passed'])

    def test_conditional_exposure_and_common_exposure_are_distinct(self):
        flags = np.r_[np.full(30,np.nan),np.zeros(30)]
        result = d.guard_flags(flags, metadata(60), protocol())
        row = next(r for r in result['groups'] if r['cadence'] == 1)
        self.assertAlmostEqual(row['normal_monitored_hours'], .5)
        self.assertAlmostEqual(row['normal_common_eligible_hours'], 1.)
        self.assertEqual(row['false_episodes_per_common_eligible_hour'], 0)

    def test_missing_required_group_cannot_pass(self):
        p = protocol(); p['groups'] = ['continuous','business']
        self.assertFalse(d.guard_flags(np.zeros(60), metadata(60), p)['passed'])

    def test_source_unknown_cancels_held_positive_without_resuming(self):
        md = metadata(10); md.loc[2,'eligible'] = False
        flags = np.zeros(10); flags[0] = 1
        intervals = ev.decision_intervals(md, ev.decisions(flags,md,5,.5), 5)
        self.assertEqual(intervals.positive.tolist(), [True,True,False,False,False,False,False,False,False,False])
        self.assertEqual(intervals.prediction_available.tolist(), [True,True,False,False,False,True,True,True,True,True])

    def test_counterfactual_relation_window_contributes_false_alarms(self):
        md = metadata(120); flags = np.zeros(120); flags[[10,35]] = 1
        intervals = ev.decision_intervals(md, ev.decisions(flags,md,1,.5), 1)
        episodes = ev.alarm_episodes(intervals,10)
        events = pd.DataFrame([
            {'group':'continuous','start':md.decision_tick[10],'end':md.decision_tick[20]},
            {'group':'continuous','start':md.decision_tick[80],'end':md.decision_tick[90]}])
        actual = ev.normal_statistics(intervals,episodes,events)[0]
        cf = ev.normal_statistics(intervals,episodes,events.iloc[1:])[0]
        self.assertEqual(actual['false_alarm_episodes'], 1)
        self.assertEqual(cf['false_alarm_episodes'], 2)
        self.assertEqual(actual['positive_normal_minutes'], 1)
        self.assertEqual(cf['positive_normal_minutes'], 2)
        self.assertAlmostEqual(cf['normal_common_eligible_hours'], 110/60)


class IndependentFeatureAndFit(unittest.TestCase):
    def test_family_partition_matches_fixed_physical_contract(self):
        families = d.family_indices(NAMES)
        self.assertEqual({k:len(v) for k,v in families.items()},
                         {'sr_count':18,'sr_latency':17,'sr_relation':2})
        self.assertEqual({NAMES[i] for i in families['sr_relation']},
                         {'count_duration_correlation','relation_absolute_residual'})
        self.assertIn(NAMES.index('count_tail_mean_W'), families['sr_count'])
        indices = [i for values in families.values() for i in values]
        self.assertEqual(len(indices), len(set(indices)))
        self.assertEqual(set(indices), set(range(43)) - {17,18,19,20,41,42})

    def test_family_masks_and_clock_exclusion(self):
        active = np.ones(43,bool); active[[17,41,42]] = False
        stats = StatisticalReference(np.zeros(43),np.ones(43),active,NAMES)
        X = np.zeros((3,43)); X[:,0] = 5; X[:,18] = 100
        valid = np.ones_like(X,bool); valid[:,[17,41,42]] = False
        valid[0,[39,40]] = False; valid[2,40] = False
        md = metadata(3); md.loc[1,'eligible'] = False
        raw, expected = d.score_detectors({'statistics':stats,'families':d.family_indices(NAMES),'forests':{}},
            {'X':{'trend':X},'feature_validity':{'trend':valid}},md)
        np.testing.assert_equal(raw['old_sr'], [100,np.nan,100])
        np.testing.assert_equal(raw['sr_count'], [5,np.nan,5])
        self.assertTrue(np.isnan(raw['sr_relation']).all())
        self.assertFalse(expected['sr_relation'].any())

    def test_unexpected_nan_from_supported_model_raises(self):
        active = np.ones(43,bool); active[[17,41,42]] = False
        stats = StatisticalReference(np.zeros(43),np.ones(43),active,NAMES)
        fake = SimpleNamespace(score_samples=lambda X:np.full(len(X),np.nan))
        encoder = SimpleNamespace(transform=lambda X:X)
        artifact = {'statistics':stats,'families':d.family_indices(NAMES),
                    'forests':{'if43':{'columns':list(range(43)),'relation_only':False,'status':'ok','model':fake,'encoder':encoder}}}
        pk = {'X':{'trend':np.zeros((2,43))},'feature_validity':{'trend':np.ones((2,43),bool)}}
        with self.assertRaisesRegex(ValueError,'Unexpected missing score'):
            d.score_detectors(artifact,pk,metadata(2))

    def test_fit_uses_train_mask_c5_only_and_separate_relation_cohort(self):
        md = metadata(3000); md.loc[:499,'train_mask'] = False
        X = np.sin(np.arange(3000)[:,None]/17 + np.arange(43)[None,:])
        X[:500] = 1e6
        valid = np.ones_like(X,bool); valid[:,[17,41,42]] = False
        valid[np.arange(3000)%25 == 0,39:41] = False
        pk = {'X':{'trend':X},'feature_validity':{'trend':valid},'feature_names':{'trend':NAMES}}
        calls = []
        def fake_fit(algorithm, seed, values):
            calls.append((algorithm,seed,values.shape))
            return SimpleNamespace()
        with patch.object(d,'fit_forest',side_effect=fake_fit), patch.object(d,'forest_score_upper_bound',return_value=.9):
            artifact = d.fit_detectors(pk,md,9102,protocol())
        self.assertEqual(artifact['fits_completed'], 4)
        self.assertEqual([shape for _,_,shape in calls], [(500,43),(500,43),(400,2),(400,2)])
        eligible = np.arange(500,3000,5)
        relation = eligible[eligible%25 != 0]
        for key, indices in [('if43',eligible),('ifrel2',relation)]:
            wanted = hashlib.sha256('\n'.join(md.iloc[indices].row_id).encode()).hexdigest()
            self.assertEqual(artifact['forests'][key]['training_row_id_sha256'], wanted)
        np.testing.assert_allclose(artifact['statistics'].median[0], np.median(X[eligible,0]))
        np.testing.assert_allclose(artifact['forests']['ifrel2']['encoder'].median,
                                   np.median(X[relation][:,[39,40]],axis=0))


if __name__ == '__main__':
    unittest.main()
