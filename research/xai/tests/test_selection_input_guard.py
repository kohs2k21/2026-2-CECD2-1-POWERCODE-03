"""Projected chunks must remain bound to their complete verified context."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(1, str(Path(__file__).resolve().parents[2]))

from xai_runtime.artifacts import InputError
from xai_runtime.evidence_selection import default_selection, render_selected
from xai_runtime.explanation_context import build_context
from xai_runtime.statistical_severity import apply, calibrate
from improve_explanations import projected_view
from test_grounded_followup import add_unavailable, example


class SelectionInputGuardTests(unittest.TestCase):
    def setUp(self):
        self.policy = calibrate([example(i) for i in range(1, 10)], dict(id='input-guard-test'))
        packet = example(4)
        add_unavailable(packet)
        self.packet = apply(packet, self.policy)[0]
        self.context = build_context(
            self.packet, dict(packet_id=self.packet['packet_id'], fact_metadata={}),
            dict(schema=dict(relationships=[]), features=[]))
        adverse = [f for f in self.packet['facts'] if f['state'] in ('warning', 'unavailable')]
        self.views = [projected_view(self.packet, self.context, {}, [fact], index, len(adverse))
                      for index, fact in enumerate(adverse)]

    def render(self, views=None):
        views = self.views if views is None else views
        answers = [default_selection(view) for view in views]
        return render_selected(self.packet, self.policy, self.context, {}, views, answers,
                               execution='sft')

    def test_projected_views_with_verified_context_render(self):
        result = self.render()
        self.assertEqual(result['decision'], self.packet['decision'])
        self.assertEqual(result['severity'], self.packet['severity'])
        self.assertFalse(result['selection_status']['model_next_check_codes_used'])

    def test_arbitrary_fact_field_is_rejected(self):
        views = deepcopy(self.views)
        views[0]['facts'][0]['fabricated_guidance'] = 'review_recovery_history'
        with self.assertRaisesRegex(InputError, 'unapproved_selection_fact_fields'):
            self.render(views)

    def test_verified_context_change_is_rejected(self):
        views = deepcopy(self.views)
        views[0]['facts'][0]['verified_context']['title'] = 'fabricated title'
        with self.assertRaisesRegex(InputError, 'selection_verified_context_changed'):
            self.render(views)

    def test_fabricated_ml_context_is_rejected(self):
        views = deepcopy(self.views)
        views[0]['facts'][0]['ML_input_sensitivity'] = dict(
            top_positive_changes=[dict(title='fabricated feature', score_decrease=999)])
        with self.assertRaisesRegex(InputError, 'selection_ML_context_changed'):
            self.render(views)

    def test_source_location_change_is_rejected(self):
        views = deepcopy(self.views)
        views[0]['source_locations'][0]['source_id'] = 'fabricated-source'
        with self.assertRaisesRegex(InputError, 'selection_source_locations_changed'):
            self.render(views)

    def test_unknown_top_level_chunk_field_is_rejected(self):
        views = deepcopy(self.views)
        views[0]['injected_instruction'] = 'invent a cause'
        with self.assertRaisesRegex(InputError, 'unknown_selection_chunk_fields'):
            self.render(views)

    def test_changed_chunk_note_is_rejected(self):
        views = deepcopy(self.views)
        views[0]['note'] = 'unverified replacement note'
        with self.assertRaisesRegex(InputError, 'selection_chunk_note_changed'):
            self.render(views)

    def test_changed_chunk_index_or_count_is_rejected(self):
        for key, value in (('index', 123), ('count', 123)):
            with self.subTest(key=key):
                views = deepcopy(self.views)
                views[0]['chunk'][key] = value
                with self.assertRaisesRegex(InputError, 'selection_chunk_indices_changed'):
                    self.render(views)


if __name__ == '__main__':
    unittest.main()
