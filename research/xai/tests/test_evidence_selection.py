"""LLMs select grounded evidence; code owns follow-up guidance."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

from jsonschema import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(1, str(Path(__file__).resolve().parents[2]))

from xai_runtime.artifacts import InputError
from xai_runtime.evidence_selection import (
    VERSION, assess_legacy, default_selection, selection_messages,
    selection_schema, validate_selection,
)
from xai_runtime.statistical_severity import apply, calibrate
from xai_runtime.summary_sft import compact
from test_grounded_followup import add_unavailable, example


class EvidenceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.policy = calibrate([example(i) for i in range(1, 10)], dict(id='selection-test'))

    def view(self, unavailable=False):
        packet = example(4)
        if unavailable:
            add_unavailable(packet)
        packet, _ = apply(packet, self.policy)
        return compact(packet)

    def test_default_selection_is_valid_and_uses_new_version(self):
        view = self.view(unavailable=True)
        plan = default_selection(view)
        self.assertEqual(plan['schema_version'], VERSION)
        self.assertEqual(set(plan), {'schema_version', 'summary_evidence_ids'})
        validate_selection(view, plan)

    def test_schema_enumerates_only_ids_in_this_view(self):
        view = self.view()
        schema = selection_schema(view)
        self.assertEqual(schema['properties']['summary_evidence_ids']['items']['enum'],
                         [fact['id'] for fact in view['facts']])
        self.assertNotIn('next_check_codes', schema['properties'])

    def test_new_contract_rejects_action_selection(self):
        view = self.view()
        plan = default_selection(view)
        plan['next_check_codes'] = ['review_source_operation']
        with self.assertRaises(ValidationError):
            validate_selection(view, plan)

    def test_foreign_evidence_id_is_rejected(self):
        view = self.view()
        plan = default_selection(view)
        plan['summary_evidence_ids'] = ['foreign-evidence']
        with self.assertRaises(InputError):
            validate_selection(view, plan)

    def test_duplicate_evidence_id_is_rejected(self):
        view = self.view()
        plan = default_selection(view)
        plan['summary_evidence_ids'] = [plan['summary_evidence_ids'][0]] * 2
        with self.assertRaises(ValidationError):
            validate_selection(view, plan)

    def test_more_than_three_evidence_ids_is_rejected(self):
        view = self.view()
        while len(view['facts']) < 4:
            fact = deepcopy(view['facts'][0])
            fact['id'] = 'extra-' + str(len(view['facts']))
            view['facts'].append(fact)
        plan = default_selection(view)
        plan['summary_evidence_ids'] = [fact['id'] for fact in view['facts'][:4]]
        with self.assertRaises(ValidationError):
            validate_selection(view, plan)

    def test_warning_and_unavailable_must_both_be_represented(self):
        view = self.view(unavailable=True)
        plan = default_selection(view)
        warning_id = next(f['id'] for f in view['facts'] if f['state'] == 'warning')
        plan['summary_evidence_ids'] = [warning_id]
        with self.assertRaisesRegex(InputError, 'missing_summary_unavailable'):
            validate_selection(view, plan)

    def test_legacy_action_error_does_not_discard_valid_evidence_selection(self):
        view = self.view()
        ids = default_selection(view)['summary_evidence_ids']
        legacy = dict(schema_version='xai-summary-plan/1.0.0',
                      summary_evidence_ids=ids,
                      next_check_codes=['review_recovery_history'])
        result = assess_legacy(view, legacy)
        self.assertTrue(result['selection_valid'])
        self.assertEqual(result['selection']['summary_evidence_ids'], ids)
        self.assertFalse(result['legacy_valid'])
        self.assertEqual(result['guidance_owner'], 'code')
        self.assertEqual(result['ignored_next_check_codes'], ['review_recovery_history'])

    def test_legacy_duplicate_action_error_preserves_selection(self):
        view = self.view()
        ids = default_selection(view)['summary_evidence_ids']
        duplicate = ['review_rule_threshold', 'review_rule_threshold']
        legacy = dict(schema_version='xai-summary-plan/1.0.0',
                      summary_evidence_ids=ids, next_check_codes=duplicate)
        result = assess_legacy(view, legacy)
        self.assertTrue(result['selection_valid'])
        self.assertEqual(result['selection']['summary_evidence_ids'], ids)
        self.assertFalse(result['legacy_valid'])
        self.assertEqual(result['ignored_next_check_codes'], duplicate)

    def test_invalid_legacy_evidence_is_not_rescued_by_ignoring_actions(self):
        view = self.view(unavailable=True)
        legacy = dict(schema_version='xai-summary-plan/1.0.0',
                      summary_evidence_ids=['foreign-id'], next_check_codes=[])
        result = assess_legacy(view, legacy)
        self.assertFalse(result['selection_valid'])
        self.assertIsNone(result['selection'])

    def test_legacy_assessment_and_prompt_do_not_mutate_inputs(self):
        view = self.view()
        legacy = dict(schema_version='xai-summary-plan/1.0.0',
                      summary_evidence_ids=default_selection(view)['summary_evidence_ids'],
                      next_check_codes=['review_recovery_history'])
        before_view, before_plan = deepcopy(view), deepcopy(legacy)
        selection_messages(view)
        assess_legacy(view, legacy)
        self.assertEqual(view, before_view)
        self.assertEqual(legacy, before_plan)


if __name__ == '__main__':
    unittest.main()
