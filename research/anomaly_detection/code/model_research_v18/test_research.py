import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import numpy as np
from model_research_v14.test_research import FakeBase, frame
from model_research_v18.models import RoutedFusion


class FusionTests(unittest.TestCase):
    def test_index_sealing_changes_only_metadata_and_selects_active_result(self):
        from model_research_v18 import evaluate
        from model_research_v13.data import read, write
        from model_evaluation.data import sha256
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); output = root/"reports"; output.mkdir()
            originals = {}
            for stage in ("parent", "adjusted", "difficulty_only"):
                target = output/stage; target.mkdir()
                write(target/"summary.json", {"pooled": [{"f1": .42}], "protocol": {"stage": stage}})
                originals[stage] = sha256(target/"summary.json")
                write(output/f"{stage}_evaluation_index.json", {"current": "reports/"+stage, "summary_sha256": "STALE"})
            with patch.object(evaluate, "ROOT", root), patch.object(evaluate, "OUTPUT", output):
                evaluate.seal_indices()
            for stage, digest in originals.items():
                self.assertEqual(read(output/f"{stage}_evaluation_index.json")["summary_sha256"], digest)
                self.assertEqual(sha256(output/stage/"summary.json"), digest)
            self.assertEqual(read(output/"evaluation_index.json")["current"], "reports/adjusted")

    def test_actual_inputs_and_exact_unsupported_fallback(self):
        data = frame(); model = RoutedFusion(FakeBase(), FakeBase()).fit_reference(data)
        model.set_point(.75, "mean", .99, .5)
        margins, flags, covered = model.score(data)
        np.testing.assert_array_equal(margins[:, 0], FakeBase().score(data)[0][:, 0])
        np.testing.assert_array_equal(margins[covered, 1], FakeBase().score(data)[0][covered, 1])
        np.testing.assert_array_equal(margins[~covered, 0], margins[~covered, 1])
        np.testing.assert_array_equal(margins[~covered, 0], margins[~covered, 2])
        np.testing.assert_array_equal(flags, margins > 0)

    def test_labels_do_not_change_model_score(self):
        data = frame(); model = RoutedFusion(FakeBase(), FakeBase()).fit_reference(data)
        model.set_point(.5, "max", .975, 1.)
        altered = data.assign(window_is_anomaly=True, scenario_types="FAKE", normal_execution_arrivals=100000)
        np.testing.assert_array_equal(model.score(data)[0], model.score(altered)[0])

    def test_serving_reference_removal_changes_no_predictions(self):
        data = frame(); model = RoutedFusion(FakeBase(), FakeBase()).fit_reference(data)
        model.set_point(.5, "mean", .99, .5); before = model.score(data)
        model.strip_reference()
        for x,y in zip(before, model.score(data)): np.testing.assert_array_equal(x,y)


if __name__ == "__main__": unittest.main()
