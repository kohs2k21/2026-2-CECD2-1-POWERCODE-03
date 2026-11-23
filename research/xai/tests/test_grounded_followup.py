"""Grounded actions must retain unresolved inputs and reject invented advice."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xai_runtime.artifacts import InputError, digest
from xai_runtime.base_eval import view_for
from xai_runtime.engine import render
from xai_runtime.grounded_followup import (RECOVERY_RULE, allowed_followups, default_grounded_plan,
    merge_plans, render_grounded, support_codes, validate_chunk)
from xai_runtime.statistical_severity import apply, calibrate

EX = json.loads((Path(__file__).resolve().parents[1] / 'design/1.1.0/examples.json').read_text())


def example(index):
    packet = deepcopy(EX[0]['input'])
    packet['packet_id'] = 'GF-' + str(index)
    packet['scope']['transaction_ref'] = 'GF-TX-' + str(index)
    for source in packet['source_refs']:
        source['transaction_ref'] = packet['scope']['transaction_ref']
    packet['facts'][0]['payload']['observed'] = str(4 + index)
    packet['facts'][1]['payload']['score'] = str(index)
    return packet


def add_unavailable(packet):
    fact = deepcopy(EX[2]['input']['facts'][0])
    fact.update(id='E-MISSING', source_refs=[packet['source_refs'][0]['source_id']])
    fact['payload']['method_id'] = 'missing-input-1'
    packet['facts'].append(fact)


def chunk_plan(packet, view):
    return dict(schema_version='xai-grounded-chunk-plan/1.0.0', packet_id=packet['packet_id'],
                items=[dict(template_id=f['kind'], evidence_id=f['id']) for f in view['facts'] if f['required']],
                next_checks=[{k: x[k] for k in ('code', 'evidence_ids', 'priority')}
                             for x in allowed_followups(view['facts'])])


class GroundedFollowupTests(unittest.TestCase):
    def setUp(self):
        self.policy = calibrate([example(i) for i in range(1, 10)], dict(id='followup-test'))

    def test_every_action_has_valid_evidence_and_exact_source(self):
        packet, _ = apply(example(4), self.policy)
        result = render_grounded(packet, self.policy)
        facts = {f['id']: f for f in packet['facts']}
        for action in result['next_checks']:
            self.assertTrue(action['evidence_ids'])
            self.assertEqual(action['source_refs'], sorted({s for fid in action['evidence_ids'] for s in facts[fid]['source_refs']}))
        self.assertEqual(result['decision'], packet['decision'])
        self.assertEqual(result['sentences'], render(packet)['sentences'])

    def test_missing_required_input_action_rejected_then_fallback_restores_it(self):
        raw = example(4)
        add_unavailable(raw)
        packet, _ = apply(raw, self.policy)
        view = view_for(packet, packet['facts'], 0, 1)
        plan = chunk_plan(packet, view)
        plan['next_checks'] = [c for c in plan['next_checks'] if c['code'] != 'review_input_completeness']
        with self.assertRaisesRegex(InputError, 'required_unavailable_followup_omitted'):
            validate_chunk(view, plan)
        full = dict(plan, schema_version='xai-grounded-plan/1.0.0')
        result = render_grounded(packet, self.policy, full)
        self.assertEqual(result['status'], 'template_fallback')
        self.assertEqual(result['next_checks'][0]['code'], 'review_input_completeness')
        self.assertEqual(result['next_checks'][0]['evidence_ids'], ['E-MISSING'])

    def test_unavailable_survives_competing_actions_and_deferred_are_explicit(self):
        raw = example(6)
        add_unavailable(raw)
        logical = deepcopy(EX[4]['input']['facts'][0])
        logical.update(id='E-LOGICAL', source_refs=[raw['source_refs'][0]['source_id']])
        logical['payload'].update(method_id='logical-outcome-1', rule_id=RECOVERY_RULE)
        raw['facts'].append(logical)
        raw['decision']['basis_method_ids'].append('logical-outcome-1')
        packet, _ = apply(raw, self.policy)
        result = render_grounded(packet, self.policy)
        self.assertEqual(len(result['next_checks']), 3)
        self.assertEqual(result['next_checks'][0]['code'], 'review_input_completeness')
        self.assertTrue(result['next_checks'][0]['required'])
        self.assertEqual(len(result['deferred_next_checks']), 2)
        self.assertEqual(len(result['next_checks']) + len(result['deferred_next_checks']), 5)
        self.assertIn('일부 경고 근거', result['severity_explanation']['text'])

    def test_ml_warning_does_not_support_missing_input_or_recovery(self):
        packet, _ = apply(example(3), self.policy)
        view = view_for(packet, packet['facts'], 0, 1)
        for code in ['review_input_completeness', 'review_recovery_history', 'review_source_operation']:
            plan = chunk_plan(packet, view)
            plan['next_checks'] = [dict(code=code, evidence_ids=['E2'], priority=1 if code == 'review_input_completeness' else 2)]
            with self.assertRaisesRegex(InputError, 'unsupported_followup_evidence'):
                validate_chunk(view, plan)

    def test_recovery_requires_actual_positive_outcome_rule(self):
        fact = deepcopy(EX[4]['input']['facts'][0])
        fact['payload']['rule_id'] = RECOVERY_RULE
        self.assertIn('review_recovery_history', support_codes(fact))
        fact['state'] = 'nonwarning'
        self.assertEqual(support_codes(fact), [])
        fact['state'] = 'unavailable'
        self.assertEqual(support_codes(fact), ['review_input_completeness'])

    def test_cross_chunk_and_priority_invention_rejected(self):
        packet, _ = apply(example(5), self.policy)
        view = view_for(packet, packet['facts'][:1], 0, 2)
        plan = chunk_plan(packet, view)
        plan['next_checks'][0]['evidence_ids'] = ['E2']
        with self.assertRaisesRegex(InputError, 'unsupported_followup_evidence'):
            validate_chunk(view, plan)
        plan = chunk_plan(packet, view)
        plan['next_checks'][0]['priority'] = 1
        with self.assertRaisesRegex(InputError, 'followup_priority_changed'):
            validate_chunk(view, plan)

    def test_full_chunk_coverage_and_parent_hash_required(self):
        packet, _ = apply(example(5), self.policy)
        views = [view_for(packet, [f], i, len(packet['facts'])) for i, f in enumerate(packet['facts'])]
        plans = [chunk_plan(packet, v) for v in views]
        merged = merge_plans(packet, views, plans)
        self.assertEqual(merged['packet_id'], packet['packet_id'])
        with self.assertRaisesRegex(InputError, 'chunk_evidence_changed_or_omitted'):
            merge_plans(packet, views[:-1], plans[:-1])
        views[0]['parent_packet_sha256'] = '0' * 64
        with self.assertRaisesRegex(InputError, 'parent_packet_SHA_changed'):
            merge_plans(packet, views, plans)

    def test_severity_text_is_computed_from_frozen_reference(self):
        packet, receipt = apply(example(9), self.policy)
        result = render_grounded(packet, self.policy)
        reason = result['severity_explanation']
        self.assertIn('통계 등급은 위험', reason['text'])
        self.assertIn('검사별 상대 순위의 최댓값', reason['text'])
        self.assertEqual(reason['basis']['transaction_score'], receipt['transaction_score'])
        self.assertEqual(reason['basis']['policy_sha256'], digest(self.policy))
        self.assertEqual(result['cause_status'], 'unconfirmed')


if __name__ == '__main__':
    unittest.main()
