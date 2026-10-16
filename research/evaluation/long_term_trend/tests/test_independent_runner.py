"""Reviewer-owned no-model failure/phase-boundary tests, no benchmark holdout."""
import sys,unittest,json,tempfile,os
from pathlib import Path
from unittest.mock import patch
import numpy as np,pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[4]))
from research.evaluation.long_term_trend import run_experiment as r
OUT=Path(os.environ.get('TREND54_TEST_OUTPUT',tempfile.gettempdir()))

def fixture():
 t=pd.date_range('2025-01-01',periods=30,freq='min',tz='UTC');frame=pd.DataFrame({'bin_start':t,'endpoint':t+pd.Timedelta(minutes=1),'available_at':t+pd.Timedelta(minutes=1),'source_complete':True,'count':1.,'duration_ms':100.,'adapter':'a','interface':'i','split':'review-fixture','seed':9101,'group':'g'})
 events=pd.DataFrame([{'event_id':'fixture-a','group':'g','start':t[10],'end':t[20],'confirmed_start':t[15],'scenario':'fixture','cohort':'core'}])
 return {'stream':frame,'events':events,'registry':{'aliases':{json.dumps(['a','i'],ensure_ascii=False):'g'}},'manifest':{'scored_start':t[0].isoformat(),'scored_end':(t[-1]+pd.Timedelta(minutes=1)).isoformat(),'source_sha256':'fixture-only'}}

class IndependentRunner(unittest.TestCase):
 def test_cli_dispatch_limits_actual_numeric_threads(self):
  from threadpoolctl import threadpool_info
  observed=[]
  def screen(path):observed.extend(threadpool_info())
  with patch.object(sys,'argv',['run_experiment','screen','--output',str(OUT/'not-created-by-mock')]),patch.object(r,'screen',side_effect=screen):r.main()
  self.assertTrue(observed);self.assertTrue(all(x['num_threads']==1 for x in observed))
 def test_all_artifacts_and_reference_absent_preserves_every_arm_event(self):
  item=fixture();p={'groups':['g'],'seeds':[9101],'cadence_candidates':[1,5,15],'watermark_lag_minutes':2,'bounded_delay_minutes':360};winners={a:{'variant':'trend' if a=='sr' else 'base','window_hours':1} for a in ('sr','if','eif')};branches={a:{'key':a+'-missing','available':False} for a in winners}
  arms={a:{'branches':[b],'threshold':0.,'selection_status':'ineligible_diagnostic_reference'} for a,b in branches.items()}
  for a in ('if','eif'):arms['sr+'+a]={'branches':[branches['sr'],branches[a]],'threshold':None,'selection_status':'ineligible_diagnostic_reference'}
  lock={'sources':{},'protocol':p,'seeds':[9101],'references':{},'artifacts':{},'winners':winners,'arms':{'9101':arms}}
  with tempfile.TemporaryDirectory(prefix='fault-fixture-',dir=OUT) as temp:
   root=Path(temp);screen=root/'screen';screen.mkdir();r.write_json(screen/'selection-lock.json',lock);r.write_json(screen/'manifest.json',{'selection_lock_sha256':r.digest(screen/'selection-lock.json')})
   def load(seed,name,protocol):
    self.assertEqual(seed,9101);self.assertEqual(name,'blind');return item
   with patch.object(r.dataset,'load_split',side_effect=load),patch.object(r,'source_hashes',return_value={}):r.holdout(screen,root/'output','blind')
   rows=json.loads((root/'output/events.json').read_text());self.assertEqual(len(rows),5)
   self.assertTrue(all(x['unassessed'] and not x['hit'] and x['bounded_delay_minutes']==360 for x in rows));manifest=json.loads((root/'output/manifest.json').read_text());self.assertEqual(manifest['new_fits'],0);self.assertEqual(manifest['recalibrations'],0);self.assertTrue(manifest['failures'])
 def test_missing_reference_flags_do_not_use_hidden_category(self):
  item=fixture();p={'watermark_lag_minutes':2};item['stream'].loc[10,'available_at']=item['stream'].bin_start.iloc[20]
  a=r.missing_reference_metadata(item,p);item['stream'].loc[10,'adapter']='unseen-secret';item['stream'].loc[10,'duration_ms']=1e9;b=r.missing_reference_metadata(item,p)
  pd.testing.assert_frame_equal(a.loc[:19],b.loc[:19])
 def test_source_hash_mismatch_fails_before_loading_holdout(self):
  with tempfile.TemporaryDirectory(prefix='tamper-fixture-',dir=OUT) as temp:
   root=Path(temp);screen=root/'screen';screen.mkdir();r.write_json(screen/'selection-lock.json',{'sources':{'a':'wrong'}});r.write_json(screen/'manifest.json',{'selection_lock_sha256':r.digest(screen/'selection-lock.json')})
   with patch.object(r.dataset,'load_split',side_effect=AssertionError('holdout opened before lock validation')):
    with self.assertRaisesRegex(ValueError,'Source mutation'):r.holdout(screen,root/'output','blind')

if __name__=='__main__':unittest.main(verbosity=2)
