import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from specmodel.dataset import Dataset
from specmodel.calculations import compile_feature
from specmodel.validation import validate_feature
from test_calculations import prop, col, op, lit, P

class RatioRequestDiagnostics(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ds = Dataset(P / 'examples/capture.json')

    def check(self, p, selection=False):
        req = {'question': 'Per CASE_LOG row return a complete Boolean indicating whether A/B cannot be evaluated.', 'analysis_request': {'complete_boolean': True, 'output_table': 'CASE_LOG', 'required_columns': [['CASE_LOG', 'A'], ['CASE_LOG', 'B']]}}
        if selection:
            req['analysis_request']['selected_equals'] = {'table': 'CASE_LOG', 'column': 'CLASS', 'value': 'absent'}
        c = compile_feature(self.ds, p, 'CPU')
        self.assertTrue(c['passed'], c.get('errors'))
        return (c, validate_feature(self.ds, req, {'schema': 'feature5.1', 'candidates': [p], 'stop_reason': None}, c))

    def test_missing_only_union_cannot_hide_zero_denominator(self):
        (c, e) = self.check(prop(op('or', op('is_missing', col('A')), op('is_missing', col('B')))))
        self.assertTrue(any((x['code'] == 'public_ratio_unavailability_counterexample' for x in e)))

    def test_equivalent_guard_formula_is_allowed(self):
        (a, b) = (col('A'), col('B'))
        x = op('if', op('or', op('is_missing', a), op('is_missing', b)), lit(True), op('eq', b, lit(0)))
        (c, e) = self.check(prop(x))
        self.assertFalse(e, e)

    def test_empty_selection_is_not_permission_for_wrong_formula(self):
        filt = op('eq', col('CLASS'), lit('absent'))
        x = op('if', filt, op('or', op('is_missing', col('A')), op('is_missing', col('B'))), lit(False))
        (c, e) = self.check(prop(x, filt), True)
        self.assertEqual(c['values'], [])
        self.assertFalse(c['compiled_feature']['possible_states']['unavailable_possible'])
        self.assertTrue(any((x['code'] == 'public_ratio_unavailability_counterexample' for x in e)))

    def test_nested_result_missing_correct_without_source_rewrite(self):
        (c, e) = self.check(prop(op('is_missing', op('divide', col('A'), col('B')))))
        self.assertFalse(e, e)
        self.assertEqual(c['values'], [False, True, True, True, False, True, False])
if __name__ == '__main__':
    unittest.main()
