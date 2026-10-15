"""Issue 44: offline Trainer reproduction, never imported by server runtime."""

from pathlib import Path
import sys

# The published joblib classes retain these original module names.
SOURCE_ROOT = Path(__file__).resolve().parents[2] / "anomaly_detection"
SOURCE_CODE = SOURCE_ROOT / "code"
if str(SOURCE_CODE) not in sys.path:
    sys.path.insert(0, str(SOURCE_CODE))
