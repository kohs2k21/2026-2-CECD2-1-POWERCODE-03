"""Measured disagreement/complementarity; not a new three-model ensemble."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))
import numpy as np
from model_research_v13.data import read, write
from model_research_v14.run import OUTPUT


def analyze():
    folder = ROOT / read(OUTPUT / "evaluation_index.json")["current"]
    result = read(folder / "summary.json")
    # For each route: IF-only / Kmeans-only / LOF-only / all missed / totals.
    totals = np.zeros((3, 2, 5), np.int64)
    tasks = []
    for d in result["datasets"]:
        counts = np.zeros_like(totals)
        for cohort in ("observed", "normal"):
            with np.load(folder / d["dataset"] / (cohort+".npz"), allow_pickle=False) as arrays:
                y, p = arrays["truth"], arrays["prediction"]
                for route in range(3):
                    selected = p[:, [route, 3+route, 6+route]]
                    for truth in (False, True):
                        mask = y == truth
                        for algorithm in range(3):
                            unique = selected[:, algorithm] & ~selected[:, [i for i in range(3) if i != algorithm]].any(axis=1)
                            counts[route, int(truth), algorithm] += np.sum(mask & unique)
                        counts[route, int(truth), 3] += np.sum(mask & ~selected.any(axis=1))
                        counts[route, int(truth), 4] += mask.sum()
        totals += counts
        tasks.append({"dataset": d["dataset"], "counts": counts.tolist()})
    rows = []
    for route, name in enumerate(("global", "adapter", "ensemble")):
        for algorithm, label in enumerate(("if", "kmeans", "lof")):
            rows.append({"route": name, "algorithm": label,
                "unique_true_positive_minutes": int(totals[route, 1, algorithm]),
                "unique_false_positive_minutes": int(totals[route, 0, algorithm]),
                "unique_tp_share_of_all_anomaly_minutes": float(totals[route, 1, algorithm]/totals[route, 1, 4])})
    write(folder / "complementarity.json", {"records": rows, "tasks": tasks,
        "all_three_missed_anomaly_minutes": {n: int(totals[r, 1, 3]) for r, n in enumerate(("global", "adapter", "ensemble"))},
        "interpretation": "Only measured disagreements; OR-ing flags also adds these false alarms. No model fit/selection from this final analysis."})
    print(rows, flush=True)


if __name__ == "__main__": analyze()
