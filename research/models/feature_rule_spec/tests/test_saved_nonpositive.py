"""A failure or a correct hold must remain inspectable without becoming a positive feature."""
import unittest, tempfile, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from specmodel.io import write, digest, InputFailure
from specmodel.cli import verify_origin

class SavedNonpositive(unittest.TestCase):

    def record(self, root, pred):
        write(root / 'model/attempt/output.json', {'raw': str(pred), 'parsed': pred})
        return {'prediction': pred, 'partial_Model_prose_fields': None, 'actual_model_origin': {'file': 'model/attempt/output.json', 'raw_sha': digest(str(pred)), 'parsed_sha': digest(pred)}}

    def test_failed_raw_provenance_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            a = self.record(p, {'schema': 'feature5.1', 'candidates': [], 'stop_reason': 'No supported parent attribute lookup.'})
            f = {'status': 'feature_failed', 'attempts': [a]}
            verify_origin(p, f)
            a['prediction']['stop_reason'] = 'Tampered'
            self.assertRaises(InputFailure, verify_origin, p, f)

    def test_correct_hold_and_failed_json_replay(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            pred = {'schema': 'feature5.1', 'candidates': [], 'stop_reason': 'Required physical unit is unknown.'}
            a = self.record(p, pred)
            f = {'status': 'no_candidate_review', 'reason': pred['stop_reason'], 'attempts': [a]}
            verify_origin(p, f)
            f['reason'] = 'Invented new reason'
            self.assertRaises(InputFailure, verify_origin, p, f)
            a = self.record(p, None)
            verify_origin(p, {'status': 'feature_failed', 'attempts': [a]})
if __name__ == '__main__':
    unittest.main()
