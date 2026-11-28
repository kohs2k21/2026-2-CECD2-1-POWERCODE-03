"""Grounded summary boundaries, detail preservation and code fallback."""
from copy import deepcopy
from pathlib import Path
import sys, unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from xai_runtime.artifacts import InputError
from xai_runtime.summary_sft import compact,teacher,validate_summary,render_summary
from xai_runtime.statistical_severity import apply,calibrate
from test_grounded_followup import example,add_unavailable

class SummaryTests(unittest.TestCase):
    def setUp(self):self.policy=calibrate([example(i) for i in range(1,10)],dict(id='summary-test'))
    def packet(self):
        p=example(4);add_unavailable(p);return apply(p,self.policy)[0]
    def test_teacher_preserves_unknown_warning_and_full_detail(self):
        p=self.packet();v=compact(p);plan=teacher(v);validate_summary(v,plan)
        r=render_summary(p,self.policy,plan)
        self.assertIsNone(r['fallback_reason'])
        self.assertEqual(r['summary'][0]['evidence_id'],'E-MISSING')
        self.assertEqual(r['detail']['decision'],p['decision']);self.assertEqual(r['detail']['severity'],p['severity'])
        self.assertTrue({f['id'] for f in p['facts'] if f['required']}<={s['evidence_id'] for s in r['detail']['sentences']})
    def test_cross_packet_id_rejected(self):
        v=compact(self.packet());p=teacher(v);p['summary_evidence_ids']=['foreign']
        with self.assertRaisesRegex(InputError,'unknown_or_cross'):validate_summary(v,p)
    def test_severity_calibration_does_not_enter_training_input(self):
        p=self.packet();v=compact(p);p['severity']['policy_version']='different-display-policy'
        self.assertEqual(v,compact(p));self.assertNotIn('severity',v)
    def test_unknown_not_silently_omitted(self):
        v=compact(self.packet());p=teacher(v);p['summary_evidence_ids']=[f['id'] for f in v['facts'] if f['state']=='warning'][:1]
        with self.assertRaisesRegex(InputError,'missing_summary_unavailable'):validate_summary(v,p)
    def test_unsupported_advice_rejected(self):
        p,_=apply(example(4),self.policy);v=compact(p);plan=teacher(v);plan['next_check_codes']=['review_recovery_history']
        with self.assertRaisesRegex(InputError,'unsupported'):validate_summary(v,plan)
    def test_bad_plan_falls_back_without_changing_evidence(self):
        r=render_summary(self.packet(),self.policy,dict(bad=True),execution='sft')
        self.assertIsNotNone(r['fallback_reason']);self.assertEqual(r['execution'],'code')
    def test_large_packet_is_deferred_without_truncation(self):
        p=self.packet();seed=deepcopy(p['facts'][0])
        for i in range(20):
            f=deepcopy(seed);f['id']='extra'+str(i);p['facts'].append(f)
        with self.assertRaisesRegex(InputError,'more_than_16'):compact(p)

if __name__=='__main__':unittest.main()
