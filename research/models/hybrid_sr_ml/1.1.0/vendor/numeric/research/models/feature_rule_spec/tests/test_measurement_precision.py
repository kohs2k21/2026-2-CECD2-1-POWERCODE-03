import sys,unittest
from pathlib import Path
from decimal import localcontext
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from specmodel.dataset import Dataset
from specmodel.calculations import compile_feature
P=Path(__file__).resolve().parents[1]
class MeasurementPrecision(unittest.TestCase):
 def test_summary_SHA_independent_of_process_decimal_context(self):
  ds=Dataset(P/'examples/capture.json')
  q={'proposal_id':'cpu','purpose':'Source A availability.','meaning':'Missing A on CASE_LOG rows.','plan':{'expression':{'op':'is_missing','args':[{'op':'column','table':'CASE_LOG','column':'A'}]},'grain':{'table':'CASE_LOG','keys':['ROW_ID']},'population':{'table':'CASE_LOG','filter':None},'group_by':[]},'needed_evidence':[]}
  measures=[]
  for n in [6,28,50]:
   with localcontext() as ctx:
    ctx.prec=n;c=compile_feature(ds,q,'CPU_precision_control');self.assertTrue(c['passed']);measures.append(c['compiled_feature']['measurement'])
  self.assertEqual(len({m['measurement_sha256']for m in measures}),1)
  self.assertEqual(measures[0]['summary_decimal_precision'],50)
