"""Train-only Adapter reference tables; not per-Adapter estimators.

References describe Process-stream minute aggregates, NOT adapter-wide traffic.
Every transform uses completed/past minute inputs. No label/case name is a feature.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from model_research_v8.features import TRAFFIC
from model_research_v13.features import DRIFT, LATENCY
from model_research_v15.features import TEMPORAL

BASE = (*TRAFFIC, *LATENCY, *DRIFT, *TEMPORAL,
        "log_max_duration_1m", "log_p95_duration_5m", "log_mean_count_5m",
        "hour_sin", "hour_cos", "weekday_sin", "weekday_cos")
ADAPTER = ("adapter_max_duration_z", "adapter_p95_upper_tail",
           "adapter_count_p95_z", "adapter_count_p95_upper_tail",
           "adapter_count_p95_lower_tail", "adapter_workload_z",
           "adapter_stream_arrivals_z", "adapter_reference_log_duration",
           "adapter_delay_short_long", "adapter_delay_ewma")

DESCRIPTIONS = {
    "adapter_max_duration_z": "1분 최대 log 소요시간의 Adapter 과거 중앙값 대비 편차 / IQR",
    "adapter_p95_upper_tail": "최근 5분 P95가 같은 Adapter의 정상 5분 P95 분포에서 드물게 큰 정도",
    "adapter_count_p95_z": "Adapter + 최근 5분 평균 totalCount 구간 기준 log P95 편차 / IQR",
    "adapter_count_p95_upper_tail": "동일 Adapter·처리건수 구간에서 5분 P95가 드물게 큰 정도",
    "adapter_count_p95_lower_tail": "동일 Adapter·처리건수 구간에서 5분 P95가 드물게 작은 정도",
    "adapter_workload_z": "최근 5분 평균 totalCount의 Adapter 기준 편차 / IQR",
    "adapter_stream_arrivals_z": "현재 Process 스트림의 1분 실행 횟수를 Adapter·요일유형·시간대 정상 재생 분포와 비교",
    "adapter_reference_log_duration": "해당 Adapter·처리건수 구간의 정상 5분 log P95 중앙값",
    "adapter_delay_short_long": "처리건수 보정 지연의 최근 5분 평균 - 15분 평균",
    "adapter_delay_ewma": "처리건수 보정 지연의 양수 부분 EWMA(alpha=0.2)",
}


def _stats(values):
    values = np.sort(np.asarray(values, float))
    values = values[np.isfinite(values)]
    if not len(values):
        raise ValueError("No finite normal reference")
    q25, med, q75 = np.quantile(values, [.25, .5, .75])
    return float(med), float(max(q75-q25, .15)), values


def _tail(values, reference, upper):
    n = len(reference)
    rank = (n-np.searchsorted(reference, values, side="left") if upper
            else np.searchsorted(reference, values, side="right"))
    return -np.log((rank+1)/(n+1))


def _context(frame):
    result = frame.copy()
    result["_bin"] = np.floor(result.log_mean_count_5m.fillna(0)/np.log(2)).astype(int)
    dates = pd.to_datetime(result.window_start, format="ISO8601")
    result["_weekend"], result["_hour"] = (dates.dt.dayofweek >= 5).astype(int), dates.dt.hour
    result["_arrival"] = np.log1p(pd.to_numeric(result.native_execution_arrivals))
    return result


@dataclass
class AdapterReferences:
    tables: dict
    pooled: dict
    minimum: int = 30

    @classmethod
    def fit(cls, train, minimum=30):
        f = _context(train)
        definitions = {
            "max": ("log_max_duration_1m", ("adapterType",)),
            "p95": ("log_p95_duration_5m", ("adapterType",)),
            "count_p95": ("log_p95_duration_5m", ("adapterType", "_bin")),
            "workload": ("log_mean_count_5m", ("adapterType",)),
            "arrival": ("_arrival", ("adapterType", "_weekend", "_hour")),
            "arrival_adapter": ("_arrival", ("adapterType",)),
        }
        tables, pooled = {}, {}
        for name, (column, keys) in definitions.items():
            subset = f.loc[np.isfinite(f[column])]
            pooled[name] = _stats(subset[column])
            table = {}
            for key, part in subset.groupby(list(keys), observed=True, sort=False):
                if len(part) >= minimum:
                    table[key if isinstance(key, tuple) else (key,)] = _stats(part[column])
            tables[name] = table
        return cls(tables, pooled, minimum)

    def transform(self, original):
        f = _context(original).reset_index(drop=True)
        output = {name: np.full(len(f), np.nan) for name in ADAPTER}
        level = np.full(len(f), 2, int)
        support = np.zeros(len(f), int)
        for (adapter, count_bin, weekend, hour), ids in f.groupby(
                ["adapterType", "_bin", "_weekend", "_hour"], sort=False, observed=True).indices.items():
            part = f.iloc[ids]
            maximum = self.tables["max"].get((adapter,), self.pooled["max"])
            p95 = self.tables["p95"].get((adapter,), self.pooled["p95"])
            conditional = self.tables["count_p95"].get((adapter, count_bin))
            reference_level = 0
            if conditional is None:
                conditional = self.tables["p95"].get((adapter,))
                reference_level = 1
            if conditional is None:
                conditional, reference_level = self.pooled["count_p95"], 2
            workload = self.tables["workload"].get((adapter,), self.pooled["workload"])
            arrival = self.tables["arrival"].get((adapter, weekend, hour),
                self.tables["arrival_adapter"].get((adapter,), self.pooled["arrival"]))
            x = part.log_p95_duration_5m.to_numpy(float)
            valid = np.isfinite(x)
            output["adapter_max_duration_z"][ids] = (part.log_max_duration_1m-maximum[0])/maximum[1]
            output["adapter_workload_z"][ids] = (part.log_mean_count_5m-workload[0])/workload[1]
            output["adapter_stream_arrivals_z"][ids] = (part._arrival-arrival[0])/arrival[1]
            output["adapter_reference_log_duration"][ids] = conditional[0]
            output["adapter_count_p95_z"][ids] = (x-conditional[0])/conditional[1]
            for column, ref, upper in (
                ("adapter_p95_upper_tail", p95, True),
                ("adapter_count_p95_upper_tail", conditional, True),
                ("adapter_count_p95_lower_tail", conditional, False)):
                output[column][ids[valid]] = _tail(x[valid], ref[2], upper)
            level[ids], support[ids] = reference_level, len(conditional[2])
        for name, values in output.items():
            f[name] = values
        # Empty completion minutes stay unobserved, not zero-duration examples.
        delay = f.adapter_count_p95_z
        grouped = delay.groupby(f.case_id, sort=False)
        short = grouped.transform(lambda s: s.rolling(5, min_periods=1).mean())
        long = grouped.transform(lambda s: s.rolling(15, min_periods=1).mean())
        f["adapter_delay_short_long"] = short-long
        f["adapter_delay_ewma"] = delay.clip(lower=0).groupby(f.case_id, sort=False).transform(
            lambda s: s.ewm(alpha=.2, adjust=False).mean())
        # Diagnostics only, never passed to the detector as ID/rarity signals.
        f["adapter_reference_level"], f["adapter_reference_support"] = level, support
        return f.drop(columns=["_bin", "_weekend", "_hour", "_arrival"])
