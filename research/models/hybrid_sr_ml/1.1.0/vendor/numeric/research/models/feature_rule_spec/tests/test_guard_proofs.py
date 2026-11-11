import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from specmodel.conditional_expression import state_proof

def op(n, *a):
    return {'op': n, 'args': list(a)}

def col(n):
    return {'op': 'column', 'table': 'ANY_TABLE', 'column': n}

def lit(n):
    return {'op': 'literal', 'value': n}

class GuardProof(unittest.TestCase):

    def test_false_OR_missing_guard_certifies_both_present(self):
        (a, b, c) = map(col, ['A', 'B', 'C'])
        x = op('if', op('or', op('is_missing', a), op('is_missing', b)), lit(True), op('if', op('is_missing', c), lit(True), op('eq', op('subtract', b, c), lit(0))))
        self.assertFalse(state_proof(x)['unavailable_possible'])

    def test_true_OR_missing_guard_does_not_certify_both_present(self):
        (a, b) = map(col, ['A', 'B'])
        x = op('if', op('or', op('is_missing', a), op('is_missing', b)), op('gt', a, lit(0)), lit(False))
        self.assertTrue(state_proof(x)['unavailable_possible'])

    def test_AND_two_present_under_negated_missing(self):
        (a, b) = map(col, ['A', 'B'])
        guard = op('and', op('not', op('is_missing', a)), op('not', op('is_missing', b)))
        self.assertFalse(state_proof(op('if', guard, op('gt', op('subtract', a, b), lit(0)), lit(False)))['unavailable_possible'])
if __name__ == '__main__':
    unittest.main()
