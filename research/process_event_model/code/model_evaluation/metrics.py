"""Explicit denominators and strict binary decisions; no point adjustment."""
import numpy as np

from .models import METHODS


def metric_record(tn, fp, fn, tp):
    tn, fp, fn, tp = map(int, (tn, fp, fn, tp))
    n, positive = tn + fp + fn + tp, tp + fn
    return {
        "n": n, "normal": tn+fp, "anomaly": positive,
        "tn": tn, "fp": fp, "fn": fn, "tp": tp,
        "accuracy": (tp+tn)/n if n else None,
        "precision": tp/(tp+fp) if tp+fp else (0. if n else None),
        "recall": tp/positive if positive else None,
        "f1": 2*tp/(2*tp+fp+fn) if positive else None,
        "normal_false_positive_rate": fp/(tn+fp) if tn+fp else None,
        "always_normal_accuracy": (tn+fp)/n if n else None,
    }


class Counts:
    def __init__(self):
        self.values = np.zeros((len(METHODS), 4), dtype=np.int64)  # TN, FP, FN, TP

    def update(self, truth, predictions, mask=None):
        y, pred = np.asarray(truth, dtype=bool), np.asarray(predictions, dtype=bool)
        if pred.shape != (len(y), len(METHODS)):
            raise ValueError("Prediction/label shape mismatch")
        if mask is not None:
            y, pred = y[mask], pred[mask]
        for i in range(len(METHODS)):
            p = pred[:, i]
            self.values[i] += [np.sum(~y & ~p), np.sum(~y & p), np.sum(y & ~p), np.sum(y & p)]

    def records(self):
        return [{"method": method, **metric_record(*self.values[i])} for i, method in enumerate(METHODS)]

    @classmethod
    def from_records(cls, records):
        result = cls()
        for i, method in enumerate(METHODS):
            row = next(r for r in records if r["method"] == method)
            result.values[i] = [row[c] for c in ("tn", "fp", "fn", "tp")]
        return result
