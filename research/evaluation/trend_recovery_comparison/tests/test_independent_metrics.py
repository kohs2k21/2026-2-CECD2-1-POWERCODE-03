"""Hand-counted event, unknown and recovery-censor counterexamples."""
import unittest

import numpy as np
import pandas as pd

from research.evaluation.trend_recovery_comparison.metrics import compare_alerts


def fixture(n=40):
    ticks=pd.date_range('2026-07-06',periods=n,freq='min',tz='UTC')
    meta=pd.DataFrame(dict(row_id=[f'r{i}' for i in range(n)],group='g',
        decision_tick=ticks,state='READY',eligible=True))
    events=pd.DataFrame([dict(event_id='e',group='g',scenario='burst',start=ticks[5],end=ticks[15])])
    return meta,events


class RecoveryMetrics(unittest.TestCase):
    def compare(self,before,after,baseline_hit=True,alert_hit=True,metadata=None,events=None):
        meta,truth=fixture(len(before))
        if metadata is not None:meta=metadata
        if events is not None:truth=events
        def rows(hit):return [dict(event_id=e,hit=hit,delay_minutes=0 if hit else None) for e in truth.event_id]
        return compare_alerts(before,after,meta,truth,rows(baseline_hit),rows(alert_hit),1)

    def test_missed_event_does_not_receive_zero_recovery(self):
        row=self.compare(np.zeros(40),np.zeros(40),False,False)['event_details'][0]
        self.assertIsNone(row['recovery_first_negative_delay_minutes'])
        self.assertIsNone(row['recovery_stable_negative_delay_minutes'])
        self.assertEqual(row['recovery_first_negative_censor_reason'],'baseline_miss')

    def test_alert_miss_and_all_unknown_are_explicitly_censored(self):
        row=self.compare(np.ones(40),np.zeros(40),True,False)['event_details'][0]
        self.assertEqual(row['recovery_first_negative_censor_reason'],'alert_miss')
        self.assertIsNone(row['recovery_first_negative_delay_minutes'])
        row=self.compare(np.full(40,np.nan),np.full(40,np.nan),False,False)['event_details'][0]
        self.assertEqual(row['recovery_first_negative_censor_reason'],'all_unknown')
        self.assertEqual(row['recovery_horizon_unknown_minutes'],25)

    def test_ongoing_suppression_and_clear_count_are_distinct(self):
        baseline=np.ones(40);alert=np.ones(40);alert[10:]=0
        result=self.compare(baseline,alert);row=result['event_details'][0]
        self.assertEqual(result['active_suppressed_minutes'],5)
        self.assertEqual(result['active_premature_clear_count'],1)
        self.assertEqual(result['all_clear_count'],1)
        self.assertTrue(row['recovery_precleared_before_truth_end'])
        self.assertIsNone(row['recovery_first_negative_delay_minutes'])
        self.assertEqual(row['recovery_first_negative_censor_reason'],'precleared_before_truth_end')

    def test_first_off_and_stable_off_have_explicit_unknown_horizon(self):
        baseline=np.ones(40);alert=np.ones(40);alert[20:]=0
        baseline[30]=alert[30]=np.nan
        row=self.compare(baseline,alert)['event_details'][0]
        self.assertEqual(row['recovery_first_negative_delay_minutes'],5)
        self.assertEqual(row['recovery_stable_negative_delay_minutes'],16)
        self.assertEqual(row['recovery_horizon_unknown_minutes'],1)
        self.assertEqual(row['recovery_horizon'],fixture()[0].decision_tick.iloc[0]+pd.Timedelta(minutes=40))

    def test_no_off_is_unresolved_not_zero(self):
        row=self.compare(np.ones(40),np.ones(40))['event_details'][0]
        self.assertIsNone(row['recovery_first_negative_delay_minutes'])
        self.assertEqual(row['recovery_first_negative_censor_reason'],'no_negative')
        self.assertEqual(row['recovery_stable_negative_censor_reason'],'no_stable_negative')

    def test_raw_new_positive_is_not_policy_reactivation(self):
        before=np.zeros(40);before[5:10]=1;before[20:25]=1
        result=self.compare(before,before)
        self.assertEqual(result['all_reactivation_count'],0)
        after=before.copy();before[5:25]=1;after[10:20]=0
        result=self.compare(before,after)
        self.assertEqual(result['all_reactivation_count'],1)

    def test_unknown_cannot_be_converted_to_negative_or_new_positive(self):
        before=np.ones(40);before[20]=np.nan;after=before.copy();after[20]=0
        with self.assertRaisesRegex(AssertionError,'availability'):self.compare(before,after)
        before=np.zeros(40);after=before.copy();after[20]=1
        with self.assertRaisesRegex(AssertionError,'positive'):self.compare(before,after)


if __name__=='__main__':
    unittest.main()
