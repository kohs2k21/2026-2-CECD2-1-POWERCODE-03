import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from specmodel.contracts import validate_request_contract
from specmodel.io import InputFailure
class RequestFormat(unittest.TestCase):
 def test_pair_accepted(self):
  validate_request_contract({'analysis_request':{'required_columns':[['T','C']]}})
 def test_object_rejected_before_generation(self):
  with self.assertRaisesRegex(InputFailure,'required_columns'):
   validate_request_contract({'analysis_request':{'required_columns':[{'table':'T','column':'C'}]}})
