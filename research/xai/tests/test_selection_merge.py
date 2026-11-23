"""Whole-transaction guidance, chunk binding and selection fallback boundaries."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xai_runtime.artifacts import InputError, digest
from xai_runtime.evidence_selection import default_selection, render_selected
from xai_runtime.explanation_context import build_context
from xai_runtime.statistical_severity import apply, calibrate
from improve_explanations import projected_view
from test_grounded_followup import example, add_unavailable


class SelectionMergeTests(unittest.TestCase):
    def setUp(self):
        self.policy = calibrate([example(i) for i in range(1, 10)], dict(id='merge-test'))
        p = example(4)
        add_unavailable(p)
        self.packet = apply(p, self.policy)[0]
        self.context = build_context(self.packet, dict(packet_id=p['packet_id'], fact_metadata={}),
                                     dict(schema=dict(relationships=[]), features=[]))
        adverse = [f for f in self.packet['facts'] if f['state'] in ('warning', 'unavailable')]
        self.views = [projected_view(self.packet, self.context, {}, [f], i, len(adverse))
                      for i, f in enumerate(adverse)]

    def render(self, views=None, answers=None):
        views = self.views if views is None else views
        answers = [default_selection(v) for v in views] if answers is None else answers
        return render_selected(self.packet, self.policy, self.context, {}, views, answers,
                               execution='sft')

    def test_split_preserves_all_unknown_checks_and_packet(self):
        before = deepcopy(self.packet)
        result = self.render()
        self.assertEqual(result['decision'], self.packet['decision'])
        self.assertEqual(result['severity'], self.packet['severity'])
        self.assertEqual(self.packet, before)
        self.assertFalse(result['selection_status']['model_next_check_codes_used'])
        self.assertTrue(any(c['code'] == 'review_input_completeness' for c in result['next_checks']))
        self.assertTrue({f['id'] for f in self.packet['facts'] if f['required']} <=
                        {d['evidence_id'] for d in result['details']})

    def test_invalid_selection_falls_back_without_losing_unknowns(self):
        answers = [default_selection(v) for v in self.views]
        answers[0]['summary_evidence_ids'] = ['foreign']
        result = self.render(answers=answers)
        self.assertEqual(result['selection_status']['code_selection_fallback_chunks'], [0])
        self.assertTrue(any(c['code'] == 'review_input_completeness' for c in result['next_checks']))

    def test_model_action_field_rejected_and_never_rendered(self):
        answers = [default_selection(v) for v in self.views]
        answers[0]['next_check_codes'] = ['invented_network_failure']
        result = self.render(answers=answers)
        self.assertEqual(result['selection_status']['code_selection_fallback_chunks'], [0])
        self.assertNotIn('invented_network_failure', str(result))

    def test_missing_or_duplicate_chunk_is_source_error_not_fallback(self):
        for views in (self.views[:-1], self.views + [self.views[0]]):
            with self.subTest(views=len(views)):
                with self.assertRaisesRegex(InputError, 'selection_chunks_omit_duplicate'):
                    self.render(views=views)

    def test_changed_parent_or_numeric_payload_rejected(self):
        bad = deepcopy(self.views)
        bad[0]['parent_packet_sha256'] = '0' * 64
        with self.assertRaisesRegex(InputError, 'selection_chunk_parent_changed'):
            self.render(views=bad)
        bad = deepcopy(self.views)
        bad[0]['facts'][0]['payload']['invented'] = 'changed'
        with self.assertRaisesRegex(InputError, 'selection_chunk_evidence_changed'):
            self.render(views=bad)


if __name__ == '__main__':
    unittest.main()
