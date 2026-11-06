"""Train-only, frozen group thresholds with support-aware global fallback."""
from dataclasses import dataclass
import numpy as np
import pandas as pd


def q_label(q):
    return "p99" if q == .99 else "p99_9"


@dataclass
class QuantileBook:
    thresholds: dict
    support: dict
    quantiles: tuple
    columns: tuple

    @classmethod
    def fit(cls, reference, config):
        thresholds, support = {}, {}
        groups = [("GLOBAL", reference)] + [(str(a), g) for a, g in reference.groupby("adapterType", observed=True, dropna=True)]
        for name, group in groups:
            thresholds[name], support[name] = {}, {}
            for c in config.quantile_columns:
                s = group[c].dropna()
                s = s.loc[s.ge(0)]
                support[name][c] = len(s)
                for q in config.quantiles:
                    minimum = config.min_p99_support if q == .99 else config.min_p999_support
                    if len(s) >= minimum:
                        thresholds[name][(c, q)] = float(s.quantile(q))
        return cls(thresholds, support, tuple(config.quantiles), tuple(config.quantile_columns))

    def evaluate(self, frame):
        output = pd.DataFrame(index=frame.index)
        adapter = frame.adapterType.astype("string")
        any_comparable = pd.Series(False, index=frame.index)
        any_exceed = pd.Series(False, index=frame.index)
        for c in self.columns:
            for q in self.quantiles:
                name = f"{q_label(q)}_{c}"
                fallback = self.thresholds.get("GLOBAL", {}).get((c, q), np.nan)
                local = {a: d[(c, q)] for a, d in self.thresholds.items() if a != "GLOBAL" and (c, q) in d}
                value = adapter.map(local).astype(float)
                basis = pd.Series("unavailable", index=frame.index, dtype="string")
                basis.loc[value.notna()] = "adapter"
                if np.isfinite(fallback):
                    basis.loc[value.isna()] = "global"
                value = value.fillna(fallback)
                comparable = frame[c].notna() & frame[c].ge(0) & value.notna()
                exceed = pd.Series(pd.NA, index=frame.index, dtype="boolean")
                exceed.loc[comparable] = frame.loc[comparable, c].gt(value.loc[comparable])
                output[name + "_threshold"] = value
                output[name + "_basis"] = basis.astype("category")
                output[name + "_exceed"] = exceed
                any_comparable |= comparable
                any_exceed |= exceed.fillna(False)
        tail = pd.Series(pd.NA, index=frame.index, dtype="boolean")
        tail.loc[any_comparable] = any_exceed.loc[any_comparable]
        return tail, output

    def table(self):
        return pd.DataFrame([{"group": group, "metric": c, "quantile": q, "threshold": v,
                              "reference_rows": self.support[group][c]}
                             for group, items in self.thresholds.items() for (c, q), v in items.items()])
