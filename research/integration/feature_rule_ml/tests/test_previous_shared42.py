"""Frozen rule and missingness contract tests; fixtures are not LLM outputs."""
import copy
import unittest
from decimal import Decimal
import numpy as np
import pandas as pd

from research.integration.feature_rule_ml.common import InputFailure
from research.integration.feature_rule_ml.frozen_rule_library import apply_rule, combine_codes, compile_library
from research.integration.feature_rule_ml.previous_shared42 import prepare_frame


def fixture():
    feature = dict(id='f', sha='definition', meaning='count', dtype='number', unit='count',
        expression={'op':'column','table':'T','column':'COUNT'}, grain={'table':'T','keys':['row']},
        population={'table':'T','filter':None},group_by=[],missing_policy={'input':'propagate'})
    records=[dict(id='r',input=dict(feature=feature,source_version='TRAIN'))]
    rules=[dict(key='r:baseline',feature=copy.deepcopy(feature),source_version='TRAIN',
        decision=dict(state='proposed',request='baseline',condition={'op':'gt','value':2}),
        baseline=dict(state='verified',reference_split='train',reference_valid_n=100,minimum_n=20,
                      unit='count',value=2,operator='gt'))]
    return rules,records


class LibraryTests(unittest.TestCase):
    def test_original_records_are_not_rewritten(self):
        rules,records=fixture();before=copy.deepcopy((rules,records));result=compile_library(rules,records)
        self.assertEqual((rules,records),before);self.assertEqual(result['roles'],{'numeric_SR':1})

    def test_rule_threshold_must_equal_frozen_reference(self):
        r,f=fixture();r[0]['decision']['condition']['value']=3
        with self.assertRaises(InputFailure):compile_library(r,f)

    def test_same_id_changed_expression_rejected(self):
        r,f=fixture();r[0]['feature']['expression']['column']='OTHER'
        with self.assertRaises(InputFailure):compile_library(r,f)

    def test_reference_must_be_train(self):
        r,f=fixture();r[0]['baseline']['reference_split']='test'
        with self.assertRaises(InputFailure):compile_library(r,f)

    def test_pending_is_not_an_active_rule(self):
        r,f=fixture();r[0]['decision']['state']='request_evidence'
        with self.assertRaises(InputFailure):compile_library(r,f)

    def test_exact_boundary_and_missing_comparison(self):
        result=apply_rule([Decimal('2'),Decimal('2.00000000000000001'),None],np.ones(3,bool),'number',{'op':'gt','value':2})
        np.testing.assert_array_equal(result,[0,1,-1])

    def test_missing_diagnostic_does_not_flag_outside_population(self):
        result=apply_rule([None,None,Decimal(0)],np.array([True,False,True]),'number',{'op':'is_missing'})
        np.testing.assert_array_equal(result,[1,-2,0])

    def test_no_rules_and_partial_unknown_not_healthy(self):
        np.testing.assert_array_equal(combine_codes([],2),[-1,-1])
        np.testing.assert_array_equal(combine_codes([np.array([0,1]),np.array([-1,-1])],2),[-1,1])

    def test_outside_population_is_not_a_normal_measurement(self):
        np.testing.assert_array_equal(combine_codes([np.array([-2,-2]),np.array([-2,0])],2),[-1,0])


class SharedFrameTests(unittest.TestCase):
    def fixture(self):
        c=dict(features=['f'],missing_indicators=['f'],population_indicators=['f'])
        metadata=pd.DataFrame({'row_id':['a','b'],'adapterType':['A','A']})
        return c,metadata

    def test_missing_and_outside_population_masks_are_different(self):
        c,m=self.fixture();f=prepare_frame(np.array([[np.nan],[np.nan]]),np.array([[True],[False]]),['f'],m,c)
        self.assertEqual(f['missing__f'].tolist(),[1.,0.]);self.assertEqual(f['in_population__f'].tolist(),[1.,0.])

    def test_finite_value_outside_population_rejected(self):
        c,m=self.fixture()
        with self.assertRaises(InputFailure):prepare_frame(np.array([[1.],[2.]]),np.array([[True],[False]]),['f'],m,c)

    def test_infinite_feature_rejected(self):
        c,m=self.fixture()
        with self.assertRaises(InputFailure):prepare_frame(np.array([[np.inf],[2.]]),np.ones((2,1),bool),['f'],m,c)


if __name__=='__main__':unittest.main()
