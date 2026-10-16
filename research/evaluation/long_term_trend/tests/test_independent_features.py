"""Reviewer-owned causal/eligibility checks using non-benchmark fixture seed."""
import sys,unittest,copy,pickle
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[4]))
from research.evaluation.long_term_trend import dataset as data,features as f

class IndependentFeatures(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.p=copy.deepcopy(data.load_protocol());cls.p['seeds']=[9101];cls.p['version']='review-fixture'
  cls.train=data.load_split(9101,'train',cls.p);cls.ref=f.fit_reference(cls.train['stream'],cls.p)
  cls.small=cls.train['stream'].loc[cls.train['stream'].group.eq('continuous')].head(900).copy().reset_index(drop=True)
  cls.pack=f.build_features(cls.small,cls.ref,1);cls.index=822
 def test_train_only_reference_and_frozen_transform(self):
  bad=self.small.copy();bad['split']='calibration'
  with self.assertRaises(ValueError):f.fit_reference(bad,self.p)
  before=pickle.dumps(self.ref);f.build_features(self.small,self.ref,12);self.assertEqual(before,pickle.dumps(self.ref))
 def test_dimensions_validity_and_zero_not_unknown(self):
  self.assertEqual(self.pack['X']['base'].shape,(900,31));self.assertEqual(self.pack['X']['trend'].shape,(900,43))
  z=self.small.copy();i=self.index;j=i-3;z.loc[j,'count']=0;z.loc[j,'duration_ms']=np.nan
  p=f.build_features(z,self.ref,1);self.assertTrue(p['metadata'].ready.iloc[i]);self.assertTrue(p['metadata'].known_zero.iloc[i]);self.assertEqual(p['metadata'].statecode.iloc[i],'ready_known_zero');self.assertTrue(np.isfinite(p['X']['trend'][i]).all());self.assertFalse(p['feature_validity']['trend'][i,7])
  z.loc[j,'source_complete']=False;z.loc[j,'count']=np.nan;p=f.build_features(z,self.ref,1)
  self.assertFalse(p['metadata'].ready.iloc[i]);self.assertFalse(p['metadata'].known_zero.iloc[i]);self.assertTrue(np.isnan(p['X']['trend'][i]).all())
 def test_exact_bin_end_window_and_two_minute_watermark(self):
  i=self.index;m=self.pack['metadata'].iloc[i];t=self.small.bin_start.iloc[i]
  self.assertEqual(m.window_end,t-pd.Timedelta(minutes=2));self.assertEqual(m.window_start,t-pd.Timedelta(minutes=62));self.assertEqual(m.source_available_bins,60);self.assertEqual(m.common_available_bins,720)
  included=self.small.endpoint.gt(m.window_start)&self.small.endpoint.le(m.window_end)
  self.assertEqual(np.flatnonzero(included).tolist(),list(range(i-62,i-2)))
  names=self.pack['feature_names']['base'];at=names.index('count_excess_max_W')
  outside=self.small.copy();outside.loc[i-63,'count']=1e9;p=f.build_features(outside,self.ref,1)
  np.testing.assert_allclose(p['X']['trend'][i],self.pack['X']['trend'][i],rtol=1e-5,atol=1e-5)
  inside=self.small.copy();inside.loc[i-62,'count']=1e9;p=f.build_features(inside,self.ref,1);self.assertGreater(p['X']['base'][i,at],1000)
  newest=self.small.copy();newest.loc[i-2:,'count']=1e9;p=f.build_features(newest,self.ref,1)
  np.testing.assert_array_equal(p['X']['trend'][i],self.pack['X']['trend'][i])
 def test_future_and_label_metadata_do_not_enter_past_X(self):
  i=self.index;changed=self.small.copy();changed.loc[i+1:,'count']=1e9;changed.loc[i+1:,'duration_ms']=1e9
  p=f.build_features(changed,self.ref,1);np.testing.assert_array_equal(p['X']['trend'][:i+1],self.pack['X']['trend'][:i+1])
  labels=self.small.copy();labels['active']=~labels.active;labels['normal_confirmed']=False;labels['status']='F';labels['lineage_id']='arbitrary-event-id'
  p=f.build_features(labels,self.ref,1);np.testing.assert_array_equal(p['X']['trend'],self.pack['X']['trend']);self.assertFalse(p['metadata'].train_mask.any())
 def test_late_payload_hidden_until_publication(self):
  i=self.index;j=i-5;t=self.small.bin_start.iloc[i];late=self.small.copy();late.loc[j,'available_at']=t+pd.Timedelta(minutes=20)
  a=f.build_features(late,self.ref,1);self.assertFalse(a['metadata'].ready.iloc[i]);self.assertEqual(a['metadata'].statecode.iloc[i],'observation_late')
  late.loc[j,'count']=1e8;late.loc[j,'duration_ms']=1e8;b=f.build_features(late,self.ref,1)
  np.testing.assert_array_equal(a['X']['trend'][:i+1],b['X']['trend'][:i+1]);self.assertTrue(b['metadata'].ready.iloc[i+20]);self.assertFalse(np.array_equal(a['X']['trend'][i+20],b['X']['trend'][i+20]))
 def test_alias_equivalence_and_unseen_not_ready(self):
  alias=self.small.copy();adapter,interface=self.ref['registry']['renamed']['continuous'];alias['adapter']=adapter;alias['interface']=interface
  p=f.build_features(alias,self.ref,1);np.testing.assert_array_equal(p['X']['trend'],self.pack['X']['trend'])
  alias.loc[self.index-3,'adapter']='genuinely-new';p=f.build_features(alias,self.ref,1)
  self.assertFalse(p['metadata'].ready.iloc[self.index]);self.assertEqual(p['metadata'].statecode.iloc[self.index],'category_unseen');self.assertTrue(np.isnan(p['X']['trend'][self.index]).all())
 def test_windows_share_full_cohort_and_cadence_does_not_change_X(self):
  for W in (1,6,12):
   p=f.build_features(self.small,self.ref,W);np.testing.assert_array_equal(p['metadata'].ready,self.pack['metadata'].ready);np.testing.assert_array_equal(p['metadata'].train_mask,self.pack['metadata'].train_mask)
  p=f.build_features(self.small,self.ref,1,15);np.testing.assert_array_equal(p['X']['trend'],self.pack['X']['trend']);np.testing.assert_array_equal(p['metadata'].row_id,self.pack['metadata'].row_id)
 def test_missing_bin_rejected_not_implicit_zero(self):
  missing=self.small.drop(index=800)
  with self.assertRaises(ValueError):f.build_features(missing,self.ref,1)
 def test_other_group_values_cannot_affect_reference_fixed_X(self):
  baseline=f.build_features(self.train['stream'],self.ref,1);changed=self.train['stream'].copy();changed.loc[changed.group.eq('business'),'count']*=100
  p=f.build_features(changed,self.ref,1);mask=baseline['metadata'].group.eq('continuous').to_numpy();np.testing.assert_array_equal(p['X']['trend'][mask],baseline['X']['trend'][mask])
 def test_generator_truth_and_quality_cohorts(self):
  item=data.load_split(9101,'selection',self.p);e=item['events'];self.assertEqual(len(e),20);self.assertEqual(e.event_id.nunique(),20);self.assertEqual(e.scenario.nunique(),5)
  self.assertEqual(e.cohort.value_counts().to_dict(),{'core':17,'source_unknown':1,'source_late':1,'new_category':1})
  relation=e[e.scenario.eq('relation_change')];self.assertTrue((relation.relation_marginal_before_sha256==relation.relation_marginal_after_sha256).all());self.assertTrue(item['stream'].status.eq('S').all())
  self.assertTrue((e.first_bin_endpoint-e.start).eq(pd.Timedelta(minutes=1)).all());self.assertTrue((e.end-e.start).dt.total_seconds().eq(e.duration_minutes*60).all());self.assertTrue((e.confirmed_start>=e.start).all());self.assertTrue((e.confirmed_start<e.end).all())
 def test_independent_split_rng_deterministic_without_holdout_load(self):
  replay=data.load_split(9101,'train',self.p);self.assertEqual(replay['manifest']['source_sha256'],self.train['manifest']['source_sha256']);self.assertEqual(len(self.train['events']),0)

if __name__=='__main__':unittest.main(verbosity=2)
