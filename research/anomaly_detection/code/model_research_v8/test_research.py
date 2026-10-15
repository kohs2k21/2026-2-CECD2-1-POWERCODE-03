import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from model_research_v8.features import ConditionalTails,add_duration_features,add_cadence,FEATURES,LATENCY
from model_research_v8.models import ChannelFamily,ResearchSettings,ClusterDistance


def source():
    return pd.DataFrame({"adapterType":"A","processId":"P","totalCount":1,"duration_ms":np.arange(100)})


def minute_frame():
    dates=pd.date_range("2026-01-01",periods=12,freq="min")
    f=pd.DataFrame({"case_id":"C","adapterType":"A","processId":"P","window_start":dates,
                    "phase":["startup"]*5+["steady"]*7,"native_execution_arrivals":[1,1,1]+[0]*9})
    for col in FEATURES:f[col]=0.
    return f


class FeaturesTests(unittest.TestCase):
    def test_ties_conservative_and_monotonic(self):
        s=source();s["duration_ms"]=0
        reference=ConditionalTails.fit(s)
        rows=pd.DataFrame({"adapterType":"A","processId":"P","totalCount":1,"duration":[0,1,2]})
        z=reference.transform(rows)
        self.assertEqual(z[0,0],0);self.assertEqual(z[0,1],0)
        self.assertTrue(np.isfinite(z).all());self.assertGreater(z[1,0],0)

    def test_fallback_levels_and_unseen_ids(self):
        ref=ConditionalTails.fit(source())
        rows=pd.DataFrame({"adapterType":["A","A","NEW"],"processId":["P","NEW","NEW"],"totalCount":1,"duration":99})
        np.testing.assert_array_equal(ref.transform(rows)[:,2],[0,1,4])

    def test_current_duration_not_future_duration(self):
        frame=minute_frame();ref=ConditionalTails.fit(source())
        t=frame.window_start.astype("datetime64[ns]").astype("int64")/1e9
        rows=pd.DataFrame({"case_id":"C","adapterType":"A","processId":"P","totalCount":1,"end_seconds":t+30,"duration":20})
        a=add_duration_features(frame,rows,ref)
        changed=rows.copy();changed.loc[7:,"duration"]=10000
        b=add_duration_features(frame,changed,ref)
        np.testing.assert_array_equal(a.loc[:6,list(FEATURES)],b.loc[:6,list(FEATURES)])

    def test_completion_exactly_at_end_is_excluded(self):
        f=minute_frame();t=f.window_start.iloc[0].timestamp()
        rows=pd.DataFrame({"case_id":["C"],"adapterType":["A"],"processId":["P"],"totalCount":[1],"end_seconds":[t+60],"duration":[200]})
        z=add_duration_features(f,rows,ConditionalTails.fit(source()))
        self.assertEqual(z.completion_count_1m.iloc[0],0);self.assertEqual(z.completion_count_1m.iloc[1],1)

    def test_single_completion_assessed_without_prior_context(self):
        f=minute_frame();t=f.window_start.iloc[0].timestamp()
        rows=pd.DataFrame({"case_id":["C"],"adapterType":["A"],"processId":["P"],"totalCount":[1],"end_seconds":[t+30],"duration":[200]})
        z=add_duration_features(f,rows,ConditionalTails.fit(source()))
        self.assertTrue(z.latency_assessed.iloc[0]);self.assertFalse(z.traffic_assessed.iloc[0])

    def test_cadence_uses_prior_counts_only(self):
        f=minute_frame();f["completion_count_1m"]=0
        z=add_cadence(f)
        np.testing.assert_array_equal(z.traffic_assessed.iloc[:4],[False,False,False,True])
        changed=f.copy();changed.loc[4:,"native_execution_arrivals"]=100
        np.testing.assert_array_equal(add_cadence(changed).traffic_assessed.iloc[:4],z.traffic_assessed.iloc[:4])

    def test_missing_not_fabricated_and_ids_not_features(self):
        f=add_duration_features(minute_frame(),pd.DataFrame(),ConditionalTails.fit(source()))
        self.assertFalse(f.latency_assessed.any());self.assertEqual(f.completion_count_1m.sum(),0)
        self.assertFalse(set(FEATURES)&{"case_id","processId","adapterType","status","window_is_anomaly","severity"})


class ModelTests(unittest.TestCase):
    def parts(self):
        rng=np.random.default_rng(3);parts={}
        for name in ("train","calibration","threshold"):
            f=pd.DataFrame(rng.normal(size=(620,len(FEATURES))),columns=FEATURES)
            f["adapterType"]="A";f["traffic_assessed"]=True;f["latency_assessed"]=True
            f["phase"]=np.tile(["startup","steady"],310);parts[name]=f
        return parts,{"A":{n:30 for n in parts}}

    def test_all_modes_strict_decision_and_exact_fallback(self):
        parts,support=self.parts()
        for algorithm in ("if","kmeans"):
            family=ChannelFamily(FEATURES,ResearchSettings(algorithm)).fit(parts,support)
            for mode in ("mean","max"):
                family.set_operating_point(.99,.75,True,2.,mode)
                test=parts["threshold"].iloc[:15].copy();test["adapterType"]="NEW"
                margin,pred,covered=family.score(test)
                self.assertFalse(covered.any())
                np.testing.assert_array_equal(margin[:,0],margin[:,1]);np.testing.assert_array_equal(margin[:,0],margin[:,2])
                np.testing.assert_array_equal(pred,margin>0)

    def test_gating_has_no_positive_evidence_not_new_normal_labels(self):
        parts,support=self.parts();family=ChannelFamily(FEATURES,ResearchSettings("kmeans")).fit(parts,support)
        test=parts["threshold"].iloc[:10].copy();test["traffic_assessed"]=False;test["latency_assessed"]=False
        np.testing.assert_array_equal(family.global_model.explain(test),np.zeros((10,2)))

    def test_covariance_candidates_positive_and_finite(self):
        rng=np.random.default_rng(42);x=rng.normal(size=(400,4));cal=rng.normal(size=(200,4))
        for mode in ("radius","diagonal","full"):
            model=ClusterDistance(ResearchSettings("kmeans",covariance=mode)).fit(x,cal)
            self.assertTrue(np.isfinite(model.score(cal)).all());self.assertTrue((model.radii>0).all())
            for inverse in model.inverse:self.assertTrue((np.linalg.eigvalsh(inverse)>0).all())

    def test_batch_invariance(self):
        parts,support=self.parts();family=ChannelFamily(FEATURES,ResearchSettings("if")).fit(parts,support)
        f=parts["threshold"].iloc[:30]
        np.testing.assert_allclose(family.score(f)[0],np.vstack([family.score(f.iloc[:15])[0],family.score(f.iloc[15:])[0]]))

    def test_refined_calibration_envelope_and_fallback(self):
        from model_research_v8.refine import Family,RefinedSettings
        parts,support=self.parts()
        family=Family(FEATURES,RefinedSettings('kmeans')).fit(parts,support)
        family.set_operating_point(.995,.25,False,1.,'max')
        f=parts['threshold'].iloc[:12].copy();f['adapterType']='NEW'
        margin,pred,covered=family.score(f)
        self.assertFalse(covered.any());np.testing.assert_array_equal(margin[:,0],margin[:,2])
        cal=family.global_model.calibration_raw
        from model_research_v5.models import normalize
        score=normalize(cal,family.global_model.calibration_phase,family.norms['GLOBAL'])
        self.assertGreaterEqual(family.thresholds['GLOBAL']['steady'][0],np.quantile(score,.995))

    def test_bridge_calibration_and_exact_fallback(self):
        from model_research_v8.bridge import Bridge
        parts,_=self.parts()
        class FakeBase:
            experts={'A':None};support=[]
            def score(self,f):
                x=f[FEATURES[0]].to_numpy()
                margin=np.column_stack([x,x*2,x*3]);covered=f.adapterType.eq('A').to_numpy()
                margin[~covered,1:]=margin[~covered,0,None]
                return margin,margin>0,covered
        family=Bridge(FakeBase(),LATENCY[:2],ResearchSettings('kmeans')).fit(parts)
        family.set_operating_point(.99,.995,.25,1.,'max')
        f=parts['threshold'].iloc[:15].copy();f['adapterType']='NEW'
        margin,pred,covered=family.score(f)
        self.assertFalse(covered.any());np.testing.assert_array_equal(margin[:,0],margin[:,1]);np.testing.assert_array_equal(margin[:,0],margin[:,2])
        f['traffic_assessed']=False;f['latency_assessed']=False
        self.assertFalse(family.score(f)[1].any())
        ca=family.calibration;raw=family.normal['calibration']
        evidence=family.evidence(raw,np.repeat('UNSUPPORTED',len(raw)))
        self.assertGreaterEqual(family.joint['GLOBAL'][0],np.quantile(evidence[:,0],.995))

    def test_regime_router_uses_observed_cadence_not_labels(self):
        from model_research_v8.regime import RegimeFamily
        class Branch:
            experts={'A':None};support=[]
            def __init__(self,value):self.value=value
            def score(self,f):
                m=np.full((len(f),3),self.value)
                return m,m>0,f.adapterType.eq('A').to_numpy()
        family=RegimeFamily(Branch(.2),Branch(-.3),'if')
        f=pd.DataFrame({'adapterType':'A','traffic_assessed':[False,True,False,True],
                        'window_is_anomaly':[False,True,True,False],'scenario':['a','b','c','d']})
        m,p,c=family.score(f)
        np.testing.assert_array_equal(m[:,0],[.2,-.3,.2,-.3])
        f['window_is_anomaly']=~f.window_is_anomaly;f['scenario']='CHANGED'
        np.testing.assert_array_equal(family.score(f)[0],m)

    def test_legacy_class_alias_restored(self):
        from model_research_v8.compat import legacy_classes
        import sys
        main=sys.modules['__main__'];before={k:getattr(main,k,None) for k in ('Bridge','Latent')}
        with legacy_classes():self.assertTrue(hasattr(main,'Bridge'))
        for k,v in before.items():self.assertIs(getattr(main,k,None),v)


if __name__=="__main__":unittest.main()
