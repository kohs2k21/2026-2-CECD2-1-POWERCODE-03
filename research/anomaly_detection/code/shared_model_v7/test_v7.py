import copy
import unittest
import numpy as np
import pandas as pd
from shared_model_v7.exact_policy import solve_policy
from shared_model_v7.sequential import Context
from shared_model_v7.semantic import family
from shared_model_v7.acceptance import check
from shared_model_v7.change_scan import Context as Scan
from shared_model_v7.multimodal import Reference
from shared_model_v7.remap_models import Marginal,Transport
from shared_model_v7.role_context import Context as Trajectory
from shared_model_v7.role_priority import analyze
from shared_model_v7.history_support import past_observations
from shared_model_v7.subspace_forest import Paths,expected_path
from shared_model_v7.context_reference import ContextReference
from shared_model_v7.count_filter import Context as CountContext,scores as filtered_scores
from shared_model_v7.dense_policy import solve as solve_dense
from shared_model_v7.evidence_fusion import Encoder as FusionEncoder,Reference as FusionReference
from shared_model_v7.exact_count_context import Context as ExactCount
from shared_model_v7.adapter_calibration import Codec,TrafficSharedCodec,tables as adapter_tables
from shared_model_v7.causal_hold import held_scores
from shared_model_v7.strict_policy import solve as solve_strict
from shared_model_v7.pmf_filter import Context as PMFContext
from shared_model_v7.switch_calibration import tables as switch_tables
from shared_model_v7.continuous_fusion import Reference as ContinuousReference
from shared_model_v7.shrunk_fusion import SharedCutoffCodec
from shared_model_v7.cohort_kmeans import Detector as CohortKMeans,Setting as CohortSetting
from shared_model_v6.features import RAW
from shared_model_v4.rank_context import RANK
from shared_model_v7.role_context import COHORT
from shared_model_v7.gap_passthrough import Reference as GapReference
from shared_model_v7.burst_margin import Reference as BurstReference
from types import SimpleNamespace

class DummyPeaks:
    def transform(self,f,view):return f[['point','delay','trend','burst','gap']].to_numpy(float)

class DummyDurationReference:
    def transform(self,f):return np.column_stack([f.pointstat.to_numpy(float),np.zeros((len(f),2))])

class ScopeTests(unittest.TestCase):
    def test_release_counts_shared_estimator_aliases_once(self):
        from sklearn.ensemble import IsolationForest
        from sklearn.cluster import MiniBatchKMeans
        from shared_model_v7.release import estimators
        core=IsolationForest(n_estimators=2)
        self.assertEqual(len(estimators({'a':core,'b':[core,{'same':core}]})),1)
        self.assertEqual(len(estimators({'a':core,'b':MiniBatchKMeans(n_clusters=2)})),2)
    def test_user_relaxed_FPR_cap_is_inclusive_but_does_not_relax_other_metrics(self):
        r={'method':'if','accuracy':.95,'precision':.9,'recall':.7,'f1':.78,'normal_false_positive_rate':.02}
        base={'pooled':[r],'datasets':[{'dataset':str(i),'metrics':[r]}for i in range(5)]};new=copy.deepcopy(base)
        new['datasets'][0]['metrics'][0]['normal_false_positive_rate']=.06
        kwargs={'max_fpr':.06,'fpr_inclusive':True,'fpr_nonincrease':False}
        self.assertTrue(check(new,base,**kwargs)['all_targets_met'])
        new['datasets'][0]['metrics'][0]['normal_false_positive_rate']=.06001
        self.assertFalse(check(new,base,**kwargs)['all_targets_met'])
        new['datasets'][0]['metrics'][0]['normal_false_positive_rate']=.03
        new['datasets'][0]['metrics'][0]['precision']=.899
        self.assertFalse(check(new,base,**kwargs)['all_targets_met'])
    def test_strict_policy_rejects_even_small_FPR_increase(self):
        baseline=np.tile([999,1,60,40],(5,1))
        bad=np.tile(np.array([[[998,2,10,90]]]),(5,1,1))
        choice,receipt=solve_strict([bad],baseline,time_limit=3)
        self.assertIsNone(choice)
        good=np.tile(np.array([[[999,1,10,90]]]),(5,1,1))
        choice,receipt=solve_strict([good],baseline,time_limit=3)
        self.assertIsNotNone(choice);self.assertTrue(receipt['independently_valid'])
    def test_strict_role_objective_preserves_each_other_split(self):
        counts=np.tile(np.array([[[99,1,10,90],[99,1,30,70]]]),(10,1,1))
        counts[:2,0]=[99,1,40,60];counts[:2,1]=[99,1,20,80]
        baseline=np.tile([99,1,50,50],(10,1))
        choice,receipt=solve_strict([counts],baseline,preferred=(0,1),time_limit=3)
        np.testing.assert_array_equal(choice,[1]);self.assertTrue(receipt['independently_valid'])
    def test_scope_rejects_FPR_increase_below_five_percent(self):
        r={'method':'if','accuracy':.95,'precision':.9,'recall':.7,'f1':.78,'normal_false_positive_rate':.02}
        base={'pooled':[r],'datasets':[{'dataset':str(i),'metrics':[r]}for i in range(5)]};new=copy.deepcopy(base)
        new['datasets'][0]['metrics'][0]['normal_false_positive_rate']=.02001
        self.assertFalse(check(new,base)['all_targets_met'])
    def test_dense_ragged_policy_checks_precision_and_recall(self):
        first=np.tile(np.array([[[49,1,20,30],[48,2,10,40]]]),(5,1,1))
        second=np.tile(np.array([[[50,0,20,30]]]),(5,1,1));baseline=np.tile([99,1,50,50],(5,1))
        chosen,r=solve_dense([first,second],baseline,time_limit=3)
        np.testing.assert_array_equal(chosen,[0,0]);self.assertTrue(r['independent_metric_constraints_valid'])
        failed,_=solve_dense([first,second],baseline,minimum_recall=[.7,.55,.55,.55,.55],time_limit=3)
        self.assertIsNone(failed)
    def test_role_preference_never_relaxes_other_task_constraints(self):
        counts=np.zeros((5,1,2,4),int);counts[:,:,0,:]=[99,1,10,90];counts[:,:,1,:]=[99,1,30,70]
        counts[0,0,0]=[99,1,40,60];counts[0,0,1]=[99,1,20,80]
        baseline=np.tile([99,1,50,50],(5,1))
        ordinary,_=solve_policy(counts,baseline,[.95,.99]);focused,receipt=solve_policy(counts,baseline,[.95,.99],preferred_task=0)
        self.assertEqual(int(ordinary[0]),0);self.assertEqual(int(focused[0]),1)
        self.assertTrue(receipt['strict_per_task_nonregression']);self.assertTrue(receipt['preference_does_not_relax_other_constraints'])
    def test_role_preference_cannot_buy_recall_with_precision(self):
        counts=np.zeros((5,1,2,4),int);counts[:,:,0,:]=[99,1,60,40];counts[:,:,1,:]=[90,10,40,60]
        chosen,_=solve_policy(counts,np.tile([99,1,60,40],(5,1)),[.95,.99],preferred_task=0)
        self.assertIsNone(chosen)
    def test_exact_solver_enforces_each_task(self):
        counts=np.zeros((5,2,2,4),int)
        counts[:,:,0,:]=[99,1,60,40];counts[:,:,1,:]=[99,1,40,60]
        base=np.tile([198,2,120,80],(5,1));chosen,r=solve_policy(counts,base,[.95,.99])
        self.assertIsNotNone(chosen);self.assertTrue(r['strict_per_task_nonregression'])
        got=np.array(r['counts']);self.assertTrue(np.all(got[:,3]/(got[:,3]+got[:,2])>=.55))
    def test_precision_tradeoff_does_not_pass(self):
        counts=np.zeros((5,2,2,4),int)
        counts[:,:,0,:]=[99,1,60,40];counts[:,:,1,:]=[90,10,40,60]
        chosen,r=solve_policy(counts,np.tile([198,2,120,80],(5,1)),[.95,.99])
        self.assertIsNone(chosen);self.assertTrue(r['globally_infeasible'])
    def test_scope_rejects_one_task_regression(self):
        r={'method':'if','accuracy':.95,'precision':.9,'recall':.7,'f1':.78,'normal_false_positive_rate':.02}
        base={'pooled':[r],'datasets':[{'dataset':str(i),'metrics':[r]}for i in range(5)]};new=copy.deepcopy(base)
        new['pooled'][0]['f1']=.85;new['datasets'][0]['metrics'][0]['precision']=.899
        self.assertFalse(check(new,base)['all_targets_met'])
    def test_scope_rejects_low_recall_even_if_others_improve(self):
        r={'method':'if','accuracy':.95,'precision':.9,'recall':.7,'f1':.78,'normal_false_positive_rate':.02}
        base={'pooled':[r],'datasets':[{'dataset':str(i),'metrics':[r]}for i in range(5)]};new=copy.deepcopy(base)
        new['datasets'][0]['metrics'][0]['recall']=.549
        self.assertFalse(check(new,base)['all_targets_met'])
    def test_scope_accepts_maintained_metrics(self):
        r={'method':'if','accuracy':.95,'precision':.9,'recall':.7,'f1':.78,'normal_false_positive_rate':.02}
        base={'pooled':[r],'datasets':[{'dataset':str(i),'metrics':[r]}for i in range(5)]}
        self.assertTrue(check(copy.deepcopy(base),base)['all_targets_met'])

class FeatureTests(unittest.TestCase):
    def test_gap_retention_uses_observed_signal_not_truth(self):
        f=pd.DataFrame({'point':[2.,0.,0.],'delay':0.,'trend':0.,'burst':0.,'gap':[0.,2.,3.]})
        previous=SimpleNamespace(parent=SimpleNamespace(encoder=DummyPeaks()),
                                 apply=lambda frame,raw:np.array([4.,4.,4.]))
        ref=GapReference(previous,1.5);raw=np.array([2.,1.,3.]);score=ref.apply(f,raw)
        self.assertEqual(score[0],4.);np.testing.assert_array_equal(score[1:]>1,raw[1:]>1.5)
        f['truth']=[True,False,True];np.testing.assert_array_equal(score,ref.apply(f,raw))
    def test_volume_buffer_cannot_add_alerts_or_change_unknown_fallback(self):
        f=pd.DataFrame({'point':[0.,3.,0.],'delay':0.,'trend':0.,'burst':[3.,0.,3.],'gap':0.,'adapterType':['A','A','NEW']})
        previous=SimpleNamespace(parent=SimpleNamespace(encoder=DummyPeaks()),codec=SimpleNamespace(adapters={'A':0}),
                                 apply=lambda frame,raw:np.full(3,1.01))
        ref=BurstReference(previous,.025);value=ref.apply(f,np.zeros(3))
        np.testing.assert_allclose(value,[.985,1.01,1.01])
        self.assertEqual(int((value>1).sum()),2)
    def test_cohort_KMeans_is_one_estimator_with_ten_causal_inputs(self):
        rng=np.random.default_rng(42)
        f=pd.DataFrame({c:rng.uniform(size=80)for c in dict.fromkeys([*RAW,*RANK,*COHORT])})
        f['case_id']=np.repeat(['c'+str(i)for i in range(8)],10)
        m=CohortKMeans(CohortSetting(clusters=4),{}).fit(f)
        self.assertEqual(m.estimator.n_features_in_,10);self.assertEqual(m.record()['estimator_count'],1)
        np.testing.assert_array_equal(m.score(f)[:20],m.score(f.iloc[:20]))
    def test_shrunk_cutoff_codec_has_only_twenty_observed_groups_and_unknown(self):
        _,f=self.context_and_frame();f['latency_assessed']=False;f['adapterType']=['A','B']*5
        for c in ('point','delay','trend','burst','gap'):f[c]=0.
        codec=SharedCutoffCodec.fit(DummyPeaks(),f);self.assertEqual(codec.n_groups,21)
        self.assertEqual(codec.values(f)[0],codec.values(f)[1])
        f['adapterType']='NEW';np.testing.assert_array_equal(codec.values(f),20)
    def test_traffic_pooling_retains_duration_and_unknown_adapter_context(self):
        _,f=self.context_and_frame();f['latency_assessed']=False;f['adapterType']=['A','B']*5
        for c in ('point','delay','trend','burst','gap'):f[c]=0.
        f['burst']=2.;codec=TrafficSharedCodec.fit(DummyPeaks(),f);g=codec.values(f)
        self.assertEqual(g[0],g[1])
        duration=f.copy();duration['point']=5.
        self.assertNotEqual(codec.values(duration)[0],codec.values(duration)[1])
        unknown=f.copy();unknown['adapterType']='NEW'
        self.assertTrue(np.all(codec.values(unknown)%codec.stride==len(codec.adapters)))
    def test_stable_PMF_extreme_counts_are_not_lost_to_probability_floors(self):
        c,f=self.context_and_frame();history={k:[0,0,0,1,1,2]for k in c.history}
        ctx=PMFContext(history,stable_log=True);normal,alternatives=ctx.log_emissions((False,0),[0,5,100,999])
        self.assertTrue(np.isfinite(normal).all());self.assertTrue(np.isfinite(alternatives).all())
        self.assertTrue(np.all(alternatives[-2:]>normal[-2:,None]))
        f['native_execution_arrivals']=0.;f.loc[6,'native_execution_arrivals']=999
        score=ctx.transform(f);self.assertGreater(score[6],10.)
        np.testing.assert_allclose(score[:7],ctx.transform(f.iloc[:7]),atol=1e-12)
    def test_intensity_specific_state_filter_remains_prefix_causal(self):
        c,f=self.context_and_frame();history={k:[0,0,0,1,1,2]for k in c.history}
        ctx=PMFContext(history,duty=(.8,.5,.25),stay=(.6,.8,.95),stable_log=True)
        score=ctx.transform(f);self.assertTrue(np.isfinite(score).all())
        np.testing.assert_allclose(score[:7],ctx.transform(f.iloc[:7]),atol=1e-12)
    def test_continuous_normal_tail_retains_magnitude_above_normal_maximum(self):
        ref=np.array([0.,0.,.1,.2,.5]);value=ContinuousReference.transport(np.array([.6,1.,2.,4.]),ref)
        self.assertTrue(np.isfinite(value).all());self.assertTrue(np.all(np.diff(value)>0.))
        self.assertEqual(float(ContinuousReference.transport(np.array([0.]),np.zeros(5))[0]),0.)
    def test_original_score_switch_contains_exact_baseline_policy(self):
        _,f=self.context_and_frame();f['latency_assessed']=False;f['adapterType']='A'
        for c in ('point','delay','trend','burst','gap'):f[c]=0.
        codec=Codec.fit(DummyPeaks(),f);nk=codec.values(f)
        names=['task'+str(i)for i in range(5)];data={t:{'y':np.array([False,True])}for t in names}
        core={t:np.array([.2,.8])for t in names};fused={t:np.array([.9,.7])for t in names}
        groups={t:np.array([nk[0],nk[0]])for t in names};normal=np.linspace(0,1,len(f))
        counts,cuts,centers,modes,receipt=switch_tables(normal,normal,nk,f,fused,core,groups,data,names,codec,.5)
        choice,result=solve_strict(counts,np.tile([1,0,0,1],(5,1)),minimum_recall=.55,time_limit=3)
        self.assertIsNotNone(choice);self.assertTrue(result['independently_valid'])
        unknown=f.copy();unknown['adapterType']='NEW';key=int(codec.values(unknown)[0])
        self.assertEqual(cuts[key].tolist(),[.5]);self.assertTrue(modes[key].all())
    def test_PMF_state_prefix_future_pair_and_missing(self):
        c,f=self.context_and_frame();history={k:[0,0,0,1,1,2]for k in c.history}
        ctx=PMFContext(history);score=ctx.transform(f)
        np.testing.assert_allclose(score[:7],ctx.transform(f.iloc[:7]),atol=1e-12)
        changed=f.copy();changed.loc[7:,'native_execution_arrivals']=200
        np.testing.assert_allclose(score[:7],ctx.transform(changed)[:7],atol=1e-12)
        paired=ctx.transform(pd.concat([f,f],ignore_index=True))
        np.testing.assert_allclose(paired[:10],paired[10:],atol=1e-12)
        missing=f.copy();missing['native_execution_arrivals']=np.nan
        np.testing.assert_array_equal(ctx.transform(missing),0.)
    def test_exact_count_convolution_and_prefix_causality(self):
        history={(w,h):[0,1]for w in(False,True)for h in range(24)};ctx=ExactCount(history)
        np.testing.assert_allclose(ctx.distribution(((False,0),(False,0))),[1.,.75,.25])
        _,f=self.context_and_frame();x=ctx.transform(f);np.testing.assert_array_equal(x.iloc[:7],ctx.transform(f.iloc[:7]))
        future=f.copy();future.loc[7:,'native_execution_arrivals']=999
        np.testing.assert_array_equal(x.iloc[:7],ctx.transform(future).iloc[:7])
        pair=ctx.transform(pd.concat([f,f],ignore_index=True)).to_numpy();np.testing.assert_array_equal(pair[:10],pair[10:])
    def test_unknown_adapter_has_a_fixed_statistical_fallback(self):
        _,f=self.context_and_frame();f['latency_assessed']=False;f['adapterType']='A'
        for c in('point','delay','trend','burst','gap'):f[c]=0.
        codec=Codec.fit(DummyPeaks(),f);g=codec.values(f);unknown=f.copy();unknown['adapterType']='NEW'
        np.testing.assert_array_equal(codec.values(unknown)%codec.stride,len(codec.adapters))
        tasks=['t'+str(i)for i in range(5)];data={t:{'y':np.array([False,True])}for t in tasks};raw={t:np.array([.2,.8])for t in tasks};groups={t:np.array([g[0],g[0]])for t in tasks}
        unused,cuts,centers,receipt=adapter_tables(np.linspace(0,1,len(f)),g,f,raw,groups,data,tasks,codec)
        key=int(codec.values(unknown)[0]);self.assertEqual(len(cuts[key]),1);self.assertTrue(receipt[key]['fixed_P995_if_unobserved'])
    def test_adapter_grid_retains_parent_normal_threshold_options(self):
        codec=Codec(DummyPeaks(),{'A':0,'B':1});normal=np.r_[np.linspace(0,1,60),np.linspace(.43,.57,60)]
        frame=pd.DataFrame({'case_id':np.repeat([str(i)for i in range(6)],20)});g=np.r_[np.zeros(60,int),np.ones(60,int)]
        tasks=['t'+str(i)for i in range(5)];data={t:{'y':np.array([True,False])}for t in tasks};raw={t:np.array([1.1,.8])for t in tasks};groups={t:np.ones(2,int)for t in tasks}
        unused,cuts,center,receipt=adapter_tables(normal,g,frame,raw,groups,data,tasks,codec)
        self.assertTrue(np.any((cuts[1]>.8)&(cuts[1]<1.1)))
    def test_refractory_does_not_extend_old_memory_or_hide_current_evidence(self):
        _,f=self.context_and_frame();f['native_execution_arrivals']=2;family=np.full(len(f),3);raw=np.zeros(len(f));raw[[1,3,4,8]]=2.
        reference=np.array([0.,.5,1.]);v=held_scores(f,raw,family,reference,1.5,3,refractory=7)
        self.assertTrue(np.all(v[1:5]>0));self.assertTrue(np.all(v[5:8]==0));self.assertTrue(np.all(v[8:]>0))
        np.testing.assert_array_equal(v[:7],held_scores(f.iloc[:7],raw[:7],family[:7],reference,1.5,3,refractory=7))
    def test_count_filter_is_prefix_causal_and_pair_independent(self):
        c,f=self.context_and_frame();history={k:[0,0,0,1,1,2]for k in c.history};ctx=CountContext(history)
        s=filtered_scores(ctx.emissions(f));np.testing.assert_array_equal(s[:8],filtered_scores(ctx.emissions(f.iloc[:8])))
        altered=f.copy();altered.loc[8:,'native_execution_arrivals']=999
        np.testing.assert_array_equal(s[:8],filtered_scores(ctx.emissions(altered))[:8])
        both=filtered_scores(ctx.emissions(pd.concat([f,f],ignore_index=True)));np.testing.assert_array_equal(both[:10],both[10:])
    def test_single_arrival_does_not_create_volume_state(self):
        c,f=self.context_and_frame();history={k:[0,0,0,1,1,2]for k in c.history};ctx=CountContext(history)
        f['native_execution_arrivals']=0.;z=filtered_scores(ctx.emissions(f));one=f.copy();one.loc[6,'native_execution_arrivals']=1
        np.testing.assert_array_equal(z,filtered_scores(ctx.emissions(one)))
    def test_missing_native_counts_are_not_zero_arrivals(self):
        c,f=self.context_and_frame();history={k:[0,0,0,1,1,2]for k in c.history};f['native_execution_arrivals']=np.nan
        raw=CountContext(history).emissions(f);self.assertFalse(raw['eligible'].any());np.testing.assert_array_equal(filtered_scores(raw),0.)
    def test_evidence_fusion_does_not_read_future_or_labels(self):
        c,f=self.context_and_frame();history={k:[0,0,0,1,1,2]for k in c.history}
        for key in('delay','trend','burst','gap'):f[key]=0.
        f['point']=np.linspace(0,2,len(f));f['pointstat']=f.point/4;f['current_source_upper']=f.point
        normal=f.copy();enc=FusionEncoder(DummyPeaks(),DummyDurationReference(),history,{'onset':.01,'recovery':.1,'tilt':.25}).fit(normal)
        raw=np.linspace(0,3,len(f));ref=FusionReference.fit(normal,raw,enc,.5,.5);score=ref.apply(f,raw)
        np.testing.assert_array_equal(score[:7],ref.apply(f.iloc[:7],raw[:7]))
        f['truth']=True;np.testing.assert_array_equal(score,ref.apply(f,raw))
    def test_history_support_counts_observed_minutes_not_rows_or_case_age(self):
        _,f=self.context_and_frame();f['latency_assessed']=[False,False,True,False,True,True,False,True,False,False]
        np.testing.assert_array_equal(past_observations(f),[0,0,0,1,1,2,3,3,4,4])
        np.testing.assert_array_equal(past_observations(f)[:6],past_observations(f.iloc[:6]))
        paired=past_observations(pd.concat([f,f],ignore_index=True));np.testing.assert_array_equal(paired[:10],paired[10:])
    def test_subspace_paths_reproduce_standard_IF_aggregation(self):
        from sklearn.ensemble import IsolationForest
        x=np.random.default_rng(42).normal(size=(100,3)).astype(np.float32)
        forest=IsolationForest(n_estimators=60,max_samples=32,max_features=1,random_state=42).fit(x)
        paths=Paths(forest);np.testing.assert_allclose(paths.ordinary_score(x),-forest.score_samples(x),atol=1e-12)
        self.assertEqual(paths.group_scores(x).shape,(100,3));self.assertEqual(int(paths.counts.sum()),60)
    def test_subspace_path_edge_cases(self):
        np.testing.assert_allclose(expected_path([0,1,2]),[0,0,1])
    def test_context_reference_case_balance_and_query_independence(self):
        rng=np.random.default_rng(3);normal=pd.DataFrame({'case_id':np.repeat([str(i)for i in range(20)],10),
            'point':10.,'delay':rng.uniform(size=200),'trend':0.,'burst':0.,'gap':0.})
        scores=normal.delay.to_numpy()+rng.uniform(size=200)*.1
        ref=ContextReference.fit(normal,scores,DummyPeaks(),neighbors=32)
        for table in ref.tables.values():
            unused,count=np.unique(table['cases'],return_counts=True);self.assertLessEqual(count.max(),2)
        query=normal.iloc[:5].copy();query['case_id']='new';value=ref.apply(query,scores[:5])
        np.testing.assert_array_equal(value[:2],ref.apply(query.iloc[:2],scores[:2]));self.assertTrue(np.isfinite(value).all())
    def trajectory_frame(self):
        return pd.DataFrame({'case_id':['a']*12,'window_start':pd.date_range('2026-10-12',periods=12,freq='min'),
            'residual_mean_1m':[0.,.1,0.,.1,.2,.3,.5,.7,.9,1.1,1.3,1.5],'latency_assessed':[True]*12})
    def test_trajectory_prefix_and_future_change(self):
        f=self.trajectory_frame();c=Trajectory();expected=c.transform(f).iloc[:8]
        np.testing.assert_array_equal(expected,c.transform(f.iloc[:8]))
        future=f.copy();future.loc[8:,'residual_mean_1m']=999
        np.testing.assert_array_equal(expected,c.transform(future).iloc[:8])
    def test_trajectory_pair_reset(self):
        f=self.trajectory_frame();actual=Trajectory().transform(pd.concat([f,f],ignore_index=True)).to_numpy()
        np.testing.assert_array_equal(actual[:12],actual[12:])
    def test_trajectory_positive_for_consistent_growth(self):
        f=self.trajectory_frame();r=Trajectory().transform(f)
        self.assertGreater(r.trajectory_linear_slope.iloc[-1],0.)
        self.assertGreater(r.trajectory_positive_fraction.iloc[-1],.5)
    def test_trajectory_refuses_noncontiguous_history(self):
        with self.assertRaises(ValueError):Trajectory().transform(self.trajectory_frame().drop(index=4))
    def context_and_frame(self):
        history={(weekend,hour):[0,0,0,1,1,2]for weekend in(False,True)for hour in range(24)}
        f=pd.DataFrame({'case_id':['a']*10,'window_start':pd.date_range('2026-10-12',periods=10,freq='min'),
            'native_execution_arrivals':[0,0,1,1,1,1,5,0,0,1]})
        return Context(history),f
    def test_traffic_is_prefix_causal(self):
        c,f=self.context_and_frame();whole=c.transform(f)
        np.testing.assert_array_equal(whole.iloc[:7],c.transform(f.iloc[:7]))
        altered=f.copy();altered.loc[7:,'native_execution_arrivals']=1000
        np.testing.assert_array_equal(whole.iloc[:7],c.transform(altered).iloc[:7])
    def test_paired_cohorts_reset(self):
        c,f=self.context_and_frame();both=pd.concat([f,f],ignore_index=True);value=c.transform(both)
        np.testing.assert_array_equal(value.iloc[:10].to_numpy(),value.iloc[10:].to_numpy())
    def test_requires_contiguous_grid(self):
        c,f=self.context_and_frame()
        with self.assertRaises(ValueError):c.transform(f.drop(index=4))
    def test_semantic_family_retains_db2db_and_stage(self):
        self.assertEqual(family('IF_DB2DB_900_PR002'),'IF_DB2DB_N_PR002')
        self.assertNotEqual(family('IF_DB2DB_900_PR001'),family('IF_DB2DB_900_PR002'))
    def test_scan_prefix_and_future_change(self):
        c,f=self.context_and_frame();scan=Scan({k:[0,0,0,1,1,2]for k in c.history});whole=scan.transform(f)
        np.testing.assert_allclose(whole.iloc[:7],scan.transform(f.iloc[:7]))
        altered=f.copy();altered.loc[7:,'native_execution_arrivals']=999
        np.testing.assert_allclose(whole.iloc[:7],scan.transform(altered).iloc[:7])
    def test_scan_pair_reset(self):
        c,f=self.context_and_frame();scan=Scan({k:[0,0,0,1,1,2]for k in c.history})
        both=scan.transform(pd.concat([f,f],ignore_index=True)).to_numpy()
        np.testing.assert_array_equal(both[:10],both[10:])
    def test_scan_refuses_skipped_minute(self):
        c,f=self.context_and_frame();scan=Scan({k:[0,0,0,1,1,2]for k in c.history})
        with self.assertRaises(ValueError):scan.transform(f.drop(index=4))
    def test_normal_neighbourhood_can_find_between_mode_gap(self):
        normal=pd.DataFrame({'adapterType':['A']*20,'processId':['P']*20,'case_id':['a','b']*10,
            'latency_assessed':[True]*20,'log_max_duration_1m':[0.]*10+[5.]*10,'log_mean_count_5m':[1.]*20})
        ref=Reference(0.).fit(normal);query=normal.iloc[:2].copy();query['log_max_duration_1m']=[.01,2.5]
        result=ref.transform(query);self.assertGreater(result[1,0],result[0,0])
    def test_neighbourhood_query_batch_not_future_reference(self):
        normal=pd.DataFrame({'adapterType':['A']*20,'processId':['P']*20,'case_id':['a','b']*10,
            'latency_assessed':[True]*20,'log_max_duration_1m':np.linspace(0,2,20),'log_mean_count_5m':[1.]*20})
        ref=Reference().fit(normal);query=normal.iloc[:3].copy();query['log_max_duration_1m']=[3.,4.,5.]
        expected=ref.transform(query.iloc[:1]);actual=ref.transform(query)
        np.testing.assert_array_equal(expected,actual[:1])
    def test_normal_marginal_transport_monotone_and_finite(self):
        mapper=Marginal().fit(np.array([0.,0.,1.,2.]),np.array([0.,1.,2.,4.]))
        value=mapper.apply(np.array([0.,.1,1.,2.,5.]))
        self.assertTrue(np.isfinite(value).all());self.assertTrue(np.all(np.diff(value)>=0.))
    def test_cold_history_is_prefix_causal(self):
        _,f=self.context_and_frame();f['completion_count_1m']=[0,0,1,1,1,0,1,0,0,1]
        np.testing.assert_array_equal(Transport.cold(f),[True,True,True,True,True,False,False,False,False,False])
        np.testing.assert_array_equal(Transport.cold(f)[:6],Transport.cold(f.iloc[:6]))
        future=f.copy();future.loc[6:,'completion_count_1m']=999
        np.testing.assert_array_equal(Transport.cold(f)[:6],Transport.cold(future)[:6])
    def test_cold_history_paired_reset(self):
        _,f=self.context_and_frame();f['completion_count_1m']=1
        both=Transport.cold(pd.concat([f,f],ignore_index=True));np.testing.assert_array_equal(both[:10],both[10:])

if __name__=='__main__':unittest.main()
