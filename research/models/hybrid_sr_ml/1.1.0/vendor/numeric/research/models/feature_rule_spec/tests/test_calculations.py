"""CPU contrasts, not Model performance tests."""
import unittest, tempfile, csv, copy, json, os, time
from pathlib import Path
from decimal import Decimal
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from specmodel.dataset import Dataset
from specmodel.calculations import compile_feature
from specmodel.io import write, sha, InputFailure
from specmodel.rule_logic import counts
P = Path(__file__).resolve().parents[1]

def op(n, *a):
    return {'op': n, 'args': list(a)}

def col(c):
    return {'op': 'column', 'table': 'CASE_LOG', 'column': c}

def lit(v):
    return {'op': 'literal', 'value': v}

def prop(x, selection=None, table='CASE_LOG'):
    return {'proposal_id': 'same_Model_local_alias', 'purpose': 'CPU controlled calculation.', 'meaning': 'Offline controlled calculation; no operational claim.', 'needed_evidence': [], 'plan': {'expression': x, 'grain': {'table': table, 'keys': ['ROW_ID']}, 'population': {'table': table, 'filter': selection}, 'group_by': []}}

class Calculations(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.d = Dataset(P / 'examples/capture.json')

    def compile(self, x, selection=None):
        c = compile_feature(self.d, prop(x, selection), 'CPU')
        self.assertTrue(c['passed'], c.get('errors'))
        return c

    def test_direct_ratio_state_and_normal_zero(self):
        c = self.compile(op('is_missing', op('divide', col('A'), col('B'))))
        self.assertEqual(c['values'], [False, True, True, True, False, True, False])
        n = self.compile(op('divide', col('A'), col('B')))
        self.assertEqual(n['values'][0], 0)
        self.assertEqual(n['values'][4], Decimal('-3'))
        self.assertEqual(counts(n['values'], {'op': 'is_missing'})['evaluable'], 7)
        self.assertEqual(counts(n['values'], {'op': 'gt', 'value': 1})['evaluable'], 3)

    def test_parent_match_0_1_many_and_partial_full_missing(self):
        m = {'op': 'parent_match_count', 'relationship_id': 'declared_log_header'}
        c = self.compile(m)
        self.assertEqual(c['values'], [1, 2, 0, None, 1, None, 1])
        g = self.compile(op('if', op('is_missing', m), lit(True), op('eq', m, lit(0))))
        self.assertEqual(g['values'], [False, False, True, True, False, True, False])
        self.assertFalse(g['compiled_feature']['possible_states']['unavailable_possible'])

    def test_strict_OR_not_changed(self):
        m = {'op': 'parent_match_count', 'relationship_id': 'declared_log_header'}
        c = self.compile(op('or', op('is_missing', m), op('eq', m, lit(0))))
        self.assertIsNone(c['values'][3])
        self.assertIsNone(c['values'][5])

    def test_selection_excludes_no_false_fill(self):
        x = op('is_missing', op('divide', col('A'), col('B')))
        c = self.compile(x, op('eq', col('CLASS'), lit('absent')))
        self.assertEqual(c['values'], [])
        s = c['compiled_feature']['measurement']['summary']
        self.assertEqual(s['row_count'], 0)
        self.assertEqual(s['population_rows_before_selection'], 7)
        self.assertEqual(s['selection_unavailable_count'], 1)

    def test_unknown_condition_is_not_false(self):
        c = self.compile(op('if', op('gt', col('A'), lit(0)), lit(True), lit(False)))
        self.assertIsNone(c['values'][2])
        self.assertIsNone(c['values'][5])

    def test_Boolean_is_not_numeric(self):
        for x in [op('if', col('A'), lit(True), lit(False)), op('divide', lit(True), col('B')), {'op': 'in', 'args': [col('A')], 'values': [True, False]}]:
            self.assertFalse(compile_feature(self.d, prop(x), 'CPU')['passed'])

    def test_unselected_invalid_source_and_unsupported_op_rejected(self):
        for x in [op('if', lit(True), lit(1), col('MISSING_SOURCE')), op('if', lit(True), lit(1), op('abs', col('A')))]:
            self.assertFalse(compile_feature(self.d, prop(x), 'CPU')['passed'])

    def test_lazy_execution_exception_not_hidden(self):
        e = self.d.engine
        old = e.evaluate

        def broken(n, *a, **k):
            if n['op'] == 'divide':
                raise RuntimeError('CPU selected failure')
            return old(n, *a, **k)
        e.evaluate = broken
        try:
            with self.assertRaises(RuntimeError):
                e.evaluate(op('if', lit(True), op('divide', col('A'), col('B')), lit(0)), e.rows['CASE_LOG'][0], 'CASE_LOG', {})
            self.assertEqual(e.evaluate(op('if', lit(False), op('divide', col('A'), col('B')), lit(0)), e.rows['CASE_LOG'][0], 'CASE_LOG', {}), 0)
        finally:
            e.evaluate = old

    def test_parent_child_rows_not_implicitly_aligned(self):
        x = op('subtract', {'op': 'child_count', 'relationship_id': 'declared_log_header'}, {'op': 'parent_match_count', 'relationship_id': 'declared_log_header'})
        self.assertFalse(compile_feature(self.d, prop(x), 'CPU')['passed'])

    def test_alias_collision_and_description_revisions(self):
        a = self.compile(op('is_missing', col('A')))
        b = self.compile(op('is_missing', col('B')))
        self.assertNotEqual(a['compiled_feature']['id'], b['compiled_feature']['id'])
        q = prop(op('is_missing', col('A')))
        q['meaning'] = 'True if A is missing; false if present.'
        c = compile_feature(self.d, q, 'CPU_description')
        self.assertEqual(a['compiled_feature']['definition_sha'], c['compiled_feature']['definition_sha'])
        self.assertEqual(a['compiled_feature']['measurement']['measurement_sha256'], c['compiled_feature']['measurement']['measurement_sha256'])
        self.assertNotEqual(a['compiled_feature']['description_revision']['sha'], c['compiled_feature']['description_revision']['sha'])
if __name__ == '__main__':
    unittest.main()
