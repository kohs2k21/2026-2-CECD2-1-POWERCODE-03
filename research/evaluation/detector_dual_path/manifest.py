"""Reproducibility receipt for generated data, trusted models and source code."""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import subprocess

ROOT = Path(__file__).resolve().parents[3]


def digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def file_digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def load_config(path):
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    positive = ("cases_per_scenario", "n_estimators", "max_samples", "k",
                "reference_resolution_seconds", "baseline_lookback_seconds",
                "baseline_stride_seconds", "performance_repeats", "warmup", "performance_jobs")
    for name in positive:
        value = config.get(name)
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name}: positive integer required")
    if type(config.get("seed")) is not int or not 0 <= config["seed"] <= 2**32 - 1:
        raise ValueError("seed: unsigned 32-bit integer required")
    for name in ("candidate_strides", "candidate_windows"):
        values = config.get(name)
        if not isinstance(values, list) or not values or any(type(v) is not int or v <= 0 for v in values):
            raise ValueError(f"{name}: positive integer list required")
        if len(values) != len(set(values)):
            raise ValueError(f"{name}: duplicates prohibited")
    rate = config.get("input_rate_hz")
    if type(rate) not in (int, float) or not 0 < rate < float("inf"):
        raise ValueError("input_rate_hz: finite positive number required")
    if config["performance_repeats"] < 3:
        raise ValueError("performance_repeats: at least three required")
    for name, fixed in (("reference_resolution_seconds", 60), ("baseline_lookback_seconds", 300),
                        ("baseline_stride_seconds", 60)):
        if config[name] != fixed:
            raise ValueError(f"{name}: this pilot fixes the protocol at {fixed}")
    if set(config["candidate_strides"]) - {5, 15, 60} or set(config["candidate_windows"]) - {60, 300, 900}:
        raise ValueError("Unsupported candidate recipe")
    digest(config)
    return config


def create_manifest(config):
    from .scenarios import build_suite, normal_history_observations
    episodes = build_suite(config["seed"], config["cases_per_scenario"])
    partitions = {}
    for split in ("train", "calibration", "threshold", "validation", "test"):
        selected = [asdict(episode) for episode in episodes if episode.split == split]
        partitions[split] = {"episodes": len(selected), "sha256": digest(selected)}
    inventory_path = ROOT / "research/anomaly_detection/model_inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    # Inventory formats are owned by the existing research upload.
    entries = inventory if isinstance(inventory, list) else inventory["models"]
    models = []
    for entry in entries:
        path = ROOT / "research/anomaly_detection" / entry["path"]
        actual = file_digest(path)
        if actual != entry["sha256"]:
            raise ValueError(f"Published model checksum mismatch: {entry['path']}")
        models.append({"path": str(path.relative_to(ROOT)).replace("\\", "/"), "sha256": actual})
    source_paths = sorted((ROOT / "research/evaluation/detector_dual_path").rglob("*.py"))
    source_paths += sorted((ROOT / "research/anomaly_detection/code").rglob("*.py"))
    sources = {str(path.relative_to(ROOT)).replace("\\", "/"): file_digest(path) for path in source_paths}
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
    return {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "prepared", "data_source": "new_synthetic_seconds_pilot",
            "original_customer_benchmark_available": False,
            "config": config, "config_sha256": digest(config), "seed": config["seed"],
            "dataset_partitions": partitions,
            "prior_history_sha256": digest([asdict(o) for o in normal_history_observations(config["seed"])]),
            "published_models": models, "sources": sources, "source_sha256": digest(sources),
            "git": {"head": git("rev-parse", "HEAD"), "branch": git("branch", "--show-current"),
                    "dirty": bool(git("status", "--porcelain"))},
            "environment": {"python": platform.python_version(), "platform": platform.platform(),
                            "packages": {name: version(name) for name in
                                         ("numpy", "pandas", "scikit-learn", "joblib", "threadpoolctl", "psutil")}},
            "limitations": ["Synthetic held-out quality only; original accuracy not reproduced",
                            "Virtual event time is not an ESB end-to-end latency measurement"]}


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
