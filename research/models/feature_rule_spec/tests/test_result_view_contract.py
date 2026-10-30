import sys,copy,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_calculations import prop,col,op,lit,P
from specmodel.dataset import Dataset
from specmodel.calculations import compile_feature
from specmodel.validation import validate_feature
class ResultView(unittest.TestCase):
 def setUp(self):self.ds=Dataset(P/'examples/capture.json')
 def test_result_missing_complete_and_original_immutable(self):
  p=prop(op('divide',col('A'),col('B')));p['plan']['result_use']='unavailable';original=copy.deepcopy(p);c=compile_feature(self.ds,p,'CPU');self.assertTrue(c['passed']);self.assertEqual(p,original);self.assertEqual(c['values'],[False,True,True,True,False,True,False]);self.assertEqual(c['compiled_feature']['result_view_compilation']['selected_by'],'explicit_plan_field')
 def test_explicit_null_numeric_branch(self):
  p=prop(op('if',lit(True),lit(None),col('A')));c=compile_feature(self.ds,p,'CPU');self.assertTrue(c['passed']);self.assertEqual(c['values'],[None]*7);self.assertEqual(c['compiled_feature']['dtype'],'number')
 def test_wrong_inner_type_is_not_masked_by_complete_view(self):
  p=prop(op('is_missing',op('divide',col('A'),col('B'))));p['plan']['result_use']='unavailable';c=compile_feature(self.ds,p,'CPU');errs=validate_feature(self.ds,{'question':'Calculate A/B and inspect result availability.','analysis_request':{'output_table':'CASE_LOG','result_use':'unavailable','calculation_type':'number'}},{'schema':'feature5.2','candidates':[p],'stop_reason':None},c);self.assertTrue(any(e['code']=='calculation_component_type_conflict'for e in errs))
 def test_filter_excluded_rows_have_no_false_placeholder(self):
  p=prop(op('divide',col('A'),col('B')));p['plan']['result_use']='unavailable';p['plan']['population']['filter']=op('eq',col('A'),lit(0));c=compile_feature(self.ds,p,'CPU');self.assertTrue(c['passed']);self.assertEqual(c['values'],[False]);self.assertEqual(len(c['row_ids']),1)
