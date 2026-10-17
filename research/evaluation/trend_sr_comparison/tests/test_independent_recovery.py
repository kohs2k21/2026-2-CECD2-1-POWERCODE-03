"""Observation-only fast proxy: native publication and explicit safety failures."""
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from research.evaluation.trend_sr_comparison import recovery
from research.evaluation.trend_sr_comparison.tests.test_independent_metrics import timeline,event


def setup(n=50,cadence=5):
    frame=timeline([1.]*n); frame['count']=2.
    evidence={'group':frame.group.to_numpy(),'decision_tick':frame.decision_tick.to_numpy(), 'status':'fixture',
        'count':{'supported':np.ones(n,bool),'quiet':np.ones(n,bool),'strong':np.zeros(n,bool),
                 'direction':np.zeros(n,int),'reason':np.full(n,'fixture')}}
    bundle={'entry':{'method':'R01','family':'count','cadence':cadence},'calibration':{'passed':True,'thresholds':{'count':1.}}}
    return bundle,frame,evidence


class RecoveryContracts(unittest.TestCase):
    def test_quiet10_strong3_native_publication_and_replay_reset(self):
        bundle,raw,evidence=setup()
        evidence['count']['quiet'][20:]=False
        evidence['count']['strong'][20:23]=True
        evidence['count']['direction'][20:23]=1
        fast,log=recovery.apply_fast(bundle,None,raw,evidence=evidence)
        np.testing.assert_array_equal(fast.prediction[:10],np.ones(10))
        np.testing.assert_array_equal(fast.prediction[10:25],np.zeros(15))
        self.assertEqual(fast.prediction.iloc[25],1.)
        self.assertEqual([r['to_state'] for r in log['transitions']],['ALERT','SUPPRESSED','ALERT'])
        repeat,_=recovery.apply_fast(bundle,None,raw,evidence=evidence)
        pd.testing.assert_frame_equal(fast,repeat)

    def test_unknown_immediate_no_new_positive_and_unsupported_raw_fallback(self):
        bundle,raw,evidence=setup()
        raw.loc[12,'prediction']=np.nan; raw.loc[12,'count']=np.nan
        # Branch inputs themselves are held between native C5 publications.
        raw.loc[20:24,'prediction']=0.; raw.loc[20:24,'count']=0.
        fast,_=recovery.apply_fast(bundle,None,raw,evidence=evidence)
        self.assertTrue(np.isnan(fast.prediction.iloc[12]))
        np.testing.assert_array_equal(np.isfinite(fast.prediction),np.isfinite(raw.prediction))
        self.assertFalse(((fast.prediction==1)&(raw.prediction!=1)).any())
        evidence['count']['supported'][:]=False
        fallback,_=recovery.apply_fast(bundle,None,raw,evidence=evidence)
        np.testing.assert_array_equal(fallback.prediction,raw.prediction)

    def test_hit_loss_and_extra_delay_must_be_reported_even_without_active_suppression(self):
        raw=timeline([0.]*50); raw.loc[5:9,'prediction']=1.
        truth=pd.DataFrame([event(raw,5,10,'lost')])
        fast=raw.copy(); fast.loc[5:9,'prediction']=0.
        result=recovery.compare(raw,fast,truth)['safety']
        self.assertEqual(result['raw_hit_lost_event_ids'],['lost'])
        delayed=raw.copy(); delayed.loc[5,'prediction']=0.
        result=recovery.compare(raw,delayed,truth)['safety']
        self.assertEqual(result['first_alert_extra_delays'],[{'event_id':'lost','extra_minutes':1}])
        self.assertFalse(result['strict_safety_passed'])

    def test_normal_guard_replays_all_cadences_with_frozen_cuts_and_no_fit(self):
        from types import SimpleNamespace
        from research.evaluation.trend_sr_comparison import runner
        bundle,raw,evidence=setup(500)
        raw['count']=0.; raw['prediction']=0.
        cadences=(1,2,5,10,15,30,60)
        item=SimpleNamespace(events=pd.DataFrame(columns=['event_id','group','start','end']))
        with patch.object(runner,'score_candidate_published',return_value={c:raw.copy() for c in cadences}) as publish, \
                patch.object(recovery,'build_evidence',return_value=evidence):
            result=recovery.normal_guard(bundle,item,cadences)
        publish.assert_called_once_with(bundle,item,cadences)
        self.assertEqual({r['cadence'] for r in result['candidate_checks']},set(cadences))
        self.assertTrue(result['passed'])
        self.assertFalse(result['thresholds_changed'])
        self.assertEqual(result['fit_count'],0)

    def test_real_legacy_proxy_uses_observations_and_frozen_reference_not_truth(self):
        from research.evaluation.trend_sr_comparison import dataset,methods,runner
        train=dataset.generate(9501,'train','normal')
        item=dataset.generate(9501,'selection','main')
        start=pd.Timestamp(item.manifest['evaluation_start']); stop=start+pd.Timedelta(days=2)
        item=dataset.Dataset(item.frame[item.frame.bin_start.lt(stop)].copy(),item.events.iloc[:0].copy(),
            item.quality[item.frame.bin_start.lt(stop)].copy(),{**item.manifest,'evaluation_end':stop.isoformat()})
        ref=methods.fit_method(runner.prepare(train,1),methods.Configuration('R00',60),seed=9501)
        calibration={'passed':True,'thresholds':{'count':10.,'latency':10.,'relation':5.}}
        bundle={'entry':{'method':'R00','cadence':5},'baseline':ref,'candidate':ref,
                'calibration':calibration,'baseline_calibration':calibration}
        raw=runner.score_candidate_bundle(bundle,item)
        evidence=recovery.build_evidence(bundle,item)
        self.assertEqual(set(evidence['group']),{'always','business'})
        fast,ledger=recovery.apply_fast(bundle,item,raw,evidence=evidence)
        item.events=pd.DataFrame([{'event_id':'poison','group':'always','start':start,'end':stop}])
        item.manifest['normal_confirmed']=False
        other=recovery.build_evidence(bundle,item)
        poisoned,_=recovery.apply_fast(bundle,item,raw,evidence=other)
        pd.testing.assert_frame_equal(fast,poisoned)
        np.testing.assert_array_equal(np.isfinite(raw.prediction),np.isfinite(fast.prediction))
        self.assertEqual(ledger['evidence_status'],'supported')


if __name__=='__main__': unittest.main()
