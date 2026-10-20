"""Independent causal evidence contracts; artificial inputs, no model fit/score."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

from research.evaluation.trend_recovery_comparison import datahelper, signals


def artificial(n=110):
    ticks = pd.date_range('2026-07-06', periods=n, freq='min', tz='UTC')
    counts = 100.5 + np.tile(np.arange(-10, 11), 6)[:n]
    source = pd.DataFrame(dict(bin_start=ticks, endpoint=ticks+pd.Timedelta(minutes=1),
        available_at=ticks+pd.Timedelta(minutes=1), group='continuous', adapter='a',
        interface='i', count=counts, duration_ms=counts*10, status='S',
        source_complete=True, coverage_known=True, minute_index=np.arange(n),
        split='blind', seed=9102))
    def table(values):
        return dict(values=values, median=float(np.median(values)),
            scale=float(np.quantile(values,.9)-np.quantile(values,.1)),
            q05=float(np.quantile(values,.05)), q95=float(np.quantile(values,.95)))
    reference = dict(protocol=dict(watermark_lag_minutes=2, minimum_relation_pairs=30),
        registry={'aliases':{json.dumps(['a','i']):'continuous'}}, groups={'continuous':{
            'count':{'pooled':table(np.arange(1.,201.))},
            'duration_ms':{'pooled':table(np.arange(1.,201.)*10)},
            'relation':dict(intercept=0., beta=1., scale=1.)}})
    stats = SimpleNamespace(names=['count_duration_correlation','relation_absolute_residual'],
        median=np.array([1.,0.]), scale=np.array([1.,1.]))
    item = dict(stream=source, manifest=dict(scored_start=ticks[0].isoformat(),
        scored_end=(ticks[-1]+pd.Timedelta(minutes=1)).isoformat()))
    return item, reference, stats


class SignalContracts(unittest.TestCase):
    def setUp(self):
        self.item, self.reference, self.stats = artificial()
        self.spec = datahelper.load_protocol()['policies']['fast']

    def build(self, item=None, reference=None, spec=None):
        return signals.build_signals(item or self.item, reference or self.reference,
            self.stats, spec or self.spec, 2.5)

    def test_window_boundary_lag_and_full_pairs(self):
        result=self.build()
        self.assertFalse(result['count']['supported'][6])
        self.assertTrue(result['count']['supported'][7])
        self.assertFalse(result['relation']['supported'][31])
        self.assertTrue(result['relation']['supported'][32])
        self.assertEqual(result['relation']['pair_count'][32],30)
        self.assertAlmostEqual(result['relation']['correlation'][32],1.)
        self.assertAlmostEqual(result['relation']['absolute_residual'][32],0.)
        self.assertTrue(result['relation']['quiet'][32])
        tick=pd.Timestamp(result['decision_tick'][53])
        self.assertEqual(result['count']['window_end'][53],tick-pd.Timedelta(minutes=2))
        self.assertEqual(result['count']['window_start'][53],tick-pd.Timedelta(minutes=7))

    def test_strong_is_one_bin_direction_without_persistence(self):
        self.item['stream'].loc[50,['count','duration_ms']]=[300.,3000.]
        result=self.build()
        self.assertFalse(result['count']['strong'][52])
        self.assertEqual(result['count']['direction'][53],1)
        self.assertTrue(result['count']['strong'][53])
        self.assertFalse(result['count']['strong'][54])
        self.assertFalse(result['count']['quiet'][57])
        self.assertTrue(result['count']['quiet'][58])
        self.item['stream'].loc[50,'count']=0.
        result=self.build()
        self.assertEqual(result['count']['direction'][53],-1)
        self.assertFalse(result['count']['quiet'][53])
        self.assertFalse(result['latency']['supported'][53])

    def test_delayed_bin_is_unknown_until_window_excludes_it(self):
        self.item['stream'].loc[50,'available_at'] += pd.Timedelta(minutes=10)
        result=self.build()
        self.assertTrue(result['count']['supported'][52])
        self.assertTrue(np.all(~result['count']['supported'][53:58]))
        self.assertTrue(result['count']['supported'][58])
        self.assertTrue(np.all(~result['count']['quiet'][53:58]))
        self.assertTrue(np.all(~result['count']['strong'][53:58]))

    def test_missing_coverage_and_category_cannot_confirm_normal(self):
        for column,value in [('source_complete',False),('coverage_known',False),('adapter','unseen')]:
            changed=copy.deepcopy(self.item)
            changed['stream'].loc[50,column]=value
            result=self.build(changed)
            for family in ('count','latency','relation'):
                self.assertFalse(result[family]['supported'][53],column)
                self.assertFalse(result[family]['quiet'][53],column)
                self.assertFalse(result[family]['strong'][53],column)

    def test_holiday_reference_cannot_supply_activity_evidence(self):
        reference=copy.deepcopy(self.reference)
        table=reference['groups']['continuous']['count']['pooled']
        table.update(q05=0.,q95=0.)
        result=self.build(reference=reference)
        for family in ('count','latency','relation'):
            self.assertFalse(result[family]['supported'][70])
            self.assertEqual(result[family]['reason'][70],'expected_inactive_or_uncertain')

    def test_full_45_pairs_required_and_constant_relation_unsupported(self):
        conservative=datahelper.load_protocol()['policies']['conservative']
        result=self.build(spec=conservative)
        self.assertFalse(result['relation']['supported'][46])
        self.assertTrue(result['relation']['supported'][47])
        self.item['stream'].loc[20,'duration_ms']=np.nan
        result=self.build(spec=conservative)
        self.assertFalse(result['relation']['supported'][64])
        self.assertTrue(result['relation']['supported'][68])
        self.item['stream']['count']=100.5
        self.item['stream']['duration_ms']=1005.
        result=self.build()
        self.assertTrue(result['count']['quiet'][70])
        self.assertFalse(result['relation']['supported'][70])

    def test_anti_relation_is_strong_using_frozen_sr_units(self):
        counts=self.item['stream']['count'].to_numpy()
        self.item['stream']['duration_ms']=(201.-counts)*10
        # corr=-1 versus median+1 gives deviation2; raw cut1.5.
        result=signals.build_signals(self.item,self.reference,self.stats,self.spec,1.5)
        self.assertAlmostEqual(result['relation']['deviation'][70],2.)
        self.assertTrue(result['relation']['strong'][70])
        self.assertFalse(result['relation']['quiet'][70])

    def test_truth_and_future_do_not_affect_past_evidence(self):
        baseline=self.build()
        changed=copy.deepcopy(self.item)
        changed['events']='not a policy input'
        changed['stream']['active']=True
        changed['stream']['normal_confirmed']=False
        changed['stream']['status']='error'
        changed['stream'].loc[80:,['count','duration_ms']]=[999.,99999.]
        result=self.build(changed)
        for family in ('count','latency','relation'):
            for key in ('quiet','strong','supported','direction','reason'):
                np.testing.assert_array_equal(baseline[family][key][:83],result[family][key][:83])

    def test_threshold_derivation_is_linear_and_reference_immutable(self):
        before=copy.deepcopy(self.reference)
        manifest=signals.threshold_manifest(self.reference)
        table=manifest['groups']['continuous']['count']['pooled']
        self.assertAlmostEqual(table['q01'],2.99)
        self.assertAlmostEqual(table['q99'],198.01)
        self.build()
        np.testing.assert_array_equal(before['groups']['continuous']['count']['pooled']['values'],
            self.reference['groups']['continuous']['count']['pooled']['values'])
        self.assertEqual(signals.threshold_manifest(self.reference)['sha256'],manifest['sha256'])


class DataGates(unittest.TestCase):
    def test_final_requires_go_before_generator(self):
        with patch.object(datahelper.legacy,'load_split',side_effect=AssertionError('generator ran')) as generator:
            for seed in (201,237,273):
                with self.assertRaisesRegex(ValueError,'blocked'):
                    datahelper.load_item(seed,'final')
            self.assertEqual(generator.call_count,0)

    def test_replay_requires_pinned_sha_and_declared_seed(self):
        with patch.object(datahelper.legacy,'load_split',side_effect=AssertionError('generator ran')) as generator:
            for seed,phase in [(9102,'fixture'),(101,'development')]:
                with self.assertRaisesRegex(ValueError,'SHA'):
                    datahelper.load_item(seed,phase)
            for seed,phase in [(201,'development'),(101,'fixture'),(201,'oops')]:
                with self.assertRaises(ValueError):datahelper.load_item(seed,phase)
            self.assertEqual(generator.call_count,0)

    def test_final_period_or_mapping_mutation_blocked(self):
        p=datahelper.load_protocol()
        for field,value in [('days',15),('scored_start_utc','2026-07-06T00:00:00Z'),
                            ('data_seed_to_model_seed',{'201':137,'237':101,'273':173}),('default_blocked',False)]:
            changed=copy.deepcopy(p);changed['final'][field]=value
            with patch.object(datahelper.legacy,'load_split',side_effect=AssertionError('generator ran')) as generator:
                with self.assertRaises(ValueError):datahelper.load_item(201,'final',allow_final=True,protocol=changed)
                self.assertEqual(generator.call_count,0)

    def test_train_and_final_calibration_and_normal_cf_blocked(self):
        with patch.object(datahelper.legacy,'load_split',side_effect=AssertionError('generator ran')) as generator:
            for seed,phase,split,intervention in [(9102,'fixture','train',True),
                    (201,'final','calibration',True),(9102,'fixture','calibration',False)]:
                with self.assertRaises(ValueError):
                    datahelper.load_item(seed,phase,split=split,relation_intervention=intervention,
                        allow_final=True,expected_source_sha256='unused')
            self.assertEqual(generator.call_count,0)


if __name__=='__main__':
    unittest.main()
