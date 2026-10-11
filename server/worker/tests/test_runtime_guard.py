import os
from pathlib import Path
import subprocess
import sys
import unittest


class RuntimeGuardTests(unittest.TestCase):
    def test_all_unimplemented_services_fail_without_environment_disclosure(self):
        worker = Path(__file__).resolve().parents[1]
        for service in ("collector", "detector", "trainer", "llm", "invalid"):
            with self.subTest(service=service):
                result = subprocess.run(
                    [sys.executable, "-m", "common.runtime_guard"], cwd=worker,
                    env={**os.environ, "POWERCODE_SERVICE": service,
                         "DATABASE_URL": "test-secret-must-not-appear"},
                    capture_output=True, text=True, timeout=5,
                )
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("test-secret", result.stdout + result.stderr)
                self.assertIn("not implemented" if service != "invalid" else "not configured",
                              result.stderr)


if __name__ == "__main__":
    unittest.main()
