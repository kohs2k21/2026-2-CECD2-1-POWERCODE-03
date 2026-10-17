"""Controlled source rows exercise selection, actual HR/ML and missing-value contracts."""
import copy
import json
import sys
import unittest
import uuid
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import jsonschema
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from detector.status_rule import default_policy, evaluate_status
from detector.policy import Policy
from trainer.selection import select_training
from trainer.metrics import compare, comparison_family
from trainer import runner
from telemetry import cost_family, percentile


class Connection:
    def __init__(self): self.writes = []
    def cursor(self): return self
    def transaction(self): return nullcontext()
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, sql, args=None): self.writes.append((sql, args))
    def fetchone(self): return (0,)
    def commit(self): pass


class EvaluationMetricsTests(unittest.TestCase):
    def test_train_selection_never_supplies_fpn_or_labeled_anomaly_to_fit(self):
        definitions = [{'name':'centered', 'expression': {'op':'group_centered','groupBy':'P.adapter_type',
                        'value':{'op':'field','field':'P.total_count'}}}]
        rows = [(uuid.uuid4(), {'status':status,'adapter_type':'A','total_count':value}, 'train', {'label':label})
                for status,value,label in [('S',10,None),('S',20,'normal'),('F',999,None),('P',999,None),
                                           ('N',999,None),('?',999,None),('S',999,'anomaly')]]
        original = copy.deepcopy(rows)
        with patch.object(runner,'_definitions',return_value=definitions), patch.object(runner,'_snapshot_rows',return_value=rows):
            prepared,names,state,digest,selection = runner._training_inputs(Connection(),uuid.uuid4(),uuid.uuid4())
        self.assertEqual(state['features']['centered']['groups'], {'A':'15'})
        self.assertEqual(len(prepared),2)
        self.assertEqual(selection['excludedCount'],5)
        self.assertEqual(selection['excludedExplicitAnomalyCount'],1)
        self.assertEqual(rows,original)
        self.assertFalse(selection['normalGroundTruth'])

    def test_actual_unlabeled_hr_ml_cross_table_and_evaluation_preserve_statuses(self):
        source_id, config_id, snapshot_id = uuid.uuid4(),uuid.uuid4(),uuid.uuid4()
        field = {'name':'count_raw','expression':{'op':'field','field':'P.total_count'}}
        context = {'policy':Policy('parallel',30000,'any_anomaly'), 'rules':[],
                   'statusRulePolicy':default_policy(source_id), 'definitions':[field],
                   'featureSetId':'fixture', 'models':[('fixture','m','hash','f','hash',
                       {'featureNames':['count_raw']},{'value':.5,'direction':'higher_is_anomaly'},'iforest')]}
        rows = [(uuid.uuid4(),{'status':status,'total_count':value},'test',None)
                for status,value in [('S',0),('F',1),('P',0),('N',1),('S',1),('?',1)]]
        conn = Connection()
        job = {'id':uuid.uuid4(),'input':{'sourceId':str(source_id),'configVersionId':str(config_id),
                                        'snapshotId':str(snapshot_id),'purpose':'test'}}
        with patch('detector.computation.load_context',return_value=context), \
             patch.object(runner,'_snapshot_rows',return_value=rows), \
             patch('detector.computation.model_reference',return_value={}), \
             patch('detector.computation.verified_artifact_path',return_value=Path('fixture')), \
             patch('model_adapters.score_iforest',side_effect=lambda ref,values:float(values['count_raw'])), \
             patch.object(runner,'lock_training_ownership',return_value=True), patch.object(runner,'_finish'), \
             patch('trainer.metrics.comparison_family',wraps=comparison_family) as aggregate:
            result = runner._evaluate(conn,job,Path('.'),type('Message',(),{'created_at':None})(),uuid.uuid4(),uuid.uuid4())
            with patch.object(runner,'_snapshot_rows',return_value=rows[:-1]):
                complete = runner._evaluate(conn,job,Path('.'),type('Message',(),{'created_at':None})(),uuid.uuid4(),uuid.uuid4())
        for call in aggregate.call_args_list:
            for row in call.args[0]:
                self.assertEqual(set(row), {'hr', 'sr', 'ml'})
                self.assertTrue(all(set(checker) == {'status', 'anomalous'} for checker in row.values()))
        metrics = result['ruleMlComparison']['metrics']
        self.assertEqual(result['populationCount'],6)
        self.assertEqual([metrics['hr'+key]['numerator'] for key in ('Both','RuleOnly','MlOnly','Neither')],[2,1,1,1])
        self.assertEqual(metrics['hrBoth']['denominator'],5)
        self.assertEqual(metrics['hrBoth']['coverage'],5/6)
        self.assertEqual(metrics['hrMissing']['value'],1)
        self.assertIsNone(metrics['srBoth']['value'])
        self.assertEqual(metrics['srBoth']['reason'],'sr_experiment_pending')
        self.assertIsNone(result['metrics']['precision'])
        self.assertEqual(result['unlabeledCount'],6)
        self.assertEqual(complete['applicability'],'valid')
        self.assertIsNone(complete['metrics']['precision'])
        self.assertTrue(any('run_evaluations' in sql for sql,args in conn.writes))

    def test_registered_rule_hit_is_not_status_hr_or_sr(self):
        from detector.computation import compute
        context = {'policy':Policy('rule_only',30000,'selected_path'), 'models':[], 'definitions':[],
            'statusRulePolicy':default_policy(uuid.uuid4()),
            'rules':[('custom-rule',1,{'schemaVersion':1,'state':'active',
                'condition':{'field':'P.error_count','operator':'gt','value':0},
                'outcome':{'decisionCode':'CUSTOM_HIT','severity':'warning'}})]}
        result=compute(context,{'status':'S','error_count':1},None)
        self.assertTrue(result['rule']['anomalous'])
        self.assertFalse(result['hr']['anomalous'])
        self.assertIsNone(result['sr']['anomalous'])



if __name__=='__main__': unittest.main()
