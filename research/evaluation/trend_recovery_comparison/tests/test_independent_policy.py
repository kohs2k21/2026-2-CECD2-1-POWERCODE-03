"""Independent exact-clock state counterexamples, no source generation or fit."""
import copy
import unittest

import numpy as np
import pandas as pd

from research.evaluation.trend_recovery_comparison import datahelper, policy


def inputs(n=80):
    ticks=pd.date_range('2026-07-06',periods=n,freq='min',tz='UTC')
    metadata=pd.DataFrame(dict(row_id=[f'r{i}' for i in range(n)],group='g',
        decision_tick=ticks,eligible=True))
    signals={'row_id':metadata.row_id.to_numpy(),'decision_tick':ticks}
    for family in ('count','latency','relation'):
        signals[family]=dict(supported=np.ones(n,bool),quiet=np.ones(n,bool),
            strong=np.zeros(n,bool),direction=np.zeros(n,int),reason=np.full(n,'ready',object))
    return {'c':np.ones(n)},metadata,signals,{'c':'count'},datahelper.load_protocol()['policies']['fast']


class PolicyContracts(unittest.TestCase):
    def setUp(self):
        self.args=inputs()

    def run_policy(self,cadence=1,args=None):
        return policy.apply_policy(*(args or self.args),cadence)

    def test_first_emission_immediate_and_quiet_elapsed_boundary(self):
        out,ledger,transitions=self.run_policy()
        np.testing.assert_array_equal(out[:10],np.ones(10))
        self.assertEqual(out[10],0)
        self.assertEqual(ledger.loc[9,'c_quiet_elapsed'],9)
        self.assertEqual(ledger.loc[10,'c_quiet_elapsed'],10)
        clears=transitions.query("reason=='quiet_confirmed'")
        self.assertEqual(clears.decision_tick.iloc[0],self.args[1].decision_tick.iloc[10])

    def test_midminute_quiet_break_restarts_true_elapsed_time(self):
        self.args[2]['count']['quiet'][6]=False
        out,_,_=self.run_policy(5)
        self.assertEqual(out[15],1)
        self.assertEqual(out[20],0)

    def test_offgrid_raw_mutation_cannot_change_outputs_or_transitions(self):
        baseline=self.run_policy(5)
        changed=copy.deepcopy(self.args)
        changed[0]['c'][np.arange(80)%5!=0]=np.nan
        result=self.run_policy(5,changed)
        np.testing.assert_array_equal(result[0],baseline[0])
        pd.testing.assert_frame_equal(result[1],baseline[1])
        pd.testing.assert_frame_equal(result[2],baseline[2])

    def test_strong_three_bins_rearm_while_raw_stays_positive(self):
        evidence=self.args[2]['count']
        evidence['quiet'][20:23]=False
        evidence['strong'][20:23]=True
        evidence['direction'][20:23]=1
        out,_,transitions=self.run_policy()
        self.assertTrue(np.all(out[11:22]==0))
        self.assertEqual(out[22],1)
        rearm=transitions.query("reason=='strong_confirmed'")
        self.assertEqual(len(rearm),1)
        self.assertEqual(rearm.decision_tick.iloc[0],self.args[1].decision_tick.iloc[22])

    def test_strong_direction_change_and_neutral_break_reset(self):
        evidence=self.args[2]['count']
        evidence['quiet'][20:30]=False
        evidence['strong'][20:25]=True
        evidence['direction'][20:25]=[1,-1,1,1,1]
        out,_,_=self.run_policy()
        self.assertTrue(np.all(out[20:24]==0))
        self.assertEqual(out[24],1)
        # A separate two-bin tail separated by neutral does not rearm.
        self.args=inputs();evidence=self.args[2]['count']
        evidence['quiet'][20:30]=False
        evidence['strong'][[20,21,23,24]]=True
        evidence['direction'][[20,21,23,24]]=1
        out,_,_=self.run_policy()
        self.assertTrue(np.all(out[20:30]==0))

    def test_unsupported_releases_latch_without_changing_raw_support(self):
        self.args[2]['count']['supported'][20]=False
        out,_,transitions=self.run_policy()
        self.assertEqual(out[19],0)
        self.assertEqual(out[20],1)
        self.assertTrue(np.all(np.isfinite(out)))
        self.assertIn('evidence_unsupported_raw_fallback',transitions.reason.to_list())

    def test_raw_unknown_and_negative_reset_state(self):
        self.args[0]['c'][20]=np.nan
        self.args[0]['c'][40]=0
        out,_,_=self.run_policy()
        self.assertTrue(np.isnan(out[20]))
        self.assertEqual(out[21],1)
        self.assertEqual(out[40],0)
        self.assertEqual(out[41],1)
        np.testing.assert_array_equal(np.isfinite(out),np.isfinite(self.args[0]['c']))

    def test_source_unknown_offgrid_cancels_interval_until_new_native(self):
        self.args[1].loc[17,'eligible']=False
        out,ledger,transitions=self.run_policy(5)
        self.assertEqual(out[15],0)
        self.assertEqual(out[20],1)
        self.assertIn('source_unknown',transitions.reason.to_list())
        self.assertTrue(np.all(np.isfinite(ledger.alert)))

    def test_partial_or_unknown_after_clear_falls_back_with_provenance(self):
        self.args[0]['u']=np.full(80,np.nan)
        self.args[3]['u']='latency'
        out,ledger,transitions=self.run_policy()
        self.assertTrue(np.all(out==1))
        row=ledger.loc[10]
        self.assertTrue(row.fallback)
        self.assertEqual(row.fallback_branches,'c')
        self.assertEqual(row.c_state,'ALERT')
        self.assertIn('arm_unknown_raw_fallback',transitions.reason.to_list())

    def test_one_family_recovery_does_not_clear_another_active_family(self):
        self.args[0]['d']=np.ones(80)
        self.args[3]['d']='latency'
        self.args[2]['latency']['quiet'][:]=False
        out,ledger,_=self.run_policy()
        self.assertTrue(np.all(out==1))
        self.assertEqual(ledger.loc[20,'c_output'],0)
        self.assertEqual(ledger.loc[20,'d_output'],1)

    def test_no_policy_preserves_ternary_and_cal_c15_is_supported(self):
        self.args=list(self.args);self.args[4]=None
        self.args[0]['c'][30]=np.nan
        out,ledger,_=self.run_policy(15,self.args)
        native=np.arange(80)%15==0
        np.testing.assert_array_equal(out[native],self.args[0]['c'][native])
        self.assertTrue(np.all(np.isnan(out[~native])))
        np.testing.assert_array_equal(ledger.baseline,ledger.alert)

    def test_same_observations_different_truth_produce_same_false_clear(self):
        ended=copy.deepcopy(self.args);continuing=copy.deepcopy(self.args)
        ended[1]['active']=np.arange(80)<10
        continuing[1]['active']=True
        ended_out=self.run_policy(args=ended)[0]
        continuing_out=self.run_policy(args=continuing)[0]
        np.testing.assert_array_equal(ended_out,continuing_out)
        self.assertEqual(int(np.sum(continuing_out[10:]==0)),70)
        # Passing this unit test documents an observational ambiguity, not safety.

    def test_recurrence_at_20_45_90_minutes_is_fixture_not_primary_event_count(self):
        for gap in (20,45,90):
            args=inputs(140);evidence=args[2]['count'];onset=10+gap
            evidence['quiet'][onset:onset+3]=False
            evidence['strong'][onset:onset+3]=True
            evidence['direction'][onset:onset+3]=1
            out,_,_=self.run_policy(args=args)
            self.assertEqual(out[onset],0)
            self.assertEqual(out[onset+2],1)

    def test_duplicate_and_misaligned_evidence_rejected(self):
        self.args[1].loc[1,'row_id']='r0'
        with self.assertRaisesRegex(ValueError,'Duplicate'):self.run_policy()
        self.args=inputs();self.args[2]['row_id']=self.args[2]['row_id'][::-1]
        with self.assertRaisesRegex(ValueError,'alignment'):self.run_policy()

    def test_missing_minute_cannot_extend_streak(self):
        raw,metadata,evidence,families,spec=self.args
        keep=np.arange(80)!=6
        args=({k:v[keep] for k,v in raw.items()},metadata.loc[keep].reset_index(drop=True),
            {k:({x:y[keep] for x,y in value.items()} if isinstance(value,dict) else np.asarray(value)[keep])
             for k,value in evidence.items()},families,spec)
        out,ledger,transitions=self.run_policy(args=args)
        self.assertEqual(ledger.loc[ledger.row_id.eq('r11'),'alert'].iloc[0],1)
        self.assertEqual(ledger.loc[ledger.row_id.eq('r18'),'alert'].iloc[0],0)
        self.assertIn('missing_minute',transitions.reason.to_list())


if __name__=='__main__':
    unittest.main()
