"""Reproduce actual pre-Rule handoff faults without loading a Model."""
import json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_calculations import P,prop,col,op
from specmodel.dataset import Dataset
from specmodel.calculations import compile_feature
from specmodel.rule_logic import make_rule_record
from specmodel.official_facts import rule_semantics,rule_description_conflicts
from specmodel.io import InputFailure,json_default

class RuleHandoff(unittest.TestCase):
 def test_wrong_boolean_condition_on_numeric_plan_is_input_fault(self):
  ds=Dataset(P/'examples/capture.json');p=prop(op('divide',col('A'),col('B')));c=compile_feature(ds,p,'CPU')
  with self.assertRaisesRegex(InputFailure,'/rule_plan/observed_condition'):
   make_rule_record(ds,None,p,c,{'requests':['observed'],'question':'Numeric availability','observed_condition':{'op':'eq','value':True}})
 def test_empty_aggregate_decimal_fact_is_serializable(self):
  ds=Dataset(P/'examples/E02_average_captured_quantity/capture.json')
  p=prop({'op':'child_count','relationship_id':'sample_event_parent'},table='PARENT_SAMPLE');p['plan']['grain']['keys']=['PARENT_ROW']
  c=compile_feature(ds,p,'CPU');self.assertTrue(c['passed'])
  record=make_rule_record(ds,None,p,c,{'requests':['observed'],'question':'Calculation availability','observed_condition':{'op':'is_missing'}})
  parsed=json.loads(record['messages'][1]['content']);self.assertEqual(parsed['measurement']['row_count'],4)
  self.assertEqual(json.loads(json.dumps(record['input'],default=json_default)),parsed)
 def test_invalid_model_condition_keeps_typed_error_not_fake_counts(self):
  record={'input':{'measurement':{'valid_count':1,'row_count':1},'baseline':{}},'compilation':{'values':[3]}}
  facts=rule_semantics(record,{'condition':{'op':'eq','value':True}})
  self.assertFalse(facts['condition_valid']);self.assertIsNone(facts['statistics']);self.assertIn('Boolean/numeric',facts['condition_error'])
 def test_complete_boolean_future_value_claim_vs_empty_population(self):
  record={'input':{'measurement':{'valid_count':2,'row_count':2},'baseline':{},'feature':{'operator_state_proof':{'unavailable_possible':False}}},'compilation':{'values':[True,False]}}
  d={'condition':{'op':'eq','value':True},'unavailable_reason':'It may be unavailable under future values or selected rows.'}
  self.assertEqual(rule_description_conflicts(record,{'decisions':[d]})[0]['code'],'complete_output_future_value_unavailability_conflict')
  d['unavailable_reason']='With no selected rows there are no condition observations; retained Boolean values remain evaluable.'
  self.assertEqual(rule_description_conflicts(record,{'decisions':[d]}),[])
 def test_wrong_primary_operator_description_and_valid_contrast(self):
  record={'input':{'measurement':{'valid_count':2,'row_count':3},'baseline':{},'feature':{'operator_state_proof':{'unavailable_possible':True},'dtype':'number'}},'compilation':{'values':[1,3,None]}}
  d={'condition':{'op':'gt','value':1},'reason':'Numeric comparison.','unavailable_reason':'Under is_missing the condition is unassessed for that row; no cause is inferred.'}
  self.assertEqual(rule_description_conflicts(record,{'decisions':[d]})[0]['code'],'condition_operator_prose_conflict')
  d['unavailable_reason']='Unlike is_missing, this numeric comparison needs an available number.'
  self.assertEqual(rule_description_conflicts(record,{'decisions':[d]}),[])
  d['unavailable_reason']='Under is_missing the condition is unassessed when no retained rows exist.'
  self.assertEqual(rule_description_conflicts(record,{'decisions':[d]}),[])
