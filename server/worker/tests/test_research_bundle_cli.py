import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import research_bundle


class ResearchBundleCLI(unittest.TestCase):
    def test_help_works_at_container_path(self):
        output = io.StringIO()
        with patch.object(research_bundle, "__file__", "/app/research_bundle.py"), \
                contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
            research_bundle.main(["--help"])
        self.assertEqual(stopped.exception.code, 0)
        self.assertIn("--repository", output.getvalue())

    def test_explicit_repository_works_at_container_path(self):
        with patch.object(research_bundle, "__file__", "/app/research_bundle.py"), \
                patch.object(research_bundle, "build") as build, \
                contextlib.redirect_stdout(io.StringIO()):
            research_bundle.main(["--repository", "/repository", "--output", "/bundles/runtime"])
        build.assert_called_once_with(Path("/repository"), Path("/bundles/runtime"))

    def test_source_checkout_repository_is_discovered(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            manifest = root / "research/models/feature_rule_design_v0_2/model_manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("{}")
            with patch.object(research_bundle, "__file__", str(root / "server/worker/research_bundle.py")), \
                    patch.object(research_bundle, "build") as build, \
                    contextlib.redirect_stdout(io.StringIO()):
                research_bundle.main(["--output", str(root / "runtime")])
            build.assert_called_once_with(root, root / "runtime")

    def test_missing_repository_has_cli_error_and_does_not_build(self):
        with patch.object(research_bundle, "__file__", "/app/research_bundle.py"), \
                patch.object(Path, "is_file", return_value=False), \
                patch.object(research_bundle, "build") as build, \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stopped:
            research_bundle.main(["--output", "/bundles/runtime"])
        self.assertEqual(stopped.exception.code, 2)
        build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
