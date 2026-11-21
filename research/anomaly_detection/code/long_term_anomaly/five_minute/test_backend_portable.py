"""A separate backend process loads the delivered model and unchanged raw CSV."""
import json
import os
from pathlib import Path
import subprocess
import sys
import numpy as np
import pandas as pd
import pytest


def test_portable_model_and_original_process_csv_cli(tmp_path):
    root = Path(__file__).resolve().parents[3]
    model = root / "reports/long_term_anomaly/five_minute_localized/models_portable.joblib"
    if not model.exists(): pytest.skip("Trusted local artifact not included in source-only checkout")
    times = pd.date_range("2026-09-03", periods=8076, freq="5min") + pd.Timedelta(seconds=1)
    f = pd.DataFrame({"transactionId": [f"T-{i}" for i in range(len(times))],
        "processId": "IF_TEST", "dependProcessId": None, "processHubId": "HUB",
        "adapterType": "DB.READER", "channelId": "CH", "status": "S", "startTime": times,
        "endTime": times + pd.to_timedelta(100 + np.arange(len(times)) % 13, unit="ms"),
        "successCount": 1, "errorCount": 0, "responseCode": None, "totalCount": 1, "retryCount": 0})
    normal, monitor = tmp_path / "normal.csv", tmp_path / "process.csv"
    f.iloc[:8064].to_csv(normal, index=False)
    f.iloc[8064:].to_csv(monitor, index=False)
    telemetry = tmp_path / "coverage.json"
    telemetry.write_text(json.dumps({"source": "synthetic_test_collector_heartbeat",
        "certified_intervals": [["2026-09-03", "2026-10-01 01:00"]]}), encoding="utf-8")
    output = tmp_path / "prediction.json"
    cmd = [sys.executable, "-m", "long_term_anomaly.five_minute", "--model", str(model),
        "--processes", str(monitor), "--reviewed-normal-processes", str(normal),
        "--normal-end", "2026-10-01", "--window-end", "2026-10-01 01:00",
        "--analysis-hours", "1", "--coverage", str(telemetry), "--output", str(output)]
    env = dict(os.environ, PYTHONPATH=str(root / "code"), PYTHONIOENCODING="utf-8")
    result = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert len(payload["decisions"]) == 1 and len(payload["fine_bins"]) == 12
    assert payload["decisions"][0]["fine_bin_features_used_for_alarm"]
    assert payload["diagnostics"]["outside_window_monitoring_used"] is False
    assert np.isfinite(payload["decisions"][0]["score"])
    unchanged = output.read_bytes()
    again = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert again.returncode != 0 and output.read_bytes() == unchanged
