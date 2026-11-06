"""A fresh seeded confirmation generated only after model/architecture freeze.

Same generator population and six fault definitions, not independent real faults.
No candidate may be selected with these labels. Original test/failed versions
remain visible. This is one fixed confirmation sample, never seed-selected.
"""
import argparse
import secrets
import numpy as np
import pandas as pd

from ..common import ROOT, read_json, write_json, sha256
from . import benchmark, localized, run
from .bank import load_bank

DATA = ROOT / "data/evaluation_long_term_five_minute_confirmation"


def build():
    if DATA.exists(): raise FileExistsError("Never regenerate confirmation to choose a favorable seed")
    model = localized.OUT / "models.joblib"
    manifest = read_json(localized.OUT / "model_manifest.json")
    if sha256(model) != manifest["models_sha256"] or localized.runtime_hash() != manifest["runtime_sha256"]:
        raise ValueError("Frozen model/code required before confirmation generation")
    cfg = read_json(benchmark.DATA / "frozen_protocol.json")
    profiles = read_json(benchmark.DATA / "source_distribution.json")["profiles"]
    with np.load(benchmark.DATA / "source_innovations.npz", allow_pickle=False) as z: pool = z["innovations"]
    seed = secrets.randbits(48)
    tasks = list(cfg["scenario_definitions"])
    arrays, obs, avail, labels, ids, starts, meta = [], [], [], [], [], [], []
    n = cfg["test_pairs_per_scenario"]
    for ti, task in enumerate(tasks):
        for pair in range(n):
            pair_seed = seed + ti * 10000 + pair
            for fault in (False, True):
                rng = np.random.default_rng(pair_seed)
                profile = profiles[int(rng.integers(len(profiles)))]
                phase = int(rng.integers(24)) * 12
                z = benchmark.simulate(rng, profile, pool, cfg, phase)
                event = benchmark.inject(z, np.random.default_rng(pair_seed + 900_000_000), task, cfg) if fault else None
                x = np.maximum(profile["center"] + z * profile["scale"], 0)
                count = np.rint(np.expm1(np.minimum(x[:, 0], 20)))
                x[:, 0] = np.log1p(count)
                available = count > 0
                x[~available, 1:] = np.nan
                o = np.ones(len(x), bool)
                grng = np.random.default_rng(pair_seed + 700_000_000)
                for _ in range(2):
                    begin = int(grng.integers(24, len(x) // 12 - 8)) * 12
                    o[begin:begin + int(grng.integers(2, 6)) * 12] = False
                available &= o
                x[~o] = np.nan
                y = np.zeros(336, bool)
                if event: y[event["event_start_hour"]:event["event_end_hour"]] = True
                identity = f"CONF-{len(ids):04d}"
                arrays.append(x.astype(np.float32));obs.append(o);avail.append(available);labels.append(y);ids.append(identity)
                starts.append((pd.Timestamp("2032-01-01") + pd.Timedelta(minutes=phase * 5)).value)
                meta.append(dict(case_id=identity, scenario=task, injected=fault,
                    adapterType=profile["adapterType"], processId=profile["processId"], **(event or {})))
        print("Confirmation generated", task, n * 2, "cases", flush=True)
    DATA.mkdir(parents=True)
    np.savez_compressed(DATA / "inputs.npz", X=np.asarray(arrays), collection_observed=np.asarray(obs),
        latency_available=np.asarray(avail), case_id=np.asarray(ids), start_ns=np.asarray(starts), channels=np.asarray(benchmark.CHANNELS))
    np.savez_compressed(DATA / "truth.npz", y=np.asarray(labels), case_id=np.asarray(ids))
    write_json(DATA / "metadata.json", meta)
    write_json(DATA / "manifest.json", {"seed_generated_once_after_model_freeze": seed,
        "frozen_model_sha256": sha256(model), "frozen_runtime_sha256": localized.runtime_hash(),
        "generator_population_manifest_sha256": sha256(benchmark.DATA / "manifest.json"),
        "confirmation_generator_sha256": sha256(__file__),
        "files": {name: sha256(DATA / name) for name in ("inputs.npz", "truth.npz", "metadata.json")},
        "cases": len(ids), "scenarios": tasks, "generator_shared_with_development": True,
        "fresh_case_realizations": True, "independent_real_fault_validation": False,
        "official_ADBench": False, "no_model_selection_with_confirmation": True})


def load(with_truth=False):
    m = read_json(DATA / "manifest.json")
    for name in (("inputs.npz", "truth.npz", "metadata.json") if with_truth else ("inputs.npz",)):
        if sha256(DATA / name) != m["files"][name]: raise ValueError("Confirmation file changed")
    with np.load(DATA / "inputs.npz", allow_pickle=False) as z: d = {k: z[k] for k in z.files}
    if with_truth:
        with np.load(DATA / "truth.npz", allow_pickle=False) as z:
            np.testing.assert_array_equal(d["case_id"], z["case_id"]);d["y"] = z["y"]
    return d


def evaluate():
    out = localized.OUT
    if (out / "confirmation_results.json").exists(): raise FileExistsError("Preserve confirmation evaluation")
    manifest = read_json(DATA / "manifest.json")
    if manifest["frozen_model_sha256"] != sha256(out / "models.joblib") or manifest["frozen_runtime_sha256"] != localized.runtime_hash():
        raise ValueError("Model changed after confirmation generation")
    d = load(False);bank = load_bank(out / "models.joblib")
    rows = {h: [] for h in (1, 4, 8, 12, 24)}
    for i, (x, o, a) in enumerate(zip(d["X"], d["collection_observed"], d["latency_available"])):
        ref = bank.models[1].prepare_reference(x[:8064], o[:8064], a[:8064])
        for h in rows: rows[h].append(bank.models[h].sequence_features(ref, x[8064:], o[8064:], a[8064:], h))
        if (i + 1) % 96 == 0: print("Confirmation predicted", i + 1, "/", len(d["X"]), flush=True)
    rows = {h: np.asarray(f) for h, f in rows.items()}
    scores, contexts = localized.make_scores(bank, rows, d)
    p = out / "confirmation_predictions.npz"
    np.savez_compressed(p, case_id=d["case_id"], **{f"score_h{h}": s for h, s in scores.items()},
                        **{f"context_h{h}": s for h, s in contexts.items()})
    write_json(out / "confirmation_prediction_seal.json", {"model_sha256": sha256(out / "models.joblib"),
        "runtime_sha256": localized.runtime_hash(), "predictions_sha256": sha256(p),
        "inputs_sha256": sha256(DATA / "inputs.npz"), "predictions_saved_before_truth_read": True})
    y = load(True)["y"]
    meta = read_json(DATA / "metadata.json")
    if [r["case_id"] for r in meta] != list(d["case_id"]): raise ValueError("Metadata order changed")
    groups = np.asarray([r["scenario"] for r in meta])
    results = {str(h): run.metrics(y, s, bank.settings[1]["threshold"] if h == 1 else 0., groups)
               for h, s in scores.items()}
    output = {"results": results, "all_horizons_goal_pass": all(r["goal_pass"] for r in results.values()),
        "fresh_realizations_after_frozen_architecture": True, "no_model_selection_on_confirmation": True,
        "shared_synthetic_generator_not_independent_real_faults": True,
        "model_bytes": (out / "models.joblib").stat().st_size}
    write_json(out / "confirmation_results.json", output)
    for h, r in results.items(): print("Frozen-model confirmation", h, r["median"], "max FPR", r["maximum_scenario_fpr"], "pass", r["goal_pass"], flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__);p.add_argument("command", choices=("build", "evaluate"))
    args = p.parse_args();build() if args.command == "build" else evaluate()
