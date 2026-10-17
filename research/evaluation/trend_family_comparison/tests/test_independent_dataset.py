"""Fresh split and same-RNG contracts using fixture9102 only."""
import copy
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from research.evaluation.long_term_trend import features
from research.evaluation.trend_family_comparison import dataset as data


class IndependentDatasetGate(unittest.TestCase):
    def test_main_blind_actual_and_control_block_before_generator(self):
        p = data.load_protocol()
        with patch.object(data.legacy,'load_split') as generate:
            for seed in [101,137,173]:
                for intervention in [True,False]:
                    with self.assertRaisesRegex(ValueError,'Main blind blocked'):
                        data.load_split(seed,'blind',p,relation_intervention=intervention)
            generate.assert_not_called()

    def test_unknown_seed_or_split_rejected_before_generator(self):
        p = data.fixture_protocol()
        with patch.object(data.legacy,'load_split') as generate:
            for seed in [11,29,47,101,137,173,9101,9999]:
                with self.assertRaises(ValueError): data.load_split(seed,'train',p)
            with self.assertRaises(ValueError): data.load_split(9102,'selection',p)
            generate.assert_not_called()

    def test_hash_mismatch_fails_before_source_generation(self):
        p = data.fixture_protocol()
        key = next(iter(p['legacy_source_sha256']))
        p['legacy_source_sha256'][key] = '0'*64
        with patch.object(data.legacy,'load_split') as generate:
            with self.assertRaisesRegex(ValueError,'Pinned legacy source changed'):
                data.load_split(9102,'train',p)
            generate.assert_not_called()

    def test_protocol_has_new_seeds_nonoverlap_and_exact_family_sets(self):
        p = data.load_protocol()
        self.assertFalse(set(p['seeds']) & {11,29,47,9101,9102})
        spans = [(pd.Timestamp(p['scored_starts_utc'][s]),pd.Timestamp(p['scored_starts_utc'][s])+pd.Timedelta(days=p['split_days'][s])) for s in ['train','calibration','blind']]
        self.assertTrue(all(a[1] <= b[0] for a,b in zip(spans,spans[1:])))
        self.assertEqual([a[0].weekday() for a in spans], [0,0,0])
        families = list(p['family_feature_names'].values())
        flat = [name for family in families for name in family]
        self.assertEqual(len(set(flat)), 37)
        self.assertEqual(set(flat) | set(p['excluded_family_feature_names']),set(p['feature_contract']['names']))


class IndependentFixtureCounterfactual(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = data.fixture_protocol()
        cls.train = data.load_split(9102,'train',cls.p)
        cls.actual = data.load_split(9102,'blind',cls.p)
        cls.control = data.load_split(9102,'blind',cls.p,relation_intervention=False)
        cls.reference = features.fit_reference(cls.train['stream'],cls.p)
        cls.a = features.build_features(cls.actual['stream'],cls.reference,1,1)
        cls.c = features.build_features(cls.control['stream'],cls.reference,1,1)

    def test_event_denominators_and_paired_ids_are_separate(self):
        self.assertEqual(len(self.actual['events']), 20)
        self.assertEqual(len(self.control['events']), 16)
        self.assertEqual(len(self.control['control_windows']), 4)
        self.assertEqual(set(self.actual['events'].event_id),set(self.control['events'].event_id) | set(self.control['control_windows'].event_id))
        self.assertFalse(self.control['events'].scenario.eq('relation_change').any())
        self.assertEqual(set(self.actual['control_windows'].event_id),set(self.control['control_windows'].event_id))

    def test_counterfactual_preserves_every_other_source_and_hour_multiset(self):
        actual, control = self.actual['stream'], self.control['stream']
        pd.testing.assert_frame_equal(actual.drop(columns='duration_ms'),control.drop(columns='duration_ms'))
        inside = np.zeros(len(actual),bool)
        for event in self.actual['control_windows'].itertuples():
            mask = (actual.group == event.group) & (actual.bin_start >= event.start) & (actual.bin_start < event.end)
            inside |= mask.to_numpy()
            for _, hour in actual.loc[mask].groupby(actual.loc[mask,'bin_start'].dt.floor('h')):
                np.testing.assert_array_equal(np.sort(hour.duration_ms),np.sort(control.loc[hour.index,'duration_ms']))
        pd.testing.assert_series_equal(actual.loc[~inside,'duration_ms'],control.loc[~inside,'duration_ms'])
        self.assertGreater(np.nanmax(np.abs(actual.duration_ms.to_numpy()-control.duration_ms.to_numpy())),0)

    def test_metadata_and_validity_match_and_count_features_are_unchanged(self):
        pd.testing.assert_frame_equal(self.a['metadata'],self.c['metadata'])
        np.testing.assert_array_equal(self.a['feature_validity']['trend'],self.c['feature_validity']['trend'])
        names = self.a['feature_names']['trend']
        count = [names.index(n) for n in self.p['family_feature_names']['count']]
        np.testing.assert_equal(self.a['X']['trend'][:,count],self.c['X']['trend'][:,count])
        ready = self.a['metadata'].ready.to_numpy(bool)
        corr = names.index('count_duration_correlation')
        self.assertGreater(np.max(np.abs(self.a['X']['trend'][ready,corr]-self.c['X']['trend'][ready,corr])),.2)

    def test_holdout_reference_fit_is_rejected(self):
        for item in [self.actual,self.control]:
            with self.assertRaisesRegex(ValueError,'train split only'):
                features.fit_reference(item['stream'],self.p)

    def test_window_contract_includes_exact_sixty_bins_and_common720(self):
        md = self.a['metadata']; ready = md.ready
        spans = pd.to_datetime(md.window_end)-pd.to_datetime(md.window_start)
        self.assertTrue((spans[ready] == pd.Timedelta(minutes=60)).all())
        self.assertTrue(((pd.to_datetime(md.decision_tick)-pd.to_datetime(md.window_end))[ready] == pd.Timedelta(minutes=2)).all())
        self.assertEqual(self.p['max_window_minutes'],720)
        self.assertEqual(self.p['prefix_minutes'],722)


if __name__ == '__main__': unittest.main()
