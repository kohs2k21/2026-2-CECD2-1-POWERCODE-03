"""Development-only sparse evidence selection; no test inputs or labels."""
import argparse
from itertools import product
import numpy as np
from ..common import write_json, sha256
from . import run, refine
from .bank import load_bank, combine_evidence
from .benchmark import load_split


def candidates(names):
    for down, duration, variance, trend, count_variance in product(
            (.5, 1., 2.), (.5, 1., 2.), (.25, 1., 4.), (0., .25), (False, True)):
        weights = []
        for name in names:
            ch, stat = name.split(":")
            value = 0.
            if ch != "work":
                if stat == "mean_up": value = 1. if ch == "count" else duration
                elif stat == "mean_down": value = down if ch == "count" else duration * .25
                elif stat in ("variability_up", "energy", "innovation_energy"):
                    value = variance if ch == "duration" or count_variance else 0.
                elif stat.startswith(("trend", "early_late")): value = trend
            weights.append(value)
        yield weights


def study():
    refine.configure()
    path = run.OUT / "development_sparse_feature_study.json"
    if path.exists(): raise FileExistsError("Preserve completed development study")
    bank = load_bank(run.OUT / "models.joblib")
    cf, cd = run.extract("calibration");df, dd = run.extract("development")
    y = load_split("development", True)["y"]
    groups = np.asarray([r["scenario"] for r in run.metadata("development", dd["case_id"])])
    model = bank.models[1]
    ce = model.normal_evidence(cf[1].reshape(-1, len(model.feature_names)))
    de = model.normal_evidence(df[1].reshape(-1, len(model.feature_names)))
    masks = [(groups == g)[:, None] & ~y for g in np.unique(groups)]
    best = {str(cap): None for cap in (.045, .05, .0525)}
    count = 0
    for weights, temperature in product(candidates(model.feature_names), (0., .5)):
        active = np.asarray(weights) > 0
        ds = run.hour_scores(combine_evidence(de[:, active], np.asarray(weights)[active], temperature), dd, 1)
        cs = run.hour_scores(combine_evidence(ce[:, active], np.asarray(weights)[active], temperature), cd, 1)
        for key in best:
            cap = float(key)
            threshold = max(float(np.nanquantile(cs, 1 - cap, method="higher")),
                *(float(np.quantile(ds[m & np.isfinite(ds)], 1 - cap, method="higher")) for m in masks))
            result = run.metrics(y, ds, threshold, groups)
            row = dict(weights=weights, temperature=temperature, threshold=threshold,
                       normal_tail_budget=cap, development=result)
            rank = (min(result["median"].values()), result["median"]["f1"])
            if best[key] is None or rank > best[key][0]:
                best[key] = rank, row
        count += 1
        if count % 60 == 0:
            print("Sparse evidence development", count, {k: round(v[1]["development"]["median"]["recall"], 4) for k, v in best.items()}, flush=True)
    output = {"test_used": False, "development_labels_used_for_feature_selection": True,
              "source_sha256": sha256(__file__), "candidate_count": count,
              "results": {k: v[1] for k, v in best.items()}}
    write_json(path, output)
    for key, row in output["results"].items():
        print("Sparse native development", key, row["development"]["median"],
              "max FPR", row["development"]["maximum_scenario_fpr"], flush=True)


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    study()
