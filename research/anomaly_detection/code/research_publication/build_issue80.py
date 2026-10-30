"""Copy an explicit research-only publication set without copying datasets.

Existing remote files are never removed. This is a mechanical copy/export tool,
not a training run, branch manager, or GitHub uploader.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil

SOURCE = Path(__file__).resolve().parents[2]
MODELS = (
    ("if", "reports/shared_model_v7/release/handoff/if_shared.joblib"),
    ("kmeans", "reports/shared_model_v7/release/handoff/kmeans_shared.joblib"),
    ("lof", "reports/shared_model_v7/release/handoff/lof_shared.joblib"),
    ("long_term", "reports/long_term_anomaly/five_minute_localized/models_portable.joblib"),
)
SOURCE_EXTENSIONS = {".py", ".ipynb", ".md", ".json", ".txt", ".png", ".mjs"}
REPORT_NAMES = {
    "summary.json", "selection.json", "selected_configuration.json", "results.json",
    "confirmation_results.json", "report.json", "audit.json", "source_audit.json",
    "source_assembly.json", "manifest.json", "model_manifest.json",
    "portable_model_manifest.json", "verification.json", "validation.json",
    "preparation.json", "provenance.json", "latest.json", "finalization.json",
    "adbench_results.json", "adbench_verification.json", "release.json",
    "feature_importance.json", "comparison.json", "acceptance.json",
}
EXCLUDED_COMPONENTS = {"__pycache__", ".ipynb_checkpoints", "feature_cache", "cache", "node_modules", ".venv", ".git"}


def walk_files(root):
    for folder, directories, names in os.walk(root):
        directories[:] = sorted(d for d in directories if d not in EXCLUDED_COMPONENTS and "node_modules" not in d and d != "github_issue38_upload")
        for name in sorted(names):
            yield Path(folder) / name


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def dump(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def selected_files():
    for p in walk_files(SOURCE / "code"):
        rel = p.relative_to(SOURCE)
        if p.is_file() and not (set(rel.parts) & EXCLUDED_COMPONENTS) and p.suffix in SOURCE_EXTENSIONS:
            yield rel, "research_source"
    for p in walk_files(SOURCE / "reports"):
        if not p.is_file():
            continue
        rel = p.relative_to(SOURCE)
        if set(rel.parts) & EXCLUDED_COMPONENTS:
            continue
        if any("backup" in s or "before_mild" in s or s == "retired_tasks_20261018" for s in rel.parts):
            continue
        if p.suffix in {".md", ".png"} or (p.name in REPORT_NAMES and p.stat().st_size < 1024 * 1024):
            yield rel, "aggregate_report"
    for _, rel in MODELS:
        yield Path(rel), "latest_model"


def build(destination):
    destination = destination.resolve()
    expected = SOURCE / "repo_issue80" / "research" / "anomaly_detection"
    if destination != expected.resolve():
        raise ValueError(f"Publication target must be the reviewed issue80 worktree: {expected}")
    seen, records = set(), []
    for rel, category in selected_files():
        key = rel.as_posix()
        if key in seen:
            continue
        seen.add(key)
        src, dst = SOURCE / rel, destination / rel
        digest = sha256(src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists() or sha256(dst) != digest:
            shutil.copyfile(src, dst)
        if sha256(dst) != digest:
            raise ValueError(f"Copy verification failed: {key}")
        records.append({"path": key, "category": category, "bytes": src.stat().st_size, "sha256": digest})
    models = []
    for algorithm, rel in MODELS:
        p = destination / rel
        models.append({"algorithm": algorithm, "path": rel, "bytes": p.stat().st_size, "sha256": sha256(p)})
    dump(destination / "latest_inventory.json", {
        "issue": 80, "short_release": "shared_model_v7/release", "models": models,
        "short_threshold": 1.0, "long_analysis_hours": [1, 4, 8, 12, 24],
        "original_model_bytes_unchanged": True, "existing_legacy_artifacts_preserved": True,
        "datasets_added": False, "production_deployed": False,
    })
    manifest = json.loads((SOURCE / "reports/shared_model_v7/release/handoff/manifest.json").read_text(encoding="utf-8"))
    for model in manifest["models"]:
        dump(destination / "configs" / (model["algorithm"] + "_core.json"), model["record"]["settings"])
    # Histograms are aggregate evidence, not benchmark inputs/labels or per-row
    # predictions. Fold out-of-range tails into the first/last labelled bars.
    import numpy as np
    histograms = {"threshold": 1., "edges": np.linspace(-1, 3, 65).tolist(),
                  "outside_range_folded_into_edge_bins": True, "models": {}}
    tasks = ("individual_delay", "group_delay", "time_burst", "input_stop", "gradual_degradation")
    for algorithm in ("if", "kmeans", "lof"):
        rows = []
        for task in tasks:
            with np.load(SOURCE / "reports/shared_model_v7/release" / (task + "_scores.npz"), allow_pickle=False) as z:
                scores = np.asarray(z["scores"], float)[:, ("if", "kmeans", "lof").index(algorithm)]
                truth = np.asarray(z["y"], bool)
                row = {"task": task}
                for label, mask in (("normal", ~truth), ("anomaly", truth)):
                    values = scores[mask]
                    row[label] = {"counts": np.histogram(np.clip(values, -1, 3), bins=histograms["edges"])[0].tolist(),
                                  "n": len(values), "below_plot_range": int((values < -1).sum()),
                                  "above_plot_range": int((values > 3).sum())}
                rows.append(row)
        histograms["models"][algorithm] = rows
    dump(destination / "reports/shared_model_v7/release/score_histograms.json", histograms)
    dump(destination / "publication_inventory_issue80.json", {
        "issue": 80, "files": records, "copied_file_count": len(records),
        "copied_bytes": sum(r["bytes"] for r in records), "counts": dict(Counter(r["category"] for r in records)),
        "exclusions": ["period source datasets", "benchmark inputs and labels", "training caches",
                       "intermediate model binaries", "row-level NPZ predictions", "environment and credentials"],
        "existing_tracked_source_archives_or_caches_not_deleted": True,
    })
    print(json.dumps({"copied": len(records), "MiB": round(sum(r["bytes"] for r in records) / 2**20, 2),
                      "models": models}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    build(parser.parse_args().destination)
