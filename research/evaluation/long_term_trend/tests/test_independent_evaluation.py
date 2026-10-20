"""Reviewer-owned counterexamples; no benchmark data or forest fit."""
import sys,unittest,pickle
from pathlib import Path
import numpy as np
import pandas as pd
REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from research.evaluation.long_term_trend import evaluation as ev
from research.evaluation.long_term_trend.models import RobustEncoder,StatisticalReference

def timeline(n=60):
 t=pd.date_range('2026-01-01',periods=n,freq='min',tz='UTC')
 return pd.DataFrame({'row_id':[str(i) for i in range(n)],'group':'g','decision_tick':t,'state':'READY','eligible':True,'source_known':True})
def event(meta,start=10,end=30,confirmed=20,ident='a'):
 t=meta.decision_tick.iloc[0]
 return {'event_id':ident,'group':'g','start':t+pd.Timedelta(minutes=start),'end':t+pd.Timedelta(minutes=end),'confirmed_start':t+pd.Timedelta(minutes=confirmed),'scenario':'gradual','cohort':'core'}
P={'alarm_merge_gap_minutes':10,'bounded_delay_minutes':360,'calibration_positive_tick_fraction_per_group':.01,'calibration_alarm_budget_per_group_hour':.05,'cadence_candidates':[1,5,15]}

class IndependentEvaluation(unittest.TestCase):
 def run_case(self,scores,meta,events,C=1):return ev.evaluate(scores,.5,meta,pd.DataFrame(events),P,C)
 def test_preexisting_cannot_credit_new_event(self):
  m=timeline();s=np.ones(60);summary,records,_,episodes=self.run_case(s,m,[event(m)])
  self.assertEqual(len(episodes),1);self.assertEqual(summary['hits'],0);self.assertTrue(records[0]['preexisting_only'])
 def test_new_actual_onset_and_early_subset(self):
  m=timeline();s=np.zeros(60);s[15]=1;summary,records,_,_=self.run_case(s,m,[event(m)])
  self.assertEqual(summary['hits'],1);self.assertEqual(summary['early_hits'],1);self.assertEqual(records[0]['delay_minutes'],5)
 def test_overlap_one_episode_only_one_event(self):
  m=timeline();s=np.zeros(60);s[15]=1;a=event(m,10,30,10,'a');b=event(m,12,25,12,'b')
  summary,records,_,_=self.run_case(s,m,[a,b]);self.assertEqual(summary['hits'],1);self.assertEqual(sum(r['hit'] for r in records),1)
 def test_cadence15_contiguous_positive_one_episode(self):
  m=timeline(45);s=np.ones(45);summary,_,_,episodes=self.run_case(s,m,[event(m)],15)
  self.assertEqual(len(episodes),1);self.assertEqual(episodes.positive_minutes.iloc[0],45);self.assertEqual(episodes.start.iloc[0],m.decision_tick.iloc[0]);self.assertEqual(summary['hits'],0)
 def test_unknown_cancels_hold_until_new_decision(self):
  m=timeline(45);m.loc[5:9,['eligible','source_known']]=False;m.loc[5:9,'state']='SOURCE_UNKNOWN';s=np.ones(45)
  issued=ev.decisions(s,m,15,.5);interval=ev.decision_intervals(m,issued,15);episodes=ev.alarm_episodes(interval,10)
  self.assertEqual(interval.positive.iloc[:5].sum(),5);self.assertFalse(interval.prediction_available.iloc[5:15].any());self.assertEqual(len(episodes),2);self.assertEqual(episodes.start.iloc[1],m.decision_tick.iloc[15])
 def test_known_negative_gap_exact10_vs11(self):
  m=timeline();s=np.zeros(60);s[[0,11]]=1;d=ev.decisions(s,m,1,.5)
  self.assertEqual(len(ev.alarm_episodes(ev.decision_intervals(m,d,1),10)),1)
  s=np.zeros(60);s[[0,12]]=1;d=ev.decisions(s,m,1,.5)
  self.assertEqual(len(ev.alarm_episodes(ev.decision_intervals(m,d,1),10)),2)
 def test_late_alarm_counts_fp_and_recovery_time(self):
  m=timeline(40);s=np.zeros(40);s[25]=1;summary,records,_,_=self.run_case(s,m,[event(m,10,20,10)])
  self.assertEqual(summary['hits'],0);self.assertEqual(records[0]['late_episodes'],1);r=summary['normal'][0]
  self.assertEqual(r['false_alarm_episodes'],1);self.assertEqual(r['positive_normal_minutes'],1);self.assertEqual(r['normal_monitored_hours'],.5)
 def test_all_unassessed_remains_in_event_denominator(self):
  m=timeline();m['eligible']=False;m['state']='NEW_CATEGORY';s=np.full(60,np.nan)
  summary,records,_,_=self.run_case(s,m,[event(m)]);self.assertEqual(summary['events'],1);self.assertEqual(summary['hits'],0);self.assertEqual(summary['unassessed_events'],1);self.assertTrue(records[0]['unassessed']);self.assertEqual(records[0]['bounded_delay_minutes'],360)
 def test_missing_model_score_is_not_normal_zero(self):
  m=timeline();s=np.zeros(60);s[15]=np.nan;summary,_,issued,_=self.run_case(s,m,[event(m)])
  self.assertFalse(summary['eligible_scoring_complete']);self.assertEqual(summary['scored_eligible_rows'],59);self.assertEqual(issued.loc[15,'state'],'MODEL_NOT_READY');self.assertTrue(pd.isna(issued.loc[15,'prediction']))
 def test_strict_threshold_tie_is_negative(self):
  m=timeline();issued=ev.decisions(np.full(60,.5),m,5,.5);self.assertFalse(issued.prediction.any())
 def test_calibration_envelope_actual_time_budget(self):
  m=timeline(2016);s=np.sin(np.arange(len(m))*.23)+np.arange(len(m))*1e-7;result=ev.calibrate(s,m,P)
  self.assertLessEqual(len(result['candidate_checks']),8)
  for C in (1,5,15):
   issued=ev.decisions(s,m,C,result['threshold']);interval=ev.decision_intervals(m,issued,C);episodes=ev.alarm_episodes(interval,10)
   minutes=int(interval.prediction_available.sum());positive=int(interval.positive.sum())
   self.assertLessEqual(positive/minutes,.01+1e-12);self.assertLessEqual(len(episodes)/(minutes/60),.05+1e-12)
  constant=ev.calibrate(np.zeros(len(m)),m,P);self.assertEqual(constant['threshold'],0)

class IndependentStatistics(unittest.TestCase):
 def test_sr_exact_numeric_validity_and_no_saturation(self):
  X=np.array([[0,0,1],[10,100,1],[20,200,1],[30,300,1]],float);v=np.ones_like(X,bool);v[0,1]=False
  ref=StatisticalReference.fit(X,v,['physical_a','physical_b','duration_observed'],['duration_observed'])
  np.testing.assert_allclose(ref.median[:2],[15,200]);np.testing.assert_allclose(ref.scale[:2],[24,160])
  score=ref.score(np.array([[39,2000,999],[15,360,999],[255,200,999]],float),np.array([[1,0,1],[1,1,1],[1,1,1]],bool))
  np.testing.assert_allclose(score,[1,1,10]);self.assertTrue(np.isnan(ref.score(np.zeros((1,3)),np.zeros((1,3),bool))[0]))
 def test_encoder_train_freeze_and_reject_bad_input(self):
  X=np.column_stack([np.arange(256),np.ones(256)]);ref=RobustEncoder.fit(X);before=pickle.dumps(ref);z=ref.transform(np.array([[10000.,1.]]))
  self.assertTrue(np.isfinite(z).all());self.assertEqual(before,pickle.dumps(ref))
  with self.assertRaises(ValueError):ref.transform(np.array([[np.nan,1.]]))
  with self.assertRaises(ValueError):RobustEncoder.fit(np.ones((256,2)))

if __name__=='__main__':unittest.main(verbosity=2)
