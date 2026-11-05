"""Reuse research Encoder/RawModel/ScoreNormalizer on explicit frozen features.

This makes NEW row-feature models. It never relabels a v12 window checkpoint as
a row model. fit() is an explicit offline command, never an inference fallback.
"""
from pathlib import Path
import math
import platform

import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from .common import (CONTRACT, InputFailure, ML_CODE, RESEARCH, UNASSESSED,
                     WARNING, NONWARNING, dependencies, digest, json_key,
                     load_pack, new_dir, pin, read, seal, sha, verified, write)
from model_research_v5.models import Encoder, RawModel, Settings
from process_anomaly_pipeline.features import ScoreNormalizer


def group(pack, gid):
    matches = [g for g in pack["groups"] if g["id"] == gid]
    if len(matches) != 1:
        raise InputFailure("Expected exactly one matching grain: " + gid)
    return matches[0]


def signature(g, columns):
    byname = {x["name"]: x for x in g["columns"]}
    if not columns or len(set(columns)) != len(columns):
        raise InputFailure("Nonempty, unique ML feature selection required")
    if any(c not in byname for c in columns):
        raise InputFailure("Selected ML feature absent")
    return {"grain": g["grain"], "schema_version": g["schema_version"],
            "columns": [{k: byname[c][k] for k in ("name", "semantic_sha", "dtype", "unit")}
                        for c in columns]}


def matrix(g, columns):
    byname = {x["name"]: x for x in g["columns"]}
    for c in columns:
        col = byname[c]
        if col["dtype"] not in ("number", "integer", "boolean"):
            raise InputFailure("ML accepts only explicit numeric/Boolean features")
        expr = col["proposal"]["plan"]["expression"]
        if expr["op"] == "column" and expr["column"] in g["grain"]["keys"]:
            raise InputFailure("Raw row identifiers cannot be ML inputs")
    values, reasons = [], []
    for row in g["rows"]:
        vector, errors = [], []
        for c in columns:
            cell = row["features"][c]
            if cell["state"] != "value":
                errors.append(c + ":" + cell["state"])
                vector.append(np.nan)
                continue
            value = float(cell["value"])
            if not math.isfinite(value):
                errors.append(c + ":nonfinite")
            vector.append(value)
        values.append(vector)
        reasons.append(errors)
    return pd.DataFrame(values, columns=columns), reasons


def fit(train, calibration, threshold, gid, aliases, algorithm, output, quantile=.99):
    if not .5 < quantile < 1 or algorithm not in ("if", "kmeans", "lof"):
        raise InputFailure("Explicit supported algorithm and .5 < quantile < 1 required")
    packs = [load_pack(p) for p in (train, calibration, threshold)]
    groups = [group(p, gid) for p in packs]
    if [g["split"].lower() for g in groups] != ["train", "calibration", "threshold"]:
        raise InputFailure("Use separate declared train/calibration/threshold captures")
    if len({g["source_version"] for g in groups}) != 3:
        raise InputFailure("Reference captures must be independent")
    dated = [all(k in g["period"] for k in ("start_inclusive", "end_exclusive")) for g in groups]
    if any(dated):
        if not all(dated):
            raise InputFailure("Cannot mix dated and undated reference periods")
        for a, b in ((0, 1), (1, 2)):
            if groups[a]["period"]["end_exclusive"] > groups[b]["period"]["start_inclusive"]:
                raise InputFailure("Reference periods overlap or are not chronological")
    keysets = [{json_key(r["source_row_key"]) for r in g["rows"]} for g in groups]
    if any(keysets[a] & keysets[b] for a, b in ((0, 1), (0, 2), (1, 2))):
        raise InputFailure("Rows leaked between fitting/calibration/threshold references")
    if len(set(aliases)) != len(aliases):
        raise InputFailure("Duplicate feature selection")
    columns = []
    for alias in aliases:
        selected = [c["name"] for c in groups[0]["columns"] if c["alias"] == alias]
        if len(selected) != 1:
            raise InputFailure("ML alias absent or ambiguous: " + alias)
        columns += selected
    sig = signature(groups[0], columns)
    if any(signature(g, columns) != sig for g in groups[1:]):
        raise InputFailure("Reference feature/type/unit/grain mismatch")
    frames, coverage = [], []
    for g in groups:
        frame, errors = matrix(g, columns)
        ok = np.asarray([not e for e in errors])
        frame = frame.loc[ok].copy()
        if len(frame) < 8:
            raise InputFailure("At least eight complete rows per reference required; not production adequacy")
        frames.append(frame)
        coverage.append({"split": g["split"], "all_rows": len(errors),
                         "complete_rows": int(ok.sum()), "excluded_missing_rows": int((~ok).sum())})
    if len(np.unique(frames[0].to_numpy(dtype=float), axis=0)) < 2:
        raise InputFailure("All selected training vectors are constant")
    with threadpool_limits(limits=1):
        encoder = Encoder.fit(frames[0], columns)
        arrays = [encoder.transform(f) for f in frames]
        raw = RawModel(Settings(algorithm=algorithm, rotations=1, seed=42)).fit(arrays[0], arrays[1])
        # RawModel has its original explicit two-thread limit; do not rewrite it.
        for model in raw.models:
            if hasattr(model, "n_jobs"):
                model.n_jobs = 1
        normalizer = ScoreNormalizer.fit(raw.score(arrays[1]))
        raw_cut = float(np.quantile(raw.score(arrays[2]), quantile))
        cutoff = float(normalizer.transform([raw_cut])[0])
    if not math.isfinite(cutoff):
        raise InputFailure("Nonfinite calibration threshold")
    obj = {"encoder": encoder, "raw": raw, "normalizer": normalizer,
           "threshold": cutoff, "signature": sig, "algorithm": algorithm}
    out = new_dir(output)
    joblib.dump(obj, out / "model.joblib", compress=3)
    reused = ["model_research_v5/models.py", "process_anomaly_pipeline/features.py"]
    write(out / "model.json", {
        "contract": CONTRACT, "kind": "shared_feature_row_model", "group": gid,
        "algorithm": algorithm, "columns": columns, "signature": sig,
        "aliases": aliases, "dependencies": dependencies(),
        "python_version": platform.python_version(),
        "bridge_runtime_sha": sha(Path(__file__)),
        "reused_research_code": [{"path": p, "sha256": sha(ML_CODE / p)} for p in reused],
        "fit_inputs": [pin(Path(p) / "prepared.json") for p in (train, calibration, threshold)],
        "source_versions": [g["source_version"] for g in groups], "coverage": coverage,
        "split_checks": {"distinct_capture_versions": True, "original_row_keys_disjoint": True,
                         "chronological_nonoverlap_checked": all(dated),
                         "independent_clone_or_incident_provenance": "not_certified"},
        "score_direction": "larger is more anomalous; warning iff normalized score > threshold",
        "quantile": quantile, "raw_threshold": raw_cut, "threshold": cutoff,
        "missing_policy": "complete-case training; unassessable at prediction, no implicit imputation",
        "model": {"file": "model.joblib", "sha256": sha(out / "model.joblib")},
        "new_fit": True, "not_v12_checkpoint": True, "operational_approval": False,
        "minimum_rows_is_functional_guard_not_statistical_validation": True,
    })
    seal(out, "ml_model")
    return read(out / "model.json")


def load_model(folder, trusted=False):
    folder = verified(folder, "ml_model")
    meta = read(folder / "model.json")
    if meta.get("kind") != "shared_feature_row_model":
        raise InputFailure("Not a compatible shared-feature row model")
    if meta["dependencies"] != dependencies():
        raise InputFailure("ML serialized dependency versions differ; no automatic installation/refit")
    if meta.get("python_version") != platform.python_version():
        raise InputFailure("ML serialized Python version differs")
    if meta.get("bridge_runtime_sha") != sha(Path(__file__)):
        raise InputFailure("ML bridge runtime changed; review compatibility before reuse")
    for item in meta["reused_research_code"]:
        path = (ML_CODE / item["path"]).resolve()
        if not path.is_relative_to(ML_CODE.resolve()) or sha(path) != item["sha256"]:
            raise InputFailure("ML implementation changed")
    model_path = folder / meta["model"]["file"]
    if not model_path.resolve().is_relative_to(folder) or sha(model_path) != meta["model"]["sha256"]:
        raise InputFailure("ML weight SHA mismatch")
    if not trusted:
        raise InputFailure("Explicit --trust-local-model required for executable joblib")
    model = joblib.load(model_path)
    if model["signature"] != meta["signature"] or model["algorithm"] != meta["algorithm"] or model["threshold"] != meta["threshold"]:
        raise InputFailure("Serialized model differs from declared metadata")
    return meta, model


def score_group(g, meta, model):
    if signature(g, meta["columns"]) != meta["signature"]:
        raise InputFailure("ML input feature order/meaning/unit/grain mismatch; refit separately")
    frame, reasons = matrix(g, meta["columns"])
    result = [{"state": UNASSESSED, "reason": ";".join(e) or "not_scored", "score": None}
              for e in reasons]
    positions = [i for i, e in enumerate(reasons) if not e]
    if positions:
        with threadpool_limits(limits=1):
            scores = model["normalizer"].transform(model["raw"].score(
                model["encoder"].transform(frame.iloc[positions])))
        for pos, value in zip(positions, scores):
            if not math.isfinite(float(value)):
                result[pos]["reason"] = "nonfinite_model_score"
                continue
            result[pos] = {"state": WARNING if value > model["threshold"] else NONWARNING,
                           "score": float(value), "threshold": model["threshold"],
                           "margin": float(value - model["threshold"]), "reason": None}
    return result


def inspect_published():
    """Inspect v12 hashes/contracts WITHOUT deserializing mismatched objects."""
    folder = RESEARCH / "anomaly_detection/reports/model_research_v12/handoff"
    manifest = read(folder / "manifest.json")
    actual = dependencies()
    mismatches = {k: {"expected": v, "actual": actual.get(k)}
                  for k, v in manifest["dependencies"].items() if actual.get(k) != v}
    models = [{"algorithm": x["algorithm"], "sha256_matches": sha(folder / x["file"]) == x["sha256"]}
              for x in manifest["models"]]
    return {"published_models": models, "version_mismatches": mismatches,
            "required_input": manifest["input"], "pickle_loaded": False,
            "direct_row_feature_input_compatible": False,
            "reason": "v12 requires its own continuous-minute feature contract; it is not a row-feature checkpoint",
            "current_environment_ready": not mismatches and all(x["sha256_matches"] for x in models)}
