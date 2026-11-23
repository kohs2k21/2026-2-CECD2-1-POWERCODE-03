"""Statistical grade boundaries, frozen reference and nonnumeric cases."""
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xai_runtime.artifacts import InputError
from xai_runtime.engine import render
from xai_runtime.statistical_severity import apply, calibrate, midrank, quantile_thirds

EXAMPLES = json.loads((Path(__file__).resolve().parents[1] / 'design/1.1.0/examples.json').read_text())


def sample(index):
    packet = deepcopy(EXAMPLES[1]['input'])
    packet['packet_id'] = 'STAT-' + str(index)
    packet['scope']['transaction_ref'] = 'TX-' + str(index)
    for source in packet['source_refs']:
        source['transaction_ref'] = packet['scope']['transaction_ref']
    packet['facts'][0]['payload']['score'] = str(index)
    return packet


class StatisticalGradesTests(unittest.TestCase):
    def setUp(self):
        self.packets = [sample(i) for i in range(1, 10)]
        self.policy = calibrate(self.packets, dict(id='unit-test-warning-reference'))

    def test_tertiles_have_three_groups_without_ties(self):
        labels = [apply(p, self.policy)[0]['severity']['label'] for p in self.packets]
        self.assertEqual(labels, ['참고'] * 3 + ['주의'] * 3 + ['위험'] * 3)

    def test_ties_remain_equal_and_are_not_split_by_identity(self):
        ranks = [midrank([1, 1, 1, 2, 2, 3], x) for x in [1, 1, 2, 2, 3]]
        self.assertEqual(ranks[:2], [Fraction(1, 4)] * 2)
        self.assertEqual(ranks[2:4], [Fraction(2, 3)] * 2)
        self.assertEqual(quantile_thirds([1, 1, 1, 1, 2, 3]), (1, 1))
        p = sample(5)
        q = deepcopy(p)
        q['packet_id'] = 'A-DIFFERENT-ID'
        self.assertEqual(apply(p, self.policy)[0]['severity'], apply(q, self.policy)[0]['severity'])

    def test_new_batch_does_not_force_a_third_to_be_low_or_high(self):
        levels = [apply(sample(i), self.policy)[0]['severity']['level'] for i in [100, 101, 102]]
        self.assertEqual(levels, ['critical'] * 3)

    def test_multiple_rows_contribute_one_channel_reference_value_per_transaction(self):
        packets = deepcopy(self.packets)
        for packet in packets:
            fact = deepcopy(packet['facts'][0])
            fact['id'] += '-row2'
            fact['payload']['method_id'] += ':row2'
            packet['facts'].append(fact)
            packet['decision']['basis_method_ids'].append(fact['payload']['method_id'])
        policy = calibrate(packets, dict(id='repeated-rows'))
        self.assertEqual(len(policy['channels']), 1)
        self.assertEqual(len(next(iter(policy['channels'].values()))['warning_strengths']), 9)

    def test_detection_and_explanation_values_are_unchanged(self):
        packet = self.packets[3]
        original = deepcopy(packet)
        graded, receipt = apply(packet, self.policy)
        self.assertEqual(packet, original)
        self.assertEqual(render(graded)['sentences'], render(packet)['sentences'])
        self.assertEqual(graded['decision'], packet['decision'])
        self.assertEqual(receipt['transaction_score'], '7/18')

    def test_nonnumeric_warning_remains_unrated_without_fabricated_ranking(self):
        logical = deepcopy(EXAMPLES[4]['input'])
        graded, receipt = apply(logical, self.policy)
        self.assertEqual(graded['severity']['status'], 'unrated')
        self.assertEqual(receipt['unranked_warning_evidence'][0]['reason'], 'no_ordered_numeric_strength')

    def test_changed_model_is_not_silently_ranked_with_old_reference(self):
        packet = sample(4)
        packet['facts'][0]['payload']['model_sha256'] = 'a' * 64
        graded, receipt = apply(packet, self.policy)
        self.assertEqual(graded['severity']['status'], 'unrated')
        self.assertEqual(receipt['ranked_evidence'], [])
        self.assertIn('pinned_channel', receipt['unranked_warning_evidence'][0]['reason'])

    def test_policy_mutation_and_tiny_reference_are_rejected(self):
        policy = deepcopy(self.policy)
        policy['cutoffs']['p67'] = '0/1'
        with self.assertRaisesRegex(InputError, 'policy_content_changed'):
            apply(sample(4), policy)
        with self.assertRaisesRegex(InputError, 'no_usable_reference_channels'):
            calibrate(self.packets[:2], dict(id='tiny'))

    def test_nonwarning_and_unavailable_are_never_promoted_to_alerts(self):
        for index in [2, 6]:
            packet = deepcopy(EXAMPLES[index]['input'])
            graded, receipt = apply(packet, self.policy)
            self.assertEqual(graded['decision'], packet['decision'])
            self.assertEqual(graded['severity']['status'], 'not_applicable')
            self.assertEqual(receipt['ranked_evidence'], [])

    def test_smaller_is_more_anomalous_direction(self):
        packets = deepcopy(self.packets)
        for index, packet in enumerate(packets, 1):
            payload = packet['facts'][0]['payload']
            payload.update(score=str(-index), threshold='0', operator='lt', score_direction='smaller_is_more_anomalous')
        policy = calibrate(packets, dict(id='smaller-direction'))
        self.assertEqual(apply(packets[0], policy)[0]['severity']['level'], 'info')
        self.assertEqual(apply(packets[-1], policy)[0]['severity']['level'], 'critical')


if __name__ == '__main__':
    unittest.main()
