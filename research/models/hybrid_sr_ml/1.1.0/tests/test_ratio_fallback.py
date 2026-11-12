"""CPU-only boundary tests for profile-bound actual-alternative evaluation."""
from copy import deepcopy
import csv
import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest

MODEL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODEL))
from hybrid.ratio_fallback_policy import (  # noqa: E402
    ALTERNATIVES, COUNTER_COLUMNS, COUNTER_RULES, decide, finite_count, make_registry,
)
from hybrid.runner import apply_ratio_alternatives  # noqa: E402

PROFILE_PATH = MODEL / 'vendor/numeric/research/integration/feature_rule_ml/shared42/release/profile.json'


def read_profile():
    return json.loads(PROFILE_PATH.read_text())


def mutate_rule(profile, rule_id):
    return next(r for r in profile['definition']['rules'] if r['id'] == rule_id)


def mutate_feature(profile, feature_id):
    return next(x['feature'] for x in profile['definition']['features'] if x['id'] == feature_id)


class RegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = read_profile()

    def test_registry_has_exactly_fourteen_frozen_bindings(self):
        registry = make_registry(self.profile)
        self.assertEqual(len(registry['bindings']), 14)
        self.assertEqual({x['rule']['id'] for x in registry['bindings']}, set(ALTERNATIVES))
        self.assertEqual(registry['mode'], 'actual_alternative_evaluation')
        self.assertFalse(registry['not_applicable_used'])
        self.assertIsNone(registry['ratio_value_imputation'])
        self.assertFalse(registry['thresholds_recalibrated'])
        self.assertFalse(registry['ML_features_changed'])

    def test_counter_columns_are_explicit_and_cover_all_six_ratio_rules(self):
        self.assertEqual(COUNTER_RULES, set(COUNTER_COLUMNS))
        self.assertEqual(COUNTER_COLUMNS['rule_021:baseline'], ('TOTAL_COUNT', 'SUCCESS_COUNT'))
        self.assertEqual(COUNTER_COLUMNS['rule_024:baseline'], ('ERROR_COUNT', 'SUCCESS_COUNT'))
        self.assertEqual(COUNTER_COLUMNS['rule_023:baseline'], ('ERROR_COUNT', 'TOTAL_COUNT'))
        self.assertEqual(COUNTER_COLUMNS['rule_025:baseline'], ('SUCCESS_COUNT', 'TOTAL_COUNT'))

    def test_changed_ratio_feature_sha_is_rejected(self):
        p = deepcopy(self.profile)
        mutate_feature(p, 'rule_021')['sha'] = '0' * 64
        with self.assertRaises(ValueError):
            make_registry(p)

    def test_changed_alternative_expression_is_rejected(self):
        p = deepcopy(self.profile)
        f = mutate_feature(p, 'rule_015')
        f['expression']['args'][1]['column'] = 'ERROR_COUNT'
        with self.assertRaises(ValueError):
            make_registry(p)

    def test_changed_ratio_denominator_binding_is_rejected(self):
        p = deepcopy(self.profile)
        f = mutate_feature(p, 'rule_021')
        f['expression']['args'][1]['column'] = 'ERROR_COUNT'
        with self.assertRaises(ValueError):
            make_registry(p)

    def test_alternative_population_scope_mismatch_is_rejected(self):
        p = deepcopy(self.profile)
        mutate_feature(p, 'rule_041')['population']['filter']['args'][1]['value'] = 'F'
        with self.assertRaises(ValueError):
            make_registry(p)

    def test_unverified_alternative_baseline_is_rejected(self):
        p = deepcopy(self.profile)
        mutate_rule(p, 'rule_015:baseline')['baseline']['state'] = 'deferred'
        with self.assertRaises(ValueError):
            make_registry(p)

    def test_alternative_baseline_sample_under_minimum_is_rejected(self):
        p = deepcopy(self.profile)
        mutate_rule(p, 'rule_015:baseline')['baseline']['reference_valid_n'] = 1
        with self.assertRaises(ValueError):
            make_registry(p)


class FiniteCountTests(unittest.TestCase):
    def test_known_finite_nonnegative_count_is_decimal(self):
        self.assertEqual(str(finite_count('12')), '12')
        self.assertEqual(str(finite_count(0)), '0')

    def test_missing_nonfinite_boolean_invalid_or_negative_count_is_unknown(self):
        for value in (None, 'NaN', 'Infinity', float('inf'), True, 'bad', -1, '-0.01'):
            with self.subTest(value=value):
                self.assertIsNone(finite_count(value))


class DecisionTests(unittest.TestCase):
    def test_positive_denominator_preserves_original_warning_or_nonwarning(self):
        for state in ('warning', 'nonwarning', 'unavailable'):
            with self.subTest(state=state):
                r = decide('rule_021:baseline', 3, 2, state, alternative_state='warning')
                self.assertEqual((r.state, r.used_fallback, r.reason),
                                 (state, False, 'original_positive_denominator_comparison'))
                self.assertIsNone(r.actual_rule)

    def test_nonallowlisted_rule_is_returned_unchanged(self):
        r = decide('unbound:baseline', None, float('nan'), 'warning')
        self.assertEqual((r.state, r.used_fallback, r.reason),
                         ('warning', False, 'not_allowlisted'))

    def test_nonzero_denominator_rule_cannot_enter_fallback(self):
        with self.assertRaises(ValueError):
            decide('rule_021:baseline', 1, 0, 'nonwarning', alternative_state='nonwarning')

    def test_existing_alternative_warning_is_used(self):
        r = decide('rule_030:baseline', 4, 0, 'unavailable', alternative_state='warning')
        self.assertEqual((r.state, r.used_fallback, r.actual_rule),
                         ('warning', True, 'fallback:rule_030:baseline=>rule_029:baseline'))

    def test_existing_alternative_nonwarning_is_used(self):
        r = decide('rule_030:baseline', 1, 0, 'unavailable', alternative_state='nonwarning')
        self.assertEqual((r.state, r.used_fallback), ('nonwarning', True))

    def test_missing_nonfinite_and_negative_ratio_operands_stay_unknown(self):
        cases = [(None, 0), (0, None), ('NaN', 0), (1, 'Infinity'), (-1, 0), (0, -1)]
        for numerator, denominator in cases:
            with self.subTest(numerator=numerator, denominator=denominator):
                r = decide('rule_030:baseline', numerator, denominator, 'unavailable',
                           alternative_state='warning')
                self.assertEqual((r.state, r.used_fallback), ('unavailable', False))

    def test_partition_mismatch_warns_and_records_diagnostic_rule(self):
        r = decide('rule_021:baseline', 2, 0, 'unavailable',
                   alternative_state='nonwarning',
                   counters={'TOTAL_COUNT': 2, 'SUCCESS_COUNT': 0, 'ERROR_COUNT': 1})
        self.assertEqual((r.state, r.used_fallback, r.reason, r.actual_rule),
                         ('warning', True, 'reported_total_differs_from_success_plus_error',
                          'fallback:counter_partition_consistency'))

    def test_consistent_total_zero_has_no_partition_warning_and_uses_existing_difference(self):
        r = decide('rule_021:baseline', 0, 0, 'unavailable',
                   alternative_state='warning',
                   counters={'TOTAL_COUNT': 0, 'SUCCESS_COUNT': 0, 'ERROR_COUNT': 0})
        self.assertEqual((r.state, r.used_fallback, r.actual_rule),
                         ('warning', True, 'fallback:rule_021:baseline=>rule_015:baseline'))

    def test_consistent_total_zero_for_023_025_is_nonwarning_without_alternative(self):
        zeros = {'TOTAL_COUNT': 0, 'SUCCESS_COUNT': 0, 'ERROR_COUNT': 0}
        for rid, n, d in [('rule_023:baseline', 0, 0), ('rule_025:baseline', 0, 0)]:
            with self.subTest(rule=rid):
                r = decide(rid, n, d, 'unavailable', counters=zeros)
                self.assertEqual((r.state, r.used_fallback, r.actual_rule),
                                 ('nonwarning', True, 'fallback:counter_partition_consistency'))

    def test_all_three_counter_values_are_required(self):
        r = decide('rule_023:baseline', 0, 0, 'unavailable', counters=None)
        self.assertEqual((r.state, r.used_fallback, r.reason),
                         ('unavailable', False, 'need_all_three_counter_values'))

    def test_any_missing_nonfinite_or_negative_counter_stays_unknown(self):
        valid = {'TOTAL_COUNT': 2, 'SUCCESS_COUNT': 1, 'ERROR_COUNT': 1}
        cases = [
            {'TOTAL_COUNT': None, 'SUCCESS_COUNT': 1, 'ERROR_COUNT': 1},
            {'TOTAL_COUNT': 2, 'SUCCESS_COUNT': float('nan'), 'ERROR_COUNT': 1},
            {'TOTAL_COUNT': 2, 'SUCCESS_COUNT': 1, 'ERROR_COUNT': -1},
        ]
        for counters in cases:
            with self.subTest(counters=counters):
                r = decide('rule_021:baseline', 2, 0, 'unavailable',
                           alternative_state='nonwarning', counters=counters)
                self.assertEqual((r.state, r.used_fallback), ('unavailable', False))

    def test_counter_operand_mapping_mismatch_stays_unknown(self):
        r = decide('rule_024:baseline', 2, 0, 'unavailable', alternative_state='warning',
                   counters={'TOTAL_COUNT': 2, 'SUCCESS_COUNT': 1, 'ERROR_COUNT': 1})
        self.assertEqual((r.state, r.used_fallback, r.reason),
                         ('unavailable', False, 'counter_operand_binding_mismatch'))

    def test_counter_column_mapping_matches_registry_and_fallback(self):
        for rid in sorted(COUNTER_RULES):
            with self.subTest(rule=rid):
                cols = COUNTER_COLUMNS[rid]
                self.assertEqual(len(cols), 2)
                self.assertIn(cols[0], ('TOTAL_COUNT', 'SUCCESS_COUNT', 'ERROR_COUNT'))
                self.assertIn(cols[1], ('TOTAL_COUNT', 'SUCCESS_COUNT', 'ERROR_COUNT'))

    def test_no_rule_state_is_not_applicable(self):
        r = decide('rule_030:baseline', 0, 0, 'unavailable', alternative_state='nonwarning')
        self.assertNotEqual(r.state, 'not_applicable')


class RunnerBindingTests(unittest.TestCase):
    def make_guard_file(self, folder, rows):
        path = Path(folder) / 'guarded.csv.gz'
        fields = list(rows[0])
        with gzip.open(path, 'wt', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def valid_guard_row(self, **updates):
        row = {
            'transaction_id': 'tx1', 'source_row_id': 'src1',
            'numeric_SR': 'nonwarning', 'strict_numeric_SR': 'unavailable',
            'fired_numeric_rule_ids': '["rule_015:baseline"]',
            'unavailable_numeric_rule_ids': '[]',
            'strict_unavailable_numeric_rule_ids': '["rule_021:baseline"]',
            'fallback_for_rule_ids': '["rule_021:baseline"]',
            'fallback_actual_rule_ids': '["fallback:rule_021:baseline=>rule_015:baseline"]',
            'fallback_evaluation_count': '1', 'fallback_evaluation_unknown_count': '0',
            'numeric_evaluation_scope': 'original_and_explicit_alternative_comparisons',
            'policy_version': 'count-ratio-alternative-evaluation1.0',
        }
        row.update(updates)
        return row

    def test_runner_merges_result_after_exact_coverage_and_identity_match(self):
        entities = [{'entity_id': 'e1', 'source_row_id': 'src1', 'transaction_id': 'tx1'}]
        records = {'e1': {'entity_id': 'e1', 'numeric_SR': 'unavailable', 'ML': 'warning', 'logical_SR': 'nonwarning'}}
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix='ratio_binding_') as folder:
            path = self.make_guard_file(folder, [self.valid_guard_row()])
            result = apply_ratio_alternatives(records, entities, path)
        self.assertEqual(result['e1']['numeric_SR'], 'nonwarning')
        self.assertEqual(result['e1']['strict_numeric_SR'], 'unavailable')
        self.assertEqual(result['e1']['ML'], 'warning')
        self.assertEqual(result['e1']['logical_SR'], 'nonwarning')
        self.assertEqual(result['e1']['fallback_actual_rule_ids'], '["fallback:rule_021:baseline=>rule_015:baseline"]')

    def test_runner_rejects_missing_or_extra_coverage(self):
        entities = [{'entity_id': 'e1', 'source_row_id': 'src1', 'transaction_id': 'tx1'}]
        records = {'e1': {'numeric_SR': 'unavailable'}}
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix='ratio_binding_') as folder:
            missing = self.make_guard_file(folder, [self.valid_guard_row(source_row_id='extra')])
            with self.assertRaises(ValueError):
                apply_ratio_alternatives(records, entities, missing)
            extra = self.make_guard_file(folder, [self.valid_guard_row(), self.valid_guard_row(source_row_id='extra')])
            with self.assertRaises(ValueError):
                apply_ratio_alternatives(records, entities, extra)

    def test_runner_rejects_duplicate_source_identity(self):
        entities = [{'entity_id': 'e1', 'source_row_id': 'src1', 'transaction_id': 'tx1'}]
        records = {'e1': {'numeric_SR': 'unavailable'}}
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix='ratio_binding_') as folder:
            path = self.make_guard_file(folder, [self.valid_guard_row(), self.valid_guard_row()])
            with self.assertRaises(ValueError):
                apply_ratio_alternatives(records, entities, path)

    def test_runner_rejects_strict_state_or_transaction_mismatch(self):
        entities = [{'entity_id': 'e1', 'source_row_id': 'src1', 'transaction_id': 'tx1'}]
        records = {'e1': {'numeric_SR': 'unavailable'}}
        for update in ({'strict_numeric_SR': 'nonwarning'}, {'transaction_id': 'tx-other'}):
            with self.subTest(update=update), tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix='ratio_binding_') as folder:
                path = self.make_guard_file(folder, [self.valid_guard_row(**update)])
                with self.assertRaises(ValueError):
                    apply_ratio_alternatives(records, entities, path)

    def test_runner_rejects_missing_native_record(self):
        entities = [{'entity_id': 'e1', 'source_row_id': 'src1', 'transaction_id': 'tx1'}]
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix='ratio_binding_') as folder:
            path = self.make_guard_file(folder, [self.valid_guard_row()])
            with self.assertRaises(ValueError):
                apply_ratio_alternatives({}, entities, path)


if __name__ == '__main__':
    unittest.main(verbosity=2)
