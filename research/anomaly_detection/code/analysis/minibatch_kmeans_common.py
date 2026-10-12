"""Process-only MiniBatch K-means experiment and readable notebook figures.

No source/model files or analysis exports are written by this module. Training,
K diagnostics, and threshold fitting use separate chronological partitions.
Cluster distance contributions describe geometry, not incident root causes.
"""
from dataclasses import dataclass
import time

import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, MaxNLocator
import numpy as np
import pandas as pd
from sklearn.cluster import MiniBatchKMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from threadpoolctl import threadpool_limits

from process_anomaly_pipeline.features import FeatureEncoder
from process_anomaly_pipeline.pipeline import _balanced_sample

BLUE = "#366DA8"
RED = "#C83E4D"
GRAY = "#CBD3DD"
FEATURE_LABELS = {
    "log_duration_ms": "처리시간 log1p", "log_totalCount": "처리건수 log1p",
    "log_retryCount": "재시도 log1p", "missing_duration_ms": "처리시간 결측",
    "missing_totalCount": "처리건수 결측", "missing_retryCount": "재시도 결측",
    "hour_sin": "시간대 sin", "hour_cos": "시간대 cos",
    "weekday_sin": "요일 sin", "weekday_cos": "요일 cos",
}


@dataclass(frozen=True)
class KMeansConfig:
    selected_k: int = 6
    candidates: tuple = (2, 4, 6, 8, 12)
    max_train_rows: int = 100_000
    silhouette_rows: int = 2_000
    batch_size: int = 2_048
    max_iter: int = 100
    n_init: int = 5
    random_state: int = 42
    quantile: float = .99
    min_cluster_threshold_rows: int = 1_000
    score_batch_rows: int = 50_000
    plot_rows: int = 20_000

    def __post_init__(self):
        integer_names = ("selected_k", "max_train_rows", "silhouette_rows", "batch_size",
                         "max_iter", "n_init", "random_state", "min_cluster_threshold_rows",
                         "score_batch_rows", "plot_rows")
        for name in integer_names:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < (0 if name == "random_state" else 1):
                raise ValueError(f"Invalid {name}")
        if not self.candidates or any(isinstance(k, bool) or not isinstance(k, int) or k < 2 for k in self.candidates):
            raise ValueError("candidates must contain integers >= 2")
        if len(set(self.candidates)) != len(self.candidates) or self.selected_k not in self.candidates:
            raise ValueError("selected_k must occur in unique candidates")
        if self.silhouette_rows < 3 or not 0 < self.quantile < 1:
            raise ValueError("Invalid silhouette_rows or quantile")


def cluster_name(number):
    return f"C{int(number) + 1}"


def geometry(model, encoder, frame, label_map=None):
    """Nearest-center distance in the full standardized feature space."""
    x = encoder.transform(frame)
    with threadpool_limits(limits=2):
        raw = model.predict(x)
    deviations = x - model.cluster_centers_[raw]
    distance = np.linalg.norm(deviations, axis=1).astype(float)
    labels = raw if label_map is None else label_map[raw]
    return labels, distance


def threshold_table(labels, distances, k, quantile=.99, minimum=1_000):
    """Unsupported clusters use a disclosed pooled threshold, not an invented zero."""
    labels, distances = np.asarray(labels), np.asarray(distances, dtype=float)
    if len(labels) != len(distances) or not len(distances) or not np.isfinite(distances).all() or (distances < 0).any():
        raise ValueError("Threshold reference must be nonempty and finite")
    if not np.isin(labels, np.arange(k)).all() or not 0 < quantile < 1 or minimum < 1:
        raise ValueError("Invalid cluster labels or threshold settings")
    pooled = float(np.quantile(distances, quantile))
    records = []
    q_name = f"P{quantile * 100:g}"
    for c in range(k):
        values = distances[labels == c]
        supported = len(values) >= minimum
        records.append({"cluster": c, "threshold_rows": len(values),
                        "distance_threshold": float(np.quantile(values, quantile)) if supported else pooled,
                        "threshold_basis": f"cluster_{q_name}" if supported else f"GLOBAL_{q_name}_fallback",
                        "expected_tail_rows": len(values) * (1 - quantile),
                        "reference_exceed_rows": int((values > (np.quantile(values, quantile) if supported else pooled)).sum()),
                        "reference_median_distance": float(np.median(values)) if len(values) else np.nan})
    return pd.DataFrame(records), pooled


def fit_experiment(train, calibration, threshold, config=None):
    """K diagnostics use calibration only; fixed selected_k is not an optimum claim."""
    config = config or KMeansConfig()
    if min(len(train), len(calibration), len(threshold)) < 3:
        raise ValueError("Three nonempty independent partitions are required")
    sampled = _balanced_sample(train, config.max_train_rows, config.random_state)
    encoder = FeatureEncoder.fit(sampled)
    x_train = encoder.transform(sampled)
    if max(config.candidates) >= len(sampled):
        raise ValueError("K candidates must be smaller than training sample")
    if np.unique(x_train, axis=0).shape[0] < max(config.candidates):
        raise ValueError("Too few unique training feature vectors for the requested K candidates")
    diagnostic = calibration.sample(min(len(calibration), config.silhouette_rows), random_state=config.random_state)
    x_cal = encoder.transform(diagnostic)
    records, models = [], {}
    for k in config.candidates:
        started = time.monotonic()
        model = MiniBatchKMeans(n_clusters=k, batch_size=config.batch_size, max_iter=config.max_iter,
                               n_init=config.n_init, reassignment_ratio=.01,
                               random_state=config.random_state, compute_labels=True)
        with threadpool_limits(limits=2):
            model.fit(x_train)
            cal_labels = model.predict(x_cal)
            silhouette = (float(silhouette_score(x_cal, cal_labels))
                          if 1 < len(np.unique(cal_labels)) < len(x_cal) else np.nan)
        counts = np.bincount(model.labels_, minlength=k)
        records.append({"K": k, "train_mean_squared_distance": float(model.inertia_ / len(sampled)),
                        "calibration_silhouette": silhouette, "silhouette_rows": len(diagnostic),
                        "silhouette_observed_clusters": len(np.unique(cal_labels)),
                        "smallest_train_sample_cluster": int(counts.min()),
                        "fit_and_diagnostic_seconds": time.monotonic() - started,
                        "selected": k == config.selected_k})
        models[k] = model
    model = models[config.selected_k]
    # Readable C1..CK labels follow center processing time, not sklearn's arbitrary IDs.
    order = np.argsort(model.cluster_centers_[:, encoder.columns.index("log_duration_ms")], kind="stable")
    label_map = np.empty(config.selected_k, dtype=int)
    label_map[order] = np.arange(config.selected_k)
    th_labels, th_distances = geometry(model, encoder, threshold, label_map)
    thresholds, pooled = threshold_table(th_labels, th_distances, config.selected_k,
                                          config.quantile, config.min_cluster_threshold_rows)
    with threadpool_limits(limits=2):
        projection = PCA(n_components=2, random_state=config.random_state).fit(x_train)
    return {"config": config, "model": model, "encoder": encoder, "label_map": label_map,
            "center_order": order, "pca": projection, "thresholds": thresholds,
            "pooled_threshold": pooled, "k_diagnostics": pd.DataFrame(records),
            "train_sample": sampled, "diagnostic_sample": diagnostic,
            "threshold_labels": th_labels, "threshold_distances": th_distances}


def score_experiment(bundle, frame):
    """One result per row; callers keep unassessed records distinct from scoreable rows."""
    config = bundle["config"]
    labels = np.empty(len(frame), dtype=int)
    distances = np.empty(len(frame), dtype=float)
    for start in range(0, len(frame), config.score_batch_rows):
        chunk = frame.iloc[start:start + config.score_batch_rows]
        labels[start:start + len(chunk)], distances[start:start + len(chunk)] = geometry(
            bundle["model"], bundle["encoder"], chunk, bundle["label_map"])
    table = bundle["thresholds"].set_index("cluster")
    limits = table.distance_threshold.reindex(labels).to_numpy(dtype=float)
    basis = table.threshold_basis.reindex(labels).to_numpy()
    return pd.DataFrame({"cluster": labels, "distance": distances, "distance_threshold": limits,
                         "threshold_basis": basis, "score_margin": distances - limits,
                         "model_candidate": distances > limits}, index=frame.index)


def distance_contributions(bundle, frame):
    """Exact additive squared-distance components; never causal importance/SHAP."""
    x = bundle["encoder"].transform(frame)
    with threadpool_limits(limits=2):
        raw_labels = bundle["model"].predict(x)
    squared = (x - bundle["model"].cluster_centers_[raw_labels]) ** 2
    return pd.DataFrame(squared, columns=bundle["encoder"].columns, index=frame.index)


def sample_for_plot(frame, maximum=20_000, random_state=42):
    """Uniform sample: cohort prevalence is preserved in expectation, never reweighted."""
    return frame.sample(min(len(frame), maximum), random_state=random_state)


def sample_projection_rows(frame, result, maximum=20_000, random_state=42, color_by="cluster"):
    """Candidate view reserves space for rare positives; it is not a prevalence sample."""
    if color_by == "cluster":
        return sample_for_plot(frame, maximum, random_state)
    flags = result.loc[frame.index, "model_candidate"].to_numpy(dtype=bool)
    positive, negative = frame.loc[flags], frame.loc[~flags]
    positive_quota = min(len(positive), max(maximum // 4, maximum - len(negative), 1))
    negative_quota = min(len(negative), maximum - positive_quota)
    return pd.concat([sample_for_plot(negative, negative_quota, random_state),
                      sample_for_plot(positive, positive_quota, random_state)])


def format_number(value):
    if pd.isna(value):
        return "n.a."
    if abs(value) >= 100 or float(value).is_integer():
        return f"{value:,.0f}"
    return f"{value:,.3f}"


def finish(fig):
    fig.tight_layout()
    plt.show()
    plt.close(fig)


def count_axis(ax, axis="y"):
    target = ax.yaxis if axis == "y" else ax.xaxis
    target.set_major_formatter(FuncFormatter(lambda x, _: f"{x:,.0f}"))
    target.set_major_locator(MaxNLocator(nbins=6, integer=True, min_n_ticks=2))


def plot_counts(values, title, xlabel="행 수", color=BLUE):
    fig, ax = plt.subplots(figsize=(12, max(3.8, .4 * len(values) + 1.3)))
    ax.barh(np.arange(len(values)), values.to_numpy(), color=color)
    ax.set_yticks(np.arange(len(values)), values.index.astype(str))
    ax.invert_yaxis()
    ax.set(title=title, xlabel=xlabel, xlim=(0, max(float(values.max()), 1) * 1.2))
    count_axis(ax, "x")
    for i, v in enumerate(values):
        ax.text(float(v), i, f"  {int(v):,}", va="center", fontsize=10)
    ax.grid(axis="x", alpha=.18)
    finish(fig)


def histogram(ax, values, title, xlabel, cutoff=None, central=.995, bins=45):
    """Ordinary bars, linear axes, exact full-cohort bin counts and audited clipping."""
    x = np.asarray(pd.Series(values).dropna(), dtype=float)
    x = x[np.isfinite(x)]
    if not len(x):
        ax.text(.5, .5, "평가 가능한 행 없음", transform=ax.transAxes, ha="center")
        ax.set_title(title)
        ax.set_axis_off()
        return
    # Keep every observation visible for small cohorts (especially rare candidates).
    shown_quantile = 1.0 if len(x) <= 1000 else central
    low, high = (float(np.quantile(x, 1 - shown_quantile)), float(np.quantile(x, shown_quantile)))
    if cutoff is not None and np.isfinite(cutoff):
        low, high = min(low, cutoff), max(high, cutoff)
    if low == high:
        pad = max(abs(low) * .02, .01)
        low, high = low - pad, high + pad
    edges = np.linspace(low, high, bins + 1)
    counts, _ = np.histogram(x, edges)
    outside = int((x < low).sum() + (x > high).sum())
    ax.bar(edges[:-1], counts, width=np.diff(edges), align="edge", color=BLUE, edgecolor="white", linewidth=.35)
    if cutoff is not None and np.isfinite(cutoff):
        ax.axvline(cutoff, color=RED, linestyle="--", linewidth=2,
                   label=f"판정선 {format_number(cutoff)}")
        ax.legend(loc="upper right", fontsize=9)
    ax.set(title=title, xlabel=xlabel, ylabel="Process 행 수", xlim=(low, high), ylim=(0, max(int(counts.max()), 1) * 1.13))
    count_axis(ax)
    ax.grid(axis="y", alpha=.18)
    ax.text(.01, -.38, f"전수 {len(x):,}행으로 계산 · 확대 범위 밖 {outside:,}행 (탐지에서는 유지)",
            transform=ax.transAxes, fontsize=9)


def plot_count_buckets(ax, values):
    """Count-domain buckets keep 1/10,000/millions readable on ordinary linear bars."""
    x = pd.Series(values)
    valid = x.notna() & np.isfinite(x) & x.ge(0) & x.mod(1).eq(0)
    labels = ["0", "1", "2-9", "10-99", "100-999", "1,000-9,999", "10,000-99,999", "100,000+"]
    bins = [-.5, .5, 1.5, 9.5, 99.5, 999.5, 9999.5, 99999.5, np.inf]
    counts = pd.cut(x.loc[valid], bins=bins, labels=labels).value_counts(sort=False).reindex(labels, fill_value=0)
    invalid = int((x.notna() & ~valid).sum())
    if invalid:
        counts.loc["음수/소수/비유한값"] = invalid
    ax.bar(np.arange(len(counts)), counts, color=BLUE, edgecolor="white", linewidth=.5)
    ax.set_xticks(np.arange(len(counts)), counts.index, rotation=32, ha="right", fontsize=9)
    ax.set(title="처리건수 구간별 분포", xlabel="처리건수 구간 (건; 구간 폭은 서로 다름)",
           ylabel="Process 행 수", ylim=(0, max(int(counts.max()), 1) * 1.18))
    count_axis(ax)
    for i, value in enumerate(counts):
        if value:
            ax.text(i, value, f"{int(value):,}", ha="center", va="bottom", fontsize=8)
    ax.grid(axis="y", alpha=.18)
    ax.text(.01, -.57, f"전수 {len(x):,}행 · 결측 {int(x.isna().sum()):,}행 별도 · 구간별 관측값 전수 집계",
            transform=ax.transAxes, fontsize=9)
    return counts


def plot_input_distributions(frame, cohort_label="입력 데이터"):
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.6))
    histogram(axes[0], frame.duration_ms / 1000, f"{cohort_label} 처리시간 분포", "처리시간 (초)")
    plot_count_buckets(axes[1], frame.totalCount)
    axes[1].set_title(f"{cohort_label} 처리건수 구간별 분포")
    fig.subplots_adjust(bottom=.40, wspace=.27)
    plt.show()
    plt.close(fig)


def plot_k_diagnostics(diagnostics, selected_k):
    fig, axes = plt.subplots(1, 2, figsize=(13, 4))
    axes[0].plot(diagnostics.K, diagnostics.train_mean_squared_distance, marker="o", color=BLUE)
    axes[1].plot(diagnostics.K, diagnostics.calibration_silhouette, marker="o", color=BLUE)
    for ax in axes:
        ax.axvline(selected_k, color=RED, linestyle="--", label=f"실험 K={selected_k}")
        ax.set(xlabel="군집 수 K", xticks=diagnostics.K)
        ax.grid(alpha=.2)
        ax.legend()
    axes[0].set(title="학습 표본의 평균 제곱거리", ylabel="군집 중심까지 평균 제곱거리", ylim=(0, None))
    axes[1].set(title="독립 calibration 표본의 silhouette", ylabel="silhouette (클수록 군집 분리 양호)")
    finish(fig)


def plot_projection(bundle, frame, result, *, color_by="cluster"):
    plotted = sample_projection_rows(frame, result, bundle["config"].plot_rows,
                                     bundle["config"].random_state, color_by)
    fig, ax = plt.subplots(figsize=(12, 6))
    x = bundle["encoder"].transform(plotted)
    points = bundle["pca"].transform(x)
    local = result.loc[plotted.index]
    if color_by == "cluster":
        palette = plt.get_cmap("tab10")
        for c in range(bundle["config"].selected_k):
            mask = local.cluster.eq(c).to_numpy()
            ax.scatter(points[mask, 0], points[mask, 1], s=8, alpha=.35,
                       color=palette(c), label=f"{cluster_name(c)} ({int(mask.sum()):,}표본)", rasterized=True)
        centers = bundle["pca"].transform(bundle["model"].cluster_centers_[bundle["center_order"]])
        ax.scatter(centers[:, 0], centers[:, 1], s=180, marker="X", color="#18293C", edgecolor="white", label="군집 중심")
        for c, center in enumerate(centers):
            ax.annotate(cluster_name(c), center, xytext=(7, 7), textcoords="offset points", fontsize=10, weight="bold")
        title = "Process 수치 패턴 군집의 PCA 투영"
        subtitle = f"전수 {len(frame):,}행 중 균등 무복원 표본 {len(plotted):,}행"
    else:
        mask = local.model_candidate.to_numpy(dtype=bool)
        ax.scatter(points[~mask, 0], points[~mask, 1], s=8, alpha=.3, color=GRAY, label=f"판정선 이하 ({int((~mask).sum()):,}표본)", rasterized=True)
        ax.scatter(points[mask, 0], points[mask, 1], s=45, alpha=.9, color=RED, edgecolor="white",
                   label=f"모델 후보 ({int(mask.sum()):,}표본)", rasterized=True)
        title = "이상 후보가 위치한 패턴 영역의 PCA 투영"
        all_positive = int(result.loc[frame.index, "model_candidate"].sum())
        subtitle = (f"판정선 이하 {int((~mask).sum()):,}/{len(frame) - all_positive:,}행 표본 + "
                    f"후보 {int(mask.sum()):,}/{all_positive:,}행 · 후보율 추정 표본 아님")
        if 0 < all_positive <= 10:
            for ordinal, row_id in enumerate(result.loc[frame.index].query("model_candidate").sort_values("score_margin", ascending=False).index, 1):
                if row_id in plotted.index:
                    xy = points[plotted.index.get_loc(row_id)]
                    ax.annotate(f"사례 {ordinal}", xy, xytext=(8, 6), textcoords="offset points", color=RED, fontsize=10)
    explained = bundle["pca"].explained_variance_ratio_
    ax.set(title=f"{title}\n{subtitle}",
           xlabel=f"PC1 (분산 {explained[0]:.1%})", ylabel=f"PC2 (분산 {explained[1]:.1%})")
    ax.legend(loc="best", fontsize=9, ncol=2)
    ax.grid(alpha=.15)
    ax.text(.01, -.25, "판정은 전체 10차원 거리에서 수행. 2차원 위치만으로 정상·이상을 판단하지 않음.", transform=ax.transAxes, fontsize=10)
    fig.subplots_adjust(bottom=.27)
    plt.show()
    plt.close(fig)
    return plotted.index


def plot_score_margins(result, title="미래 test의 군집별 판정선 초과폭"):
    fig, axes = plt.subplots(1, 2, figsize=(14, 4.9))
    histogram(axes[0], result.score_margin, title, "거리 - 해당 군집 판정선", cutoff=0)
    histogram(axes[1], result.loc[result.model_candidate, "score_margin"], "탐지 후보만 확대", "거리 - 해당 군집 판정선", cutoff=0, central=1)
    fig.subplots_adjust(bottom=.32, wspace=.25)
    plt.show()
    plt.close(fig)


def plot_cluster_distances(bundle, result):
    k = bundle["config"].selected_k
    fig, axes = plt.subplots(int(np.ceil(k / 2)), 2, figsize=(14, 4.2 * np.ceil(k / 2)), squeeze=False)
    thresholds = bundle["thresholds"].set_index("cluster")
    for c, ax in enumerate(axes.ravel()):
        if c >= k:
            ax.set_visible(False)
            continue
        row = thresholds.loc[c]
        values = result.loc[result.cluster.eq(c), "distance"]
        histogram(ax, values, f"{cluster_name(c)} · 기준 {row.threshold_basis}", "표준화 피처 공간의 중심 거리",
                  cutoff=row.distance_threshold)
    fig.subplots_adjust(hspace=.80, bottom=.12, wspace=.25)
    plt.show()
    plt.close(fig)


def cluster_profiles(frame, result, k):
    joined = frame[["duration_ms", "totalCount", "adapterType", "status", "startTime"]].join(result[["cluster", "model_candidate"]])
    records = []
    for c in range(k):
        group = joined.loc[joined.cluster.eq(c)]
        adapters = group.adapterType.astype("string").fillna("결측").value_counts()
        records.append({"cluster": c, "rows": len(group), "candidate_rows": int(group.model_candidate.sum()),
                        "candidate_pct": group.model_candidate.mean() * 100 if len(group) else np.nan,
                        "median_duration_seconds": group.duration_ms.median() / 1000,
                        "p95_duration_seconds": group.duration_ms.quantile(.95) / 1000,
                        "median_totalCount": group.totalCount.median(),
                        "top_adapter": adapters.index[0] if len(adapters) else "관측 없음",
                        "top_adapter_pct": adapters.iloc[0] / len(group) * 100 if len(group) else np.nan})
    return pd.DataFrame(records)


def comparison_if_global(pipeline, frame, score_batch_rows=50_000):
    """Read-only GLOBAL IF comparator; NOT the production GLOBAL+Adapter decision."""
    output = np.empty(len(frame), dtype=float)
    for start in range(0, len(frame), score_batch_rows):
        chunk = frame.iloc[start:start + score_batch_rows]
        output[start:start + len(chunk)] = pipeline.global_model.raw_score(chunk)
    cutoff = pipeline.global_model.raw_threshold
    return pd.DataFrame({"if_global_raw_score": output, "if_global_threshold": cutoff,
                         "if_global_candidate": output > cutoff}, index=frame.index)
