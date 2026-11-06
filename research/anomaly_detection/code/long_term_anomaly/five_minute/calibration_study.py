"""Development-only native-reference tail-budget study; never reads test."""
from itertools import product
import argparse
import numpy as np
from ..common import read_json, write_json
from ..intervals.model import HORIZONS
from . import run
from . import refine
from .bank import load_bank, combine_evidence
from .benchmark import load_split


def study():
    refine.configure()
    path = run.OUT / "development_tail_study.json"
    if path.exists():
        raise FileExistsError("Preserve completed development study")
    bank = load_bank(run.OUT / "models.joblib")
    cf, cd = run.extract("calibration")
    df, dd = run.extract("development")
    y = load_split("development", True)["y"]
    groups = np.asarray([r["scenario"] for r in run.metadata("development", dd["case_id"])])
    h = 1
    model = bank.models[h]
    ce = model.normal_evidence(cf[h].reshape(-1, len(model.feature_names)))
    de = model.normal_evidence(df[h].reshape(-1, len(model.feature_names)))
    masks = [(groups == g)[:, None] & ~y for g in np.unique(groups)]
    best = {str(cap): None for cap in (.045, .05, .0525, .055)}
    for weights, temperature in product(run.candidate_weights(model.feature_names), (0., .5, 1.)):
        ds = run.hour_scores(combine_evidence(de, weights, temperature), dd, h)
        cs = run.hour_scores(combine_evidence(ce, weights, temperature), cd, h)
        for key in best:
            cap = float(key)
            threshold = max(float(np.nanquantile(cs, 1 - cap, method="higher")),
                *(float(np.quantile(ds[m & np.isfinite(ds)], 1 - cap, method="higher")) for m in masks))
            result = run.metrics(y, ds, threshold, groups)
            row = {"weights": weights, "temperature": temperature, "threshold": threshold,
                   "normal_tail_budget": cap, "development": result}
            rank = (min(result["median"].values()), result["median"]["f1"])
            if best[key] is None or rank > best[key][0]:
                best[key] = rank, row
    output = {"test_used": False, "development_labels_used": True,
              "dataset_or_truth_modified": False, "results": {k: v[1] for k, v in best.items()}}
    write_json(path, output)
    for key, value in output["results"].items():
        print("Native development budget", key, value["development"]["median"],
              "max FPR", value["development"]["maximum_scenario_fpr"], flush=True)


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    study()
