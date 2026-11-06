import unittest, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from specmodel.dataset import Dataset
from specmodel.calculations import compile_feature
from specmodel.validation import validate_feature
from specmodel.io import read

class ComparisonScope(unittest.TestCase):

    def test_ratio_inside_comparison_is_not_ratio_only_diagnostic(self):
        p = Path(__file__).resolve().parents[1] / 'examples/E05_comparison_result_state'
        d = Dataset(p / 'capture.json')
        q = read(p / 'request.json')

        def op(n, *a):
            return {'op': n, 'args': list(a)}

        def col(n):
            return {'op': 'column', 'table': 'COMPARISON_ROWS', 'column': n}
        prop = {'proposal_id': 'CPU_only', 'purpose': 'Inspect comparison availability.', 'meaning': 'True when the numeric comparison is unavailable; false when its true or false outcome can be evaluated.', 'needed_evidence': [], 'plan': {'expression': op('is_missing', op('gt', op('divide', col('NUM'), col('DEN')), col('CUT'))), 'grain': {'table': 'COMPARISON_ROWS', 'keys': ['ROW_KEY']}, 'population': {'table': 'COMPARISON_ROWS', 'filter': None}, 'group_by': []}}
        c = compile_feature(d, prop, 'CPU')
        self.assertTrue(c['passed'], c.get('errors'))
        self.assertEqual(c['values'], [False, False, True, True, True])
        self.assertFalse(validate_feature(d, q, {'schema': 'feature5.1', 'candidates': [prop], 'stop_reason': None}, c))
if __name__ == '__main__':
    unittest.main()
