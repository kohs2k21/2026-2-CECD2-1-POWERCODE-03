import unittest
from shared_model_v5.protocol import TASKS,gates
from model_evaluation.data import SCENARIOS
from model_evaluation.scope import ACTIVE_TASKS

def rows(values=(.3,.5,.6,.7,.8),fpr=.049):
    return [dict(task=t,f1=f,normal_false_positive_rate=fpr)for t,f in zip(TASKS,values)]

class AcceptanceTests(unittest.TestCase):
    def test_all_five_gate(self):self.assertTrue(gates(rows())['all_met'])
    def test_zero_task_is_rejected(self):self.assertFalse(gates(rows((0,.8,.8,.8,.8)))['all_met'])
    def test_minimum_not_hidden_by_median(self):self.assertFalse(gates(rows((.29,.8,.8,.8,.8)))['all_met'])
    def test_median_is_across_tasks(self):self.assertFalse(gates(rows((.3,.4,.59,.9,.9)))['all_met'])
    def test_five_percent_is_strict(self):self.assertFalse(gates(rows(fpr=.05))['all_met'])
    def test_each_task_fpr_not_only_pooled(self):
        r=rows();r[0]['normal_false_positive_rate']=.051;self.assertFalse(gates(r)['all_met'])
    def test_missing_task_rejected(self):
        with self.assertRaises(ValueError):gates(rows()[:-1])
    def test_active_scope(self):self.assertEqual(TASKS,ACTIVE_TASKS);self.assertNotIn('mixed_hard',TASKS);self.assertNotIn('column_relation',TASKS)
    def test_historical_bit_positions_preserved(self):
        self.assertEqual(SCENARIOS.index('time_burst'),3);self.assertEqual(SCENARIOS.index('input_stop'),4)

if __name__=='__main__':unittest.main()
