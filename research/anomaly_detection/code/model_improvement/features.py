"""Process-only, causal multi-scale features with training-only conditioning.

IDs route/look up baselines; IDs, STATUS, injection flags and counterfactuals are
not numeric model features. A decision at t uses arrivals/completions strictly
before t. Empty windows are observed absence, not fabricated Process rows.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from model_evaluation.data import unix_seconds

FEATURES = (
    "arrival_dev_1m", "arrival_dev_5m", "arrival_dev_15m",
    "arrival_logratio_1m", "arrival_logratio_5m", "arrival_logratio_15m",
    "zero_run", "arrival_ewma", "arrival_change",
    "residual_max_1m", "residual_mean_1m", "residual_p95_5m",
    "residual_mean_5m", "residual_mean_15m", "residual_change", "residual_ewma",
    "log_max_duration_1m", "log_p95_duration_5m", "log_p50_duration_5m",
    "log_mean_count_5m", "missing_duration_1m",
    "hour_sin", "hour_cos", "weekday_sin", "weekday_cos",
)
COMPACT = tuple(c for c in FEATURES if c not in {
    "arrival_logratio_1m", "arrival_logratio_5m", "arrival_logratio_15m",
    "residual_mean_1m", "residual_mean_15m", "log_p50_duration_5m",
    "log_mean_count_5m", "arrival_change",
})
DESCRIPTIONS = {
    "arrival_dev": "현재 요청 수와 같은 요일 유형·시간대 과거 중앙값 차이 / (과거 P95+1)",
    "arrival_logratio": "log((현재 요청 수+1)/(같은 시간대 과거 중앙값+1))",
    "zero_run": "현재까지 연속으로 요청이 없던 분 수의 log1p; 에피소드 밖 과거는 추정하지 않음",
    "arrival_ewma": "1분 요청 편차의 EWMA(alpha=0.2), 에피소드 시작에서 초기화",
    "arrival_change": "1분 편차에서 직전 5분 편차 평균을 뺀 값",
    "residual": "log1p(실측 소요시간)와 학습 정상 집단 중앙값의 차이 / 학습 IQR(최소 0.15)",
    "residual_change": "5분 잔차 평균 - 15분 잔차 평균; 짧은 창에서 악화되면 양수",
    "residual_ewma": "완료된 로그의 1분 평균 잔차 EWMA(alpha=0.2); 완료 없음은 정상 잔차 0으로 완충",
    "log_max_duration": "1분 동안 실제 완료된 Process 중 최대 소요시간의 log1p",
    "log_p95_duration": "5분 동안 실제 완료된 Process 소요시간 P95의 log1p (분별 P95의 평균 아님)",
    "log_mean_count": "5분 동안 실제 완료된 Process totalCount 평균의 log1p",
    "missing_duration": "현재 분에 완료 로그가 없으면 1, 있으면 0",
    "hour_weekday": "시간·요일의 sin/cos; 장애 발생 시각을 암기하는 ID 피처가 아님",
}


def count_bin(values):
    values = np.asarray(values, dtype=float)
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("Invalid totalCount; apply hard rules first")
    return np.floor(np.log2(values + 1)).astype(int)


@dataclass
class ConditionalBaseline:
    tables: list
    global_stats: tuple
    min_support: int = 30

    @classmethod
    def fit(cls, train, min_support=30):
        frame = train.copy()
        frame["_bin"] = count_bin(frame.totalCount)
        frame["_log"] = np.log1p(pd.to_numeric(frame.duration_ms))
        levels = (("adapterType", "processId", "_bin"), ("adapterType", "_bin"),
                  ("adapterType", "processId"), ("adapterType",))
        tables = []
        for keys in levels:
            stats = {}
            for key, group in frame.groupby(list(keys), observed=True):
                if len(group) >= min_support:
                    key = key if isinstance(key, tuple) else (key,)
                    q25, median, q75 = np.quantile(group._log, [.25, .5, .75])
                    stats[key] = (float(median), float(max(q75-q25, .15)), len(group))
            tables.append((keys, stats))
        q25, median, q75 = np.quantile(frame._log, [.25, .5, .75])
        return cls(tables, (float(median), float(max(q75-q25, .15)), len(frame)), min_support)

    def transform(self, rows):
        bins = count_bin(rows.totalCount)
        result, levels = np.empty(len(rows)), np.empty(len(rows), dtype=int)
        for i, (adapter, process, b, duration) in enumerate(zip(
                rows.adapterType, rows.processId, bins, rows.duration)):
            lookup = {"adapterType": adapter, "processId": process, "_bin": b}
            stats, level = self.global_stats, len(self.tables)
            for j, (keys, table) in enumerate(self.tables):
                found = table.get(tuple(lookup[k] for k in keys))
                if found is not None:
                    stats, level = found, j
                    break
            result[i] = (np.log1p(duration)-stats[0])/stats[1]
            levels[i] = level
        return result, levels


def _rolling(frame, values, width, operation="sum"):
    groups = pd.Series(values, index=frame.index).groupby(frame.case_id, sort=False)
    return getattr(groups.rolling(width, min_periods=1), operation)().reset_index(
        level=0, drop=True).reindex(frame.index).to_numpy()


def build_features(native, completions, history, baseline):
    frame = native.sort_values(["case_id", "window_start"], kind="stable").reset_index(drop=True).copy()
    starts = pd.to_datetime(frame.window_start, format="ISO8601")
    ends = unix_seconds(frame.window_end)
    counts = pd.to_numeric(frame.execution_arrivals).to_numpy(dtype=float)
    if (counts < 0).any():
        raise ValueError("Negative observed arrivals")
    sizes = frame.groupby("case_id", sort=False).cumcount().to_numpy()+1
    frame["native_execution_arrivals"] = counts
    for width in (1, 5, 15):
        prior = history.lookup(starts, np.minimum(sizes, width))
        observed = _rolling(frame, counts, width)
        frame[f"arrival_dev_{width}m"] = (observed-prior[:, 0])/(prior[:, 1]+1)
        frame[f"arrival_logratio_{width}m"] = np.log((observed+1)/(prior[:, 0]+1))
    zero = np.zeros(len(frame))
    for ids in frame.groupby("case_id", sort=False).groups.values():
        run = 0
        for i in ids:
            run = run+1 if counts[i] == 0 else 0
            zero[i] = np.log1p(run)
    frame["zero_run"] = zero
    frame["arrival_ewma"] = frame.groupby("case_id", sort=False).arrival_dev_1m.transform(
        lambda s: s.ewm(alpha=.2, adjust=False).mean())
    past5 = frame.groupby("case_id", sort=False).arrival_dev_1m.transform(
        lambda s: s.shift().rolling(5, min_periods=1).mean()).fillna(0)
    frame["arrival_change"] = frame.arrival_dev_1m-past5
    raw = completions.copy()
    if len(raw):
        raw["residual"], raw["baseline_level"] = baseline.transform(raw)
    raw_groups = {c: g.sort_values("end_seconds", kind="stable")
                  for c, g in raw.groupby("case_id", sort=False)}
    columns = ["residual_max_1m", "residual_mean_1m", "residual_p95_5m",
               "residual_mean_5m", "residual_mean_15m", "log_max_duration_1m",
               "log_p95_duration_5m", "log_p50_duration_5m", "log_mean_count_5m"]
    output = {c: np.full(len(frame), np.nan) for c in columns}
    missing = np.ones(len(frame))
    for case, ids in frame.groupby("case_id", sort=False).groups.items():
        if case not in raw_groups:
            continue
        group = raw_groups[case]
        times, residuals, durations, totals = (group[c].to_numpy(dtype=float)
                                            for c in ("end_seconds", "residual", "duration", "totalCount"))
        ids = np.asarray(ids)
        hi = np.searchsorted(times, ends[ids], side="left")
        for width in (1, 5, 15):
            lo = np.searchsorted(times, ends[ids]-np.minimum(sizes[ids], width)*60, side="left")
            for index, first, last in zip(ids, lo, hi):
                if last <= first:
                    continue
                r, d = residuals[first:last], durations[first:last]
                output[f"residual_mean_{width}m"][index] = r.mean()
                if width == 1:
                    missing[index] = 0
                    output["residual_max_1m"][index] = r.max()
                    output["log_max_duration_1m"][index] = np.log1p(d.max())
                elif width == 5:
                    output["residual_p95_5m"][index] = np.quantile(r, .95)
                    output["log_p95_duration_5m"][index] = np.log1p(np.quantile(d, .95))
                    output["log_p50_duration_5m"][index] = np.log1p(np.quantile(d, .5))
                    output["log_mean_count_5m"][index] = np.log1p(totals[first:last].mean())
    for column, values in output.items():
        frame[column] = values
    frame["missing_duration_1m"] = missing
    frame["residual_change"] = frame.residual_mean_5m-frame.residual_mean_15m
    frame["residual_ewma"] = frame.residual_mean_1m.fillna(0).groupby(frame.case_id, sort=False).transform(
        lambda s: s.ewm(alpha=.2, adjust=False).mean())
    for name, value, period in (("hour", starts.dt.hour, 24), ("weekday", starts.dt.dayofweek, 7)):
        frame[name+"_sin"] = np.sin(2*np.pi*value/period)
        frame[name+"_cos"] = np.cos(2*np.pi*value/period)
    return frame
