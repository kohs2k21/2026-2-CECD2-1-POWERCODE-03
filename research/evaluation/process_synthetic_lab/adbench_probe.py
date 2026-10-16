"""Small, pinned ADBench feature-space probe; never an ESB log generator.

The unchanged upstream generation method is loaded from a verified source cache.
Its full generator() is intentionally not used: that method synthesizes before a
random split. Only a caller-provided training reference fits the distribution here.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Callable
from urllib.request import urlopen

import numpy as np
from sklearn.mixture import GaussianMixture


COMMIT = "f7fed68dea6901fe9a81ed51b251b1d2456790cd"
SOURCE_SHA256 = "bd02aa9bb09d0a9302550c865e8c3e6182ed3b2491dcd0cad686e12a98ce3439"
SOURCE_URL = (
    f"https://raw.githubusercontent.com/Minqi824/ADBench/{COMMIT}/"
    "adbench/datasets/data_generator.py"
)
MODES = ("local", "cluster", "global")


def _official_method(cache_dir: Path):
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"adbench-{COMMIT}-data_generator.py"
    if cached.exists():
        source = cached.read_bytes()
    else:
        with urlopen(SOURCE_URL, timeout=30) as response:
            source = response.read(1_000_001)
        if len(source) > 1_000_000:
            raise ValueError("upstream_source_size_limit")
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
        raise ValueError("upstream_source_hash_mismatch")
    if not cached.exists():
        cached.write_bytes(source)
    parsed = ast.parse(source.decode("utf-8"))
    owner = next(node for node in parsed.body if isinstance(node, ast.ClassDef)
                 and node.name == "DataGenerator")
    method = next(node for node in owner.body if isinstance(node, ast.FunctionDef)
                  and node.name == "generate_realistic_synthetic")
    module = ast.Module(body=[method], type_ignores=[])
    namespace = {"np": np, "GaussianMixture": GaussianMixture}
    exec(compile(module, str(cached), "exec"), namespace)
    return namespace["generate_realistic_synthetic"]


def _prediction_summary(predictor, values: np.ndarray, labels=None) -> dict:
    """Reject ambiguous scores/sign conventions instead of guessing a threshold."""
    predictions = np.asarray(predictor(values))
    if predictions.shape != (len(values),) or predictions.dtype.kind != "b":
        raise ValueError("predictor_must_return_one_boolean_anomaly_flag_per_row")
    result = {"rows": len(values), "predicted_anomalies": int(predictions.sum())}
    if labels is not None:
        for label, name in ((0, "generator_reference"), (1, "injected_outlier")):
            selected = labels == label
            count = int(selected.sum())
            result[name] = {
                "rows": count,
                "predicted_anomalies": int(predictions[selected].sum()),
                "predicted_anomaly_rate": float(predictions[selected].mean()) if count else None,
            }
    else:
        result["label_status"] = "unverified_reference_not_false_positive_rate"
    return result


def run_probe(
    train_x,
    test_x,
    *,
    seed: int,
    output_dir,
    predictor: Callable | None = None,
) -> dict:
    """Generate auxiliary feature matrices and score a caller's already-fit model.

    train_x/test_x must already use the same frozen, train-fit transformation.
    Neither real test rows nor test labels fit the synthetic distribution. Generated
    zeros are samples of a reference distribution, not verified normal ESB logs.
    All output (including cached upstream code) belongs in a gitignored run folder.
    """
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "status": "pending",
        "method": "ADBench.generate_realistic_synthetic",
        "source_url": SOURCE_URL,
        "source_commit": COMMIT,
        "source_sha256": SOURCE_SHA256,
        "unit": "synthetic_numeric_feature_vector_not_PROCESS",
        "seed": int(seed),
        "alpha": 5,
        "percentage": 0.1,
        "fit_scope": "caller_training_reference_only",
        "real_test_used_for_fit": False,
        "default_generator_random_split_used": False,
        "labels": "generator_distribution_membership_not_confirmed_ESB_fault",
        "limitations": [
            "Training reference may contain undetected anomalies; labels are not verified normality.",
            "Generated vectors do not establish valid timestamps, integer counts, categorical encodings or parent-child relations.",
            "No inverse transformation to raw ESB logs; do not merge these scores with PROCESS scenario results.",
            "Reference draws and outlier draws may overlap; injected labels do not guarantee model separability.",
            "Frozen constant dimensions are restored after generating only variable dimensions.",
        ],
        "unsupported_modes": {
            "dependency": "Copula dependencies and >50-column random projection excluded from this bounded same-feature probe."
        },
        "results": [],
    }
    try:
        train = np.asarray(train_x, dtype=np.float64)
        test = np.asarray(test_x, dtype=np.float64)
        if (train.ndim != 2 or test.ndim != 2 or train.shape[1] != test.shape[1]
                or train.shape[1] == 0 or len(train) < 9 or len(test) == 0):
            raise ValueError("requires_at_least_9_train_rows_and_nonempty_matching_test_matrix")
        if not np.isfinite(train).all() or not np.isfinite(test).all():
            raise ValueError("nonfinite_feature_values")
        rng = np.random.default_rng(seed)
        fit_indices = (np.sort(rng.choice(len(train), 1000, replace=False))
                       if len(train) > 1000 else np.arange(len(train)))
        reference = train[fit_indices]
        variable = np.ptp(reference, axis=0) > 1e-12
        if not variable.any():
            raise ValueError("all_training_reference_dimensions_constant")
        n_anomalies = min(100, len(test))
        report.update({
            "training_rows_available": len(train),
            "training_rows_used": len(reference),
            "fit_row_selection": "seeded_without_replacement_max_1000",
            "fit_row_indices_sha256": hashlib.sha256(fit_indices.astype("<i8").tobytes()).hexdigest(),
            "feature_count": train.shape[1],
            "variable_feature_indices": np.flatnonzero(variable).tolist(),
            "frozen_constant_feature_indices": np.flatnonzero(~variable).tolist(),
            "injected_count": n_anomalies,
            "count_control": "zero-valued_y1_placeholder_rows_excluded_from_upstream_fit",
        })
        # Upstream counts y==1 rows, then drops those rows before fitting. These
        # placeholders control counts only; they are neither real test anomalies
        # nor fabricated labels assigned to real customer rows.
        inputs = np.vstack((reference[:, variable], np.zeros((n_anomalies, int(variable.sum())))))
        labels = np.concatenate((np.zeros(len(reference), dtype=int), np.ones(n_anomalies, dtype=int)))
        generate = _official_method(output / "vendor")
        for mode in MODES:
            result = {"mode": mode, "status": "pending"}
            old_state = np.random.get_state()
            try:
                np.random.seed(seed)
                generated, truth = generate(
                    SimpleNamespace(seed=seed), inputs.copy(), labels.copy(), mode, 5, 0.1
                )
                full = np.tile(reference[0], (len(generated), 1))
                full[:, variable] = generated
                if not np.isfinite(full).all():
                    raise ValueError("generator_returned_nonfinite_values")
                if full.shape != (len(reference) + n_anomalies, train.shape[1]):
                    raise ValueError("generator_returned_unexpected_shape")
                artifact = output / f"adbench-{mode}-seed-{seed}.npz"
                np.savez_compressed(artifact, X=full, y=truth)
                result.update({
                    "status": "generated",
                    "artifact": artifact.name,
                    "matrix_sha256": hashlib.sha256(full.astype("<f8").tobytes()).hexdigest(),
                    "label_sha256": hashlib.sha256(truth.astype("<i8").tobytes()).hexdigest(),
                    "generated_rows": len(full),
                })
                if predictor is not None:
                    result["predictions"] = _prediction_summary(predictor, full, truth)
                    result["status"] = "scored"
            except Exception as exc:
                result.update(status="unsupported_or_failed", reason=f"{type(exc).__name__}: {exc}")
            finally:
                np.random.set_state(old_state)
            report["results"].append(result)
        if predictor is not None:
            report["heldout_reference_diagnostic"] = _prediction_summary(predictor, test)
        good = sum(row["status"] in ("generated", "scored") for row in report["results"])
        report["status"] = "completed" if good == len(MODES) else "partial" if good else "unsupported_or_failed"
    except Exception as exc:
        report.update(status="unsupported_or_failed", reason=f"{type(exc).__name__}: {exc}")
    (output / "adbench-probe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    return report
