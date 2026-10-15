"""Readable, conventional bar/histogram reports; no fitting or threshold edits."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"code"))
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

from model_evaluation.models import METHODS
from model_evaluation.metrics import Counts
from model_improvement.refine import OUTPUT, ALL_FEATURES

LABELS = ["IF 전체", "IF Adapter", "IF 앙상블", "Clustering 전체", "Clustering Adapter", "Clustering 앙상블"]
SCENARIO_NAMES = {"individual_delay": "개별 지연", "group_delay": "집단 지연", "column_relation": "컬럼 관계",
                  "time_burst": "시간대 폭주", "input_stop": "입력 중단", "gradual_degradation": "점진적 악화", "mixed_hard": "혼합 Hard"}
COLORS = ["#376998"]*3+["#3A826E"]*3


def setup():
    plt.rcParams.update({"font.family": "Malgun Gothic", "font.size": 11, "axes.unicode_minus": False,
                         "figure.dpi": 110, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.grid": False, "figure.constrained_layout.use": True})


def read_results():
    return {name: json.loads((OUTPUT/f"{name}.json").read_text(encoding="utf-8"))
            for name in ("summary", "selection", "preparation", "validation_results", "role_analysis")}


def metrics_table(records):
    frame = pd.DataFrame(records).set_index("method").reindex(METHODS)
    frame.index = LABELS
    cols = [c for c in ("accuracy", "precision", "recall", "f1", "normal_false_positive_rate") if c in frame]
    frame = frame[cols]*100
    return frame.rename(columns={"accuracy": "Accuracy (%)", "precision": "Precision (%)", "recall": "Recall (%)",
                                 "f1": "F1 (%)", "normal_false_positive_rate": "정상 오탐률 (%)"}).round(2)


def metric_bars(summary):
    setup()
    frame = pd.DataFrame(summary["pooled"]).set_index("method").loc[list(METHODS)]
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    for ax, metric, title in zip(axes, ("f1", "recall", "normal_false_positive_rate"), ("F1", "이상 탐지 Recall", "정상 로그 오탐률")):
        values = frame[metric].to_numpy()*100
        ax.barh(LABELS[::-1], values[::-1], color=COLORS[::-1], height=.65)
        ax.set_xlim(0, max(values.max()*1.3, 1))
        ax.set_xlabel("%")
        ax.set_title(title)
        for i, value in enumerate(values[::-1]):
            ax.text(value+.2, i, f"{value:.2f}", va="center", fontsize=10)
    fig.suptitle("동일한 최종 평가 로그에서 6개 전략 비교")
    return fig


def scenario_bars(summary):
    setup()
    fig, axes = plt.subplots(2, 4, figsize=(18, 9), sharey=True)
    for ax, dataset in zip(axes.ravel(), summary["datasets"]):
        values = [r["f1"]*100 for r in dataset["metrics"]]
        ax.bar(range(6), values, color=COLORS, width=.7)
        ax.set_ylim(0, 100)
        ax.set_xticks(range(6), ["IF 전체", "IF A", "IF E", "K 전체", "K A", "K E"], rotation=45, ha="right")
        ax.set_title(SCENARIO_NAMES[dataset["dataset"]])
        ax.set_ylabel("F1 (%)")
        for i, value in enumerate(values):
            ax.text(i, value+1, f"{value:.1f}", ha="center", fontsize=9)
    axes.ravel()[-1].axis("off")
    fig.suptitle("시나리오별 F1: A=Adapter, E=전체+Adapter 앙상블")
    return fig


def baseline_comparison(summary):
    old = json.loads((ROOT/"reports/model_evaluation_v2/summary.json").read_text(encoding="utf-8"))
    baseline = pd.DataFrame(old["pooled"]["window"]).set_index("method").loc[list(METHODS)]
    new = pd.DataFrame(summary["pooled"]).set_index("method").loc[list(METHODS)]
    if not np.array_equal(baseline[["n", "normal", "anomaly"]], new[["n", "normal", "anomaly"]]):
        raise ValueError("Baseline and new evaluation denominators differ")
    rows = pd.DataFrame({"기존 F1 (%)": baseline.f1*100, "개선 F1 (%)": new.f1*100,
                         "F1 변화 (%p)": (new.f1-baseline.f1)*100,
                         "기존 오탐 (%)": baseline.normal_false_positive_rate*100,
                         "개선 오탐 (%)": new.normal_false_positive_rate*100})
    rows.index = LABELS
    return rows.round(2)


def confirmation_bars(validation):
    setup()
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    stages = ("baseline_v2", "features_default", "features_tuned")
    for ax, metric, title in zip(axes, ("f1", "normal_false_positive_rate"), ("F1", "정상 오탐률")):
        for j, (name, color, label) in enumerate(zip(stages, ("#AAB3BC", "#BD914A", "#376998"),
                                                  ("기존 v2", "관계 피처 추가·기본 설정", "검증 탐색으로 선택"))):
            data = validation[name]["confirmation"]["pooled"]
            values = [r[metric]*100 for r in data]
            ax.bar(np.arange(6)+(j-1)*.24, values, width=.22, color=color, label=label)
        ax.set_xticks(range(6), LABELS, rotation=25, ha="right")
        ax.set_ylabel("%")
        ax.set_ylim(bottom=0)
        ax.set_title(f"새 확인용 사례에서 {title}")
    axes[0].legend(fontsize=9)
    fig.suptitle("별도 확인 집단: 결과를 확인한 뒤 설정을 다시 고르지 않음")
    return fig


def overlap_bars(analysis, matched=False):
    setup()
    key = "matched_budget_overlap" if matched else "deployed_overlap"
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    names = [SCENARIO_NAMES[d["dataset"]] for d in analysis["datasets"]]
    labels = ("둘 다 경보", "IF만 경보", "Clustering만 경보", "둘 다 경보 없음")
    colors = ("#8075A8", "#376998", "#3A826E", "#CED2D6")
    for ax, truth in zip(axes, ("anomaly", "normal")):
        bottom = np.zeros(len(names))
        for category, label, color in zip(("both", "if_only", "kmeans_only", "neither"), labels, colors):
            values = np.array([next(r["rate"] for r in d[key] if r["truth"] == truth and r["category"] == category)
                               for d in analysis["datasets"]])*100
            ax.bar(names, values, bottom=bottom, color=color, label=label, width=.7)
            bottom += values
        ax.set_ylim(0, 100)
        ax.set_ylabel("집단 내 비중 (%)")
        ax.tick_params(axis="x", rotation=35)
        ax.set_title("실제 이상 구간" if truth == "anomaly" else "실제 정상 구간 (탐지=오탐)")
    axes[0].legend(loc="upper center", ncol=2, fontsize=9)
    fig.suptitle("두 앙상블의 경보 겹침"+(" — 확인용 정상 오탐 1%에 맞춘 진단" if matched else " — 선택된 실제 threshold"))
    return fig


def score_histograms(dataset="mixed_hard"):
    setup()
    with np.load(OUTPUT/dataset/"observed.npz", allow_pickle=False) as file:
        score, y = file["margin"], file["truth"]
    with np.load(OUTPUT/dataset/"normal.npz", allow_pickle=False) as file:
        score = np.vstack([score, file["margin"]])
        y = np.concatenate([y, file["truth"]])
    fig, axes = plt.subplots(2, 3, figsize=(15, 9))
    for j, ax in enumerate(axes.ravel()):
        lo, hi = np.quantile(score[:, j], [.005, .995])
        lo, hi = min(lo, -.05), max(hi, .05)
        bins = np.linspace(lo, hi, 42)
        for label, mask, color in (("정상", ~y, "#7D9AB5"), ("이상", y, "#CE795E")):
            values = score[mask, j]
            # Percent of ALL class rows, not renormalized visible portion.
            ax.hist(values, bins=bins, weights=np.full(len(values), 100/len(values)),
                    color=color, alpha=.6, label=f"{label} ({len(values):,})", edgecolor="white", linewidth=.4)
        omitted = int(np.sum((score[:, j] < lo) | (score[:, j] > hi)))
        ax.axvline(0, color="#A43F35", linestyle="--", linewidth=1.5, label="탐지 경계=0")
        ax.set_title(LABELS[j]+f" (그림 밖 {omitted:,}개)")
        ax.set_xlabel("보정된 점수 - 해당 경로 threshold")
        ax.set_ylabel("집단 전체 행 대비 비중 (%)")
        ax.legend(fontsize=8)
    fig.suptitle(SCENARIO_NAMES[dataset]+": 정상·이상 점수 분포 (일반 히스토그램)")
    return fig


def input_histograms():
    setup()
    columns = ("arrival_dev_1m", "arrival_dev_5m", "zero_run", "residual_max_1m", "residual_p95_5m", "residual_change")
    labels = ("1분 요청 편차", "5분 요청 편차", "연속 요청 없음(log1p)", "1분 최대 조건부 지연", "5분 P95 조건부 지연", "5분-15분 평균 지연 변화")
    with np.load(OUTPUT/"mixed_hard/observed.npz", allow_pickle=False) as file:
        values, y = file["features"], file["truth"]
    with np.load(OUTPUT/"mixed_hard/normal.npz", allow_pickle=False) as file:
        values = np.vstack([values, file["features"]])
        y = np.concatenate([y, file["truth"]])
    fig, axes = plt.subplots(2, 3, figsize=(15, 8))
    for ax, column, label in zip(axes.ravel(), columns, labels):
        sample = values[:, ALL_FEATURES.index(column)]
        finite = np.isfinite(sample)
        lo, hi = np.quantile(sample[finite], [.005, .995])
        if lo == hi: hi = lo+1
        bins = np.linspace(lo, hi, 35)
        for text, mask, color in (("정상", ~y, "#7D9AB5"), ("이상", y, "#CE795E")):
            ax.hist(sample[mask & finite], bins=bins, weights=np.full(np.sum(mask & finite), 100/np.sum(mask)),
                    color=color, alpha=.6, edgecolor="white", linewidth=.4, label=text)
        missing = int(np.sum(~finite))
        outside = int(np.sum(finite & ((sample < lo) | (sample > hi))))
        ax.set_title(label+f"\n결측 {missing:,} / 표시 범위 밖 {outside:,}", fontsize=10)
        ax.set_ylabel("집단 전체 행 대비 비중 (%)")
        ax.legend(fontsize=9)
    fig.suptitle("Hard 입력 분포: 겹치는 영역은 실제로 판별하기 어려움")
    return fig


def supported_table(summary):
    counts = Counts()
    for d in summary["datasets"]:
        counts.values += Counts.from_records(d["supported_only_metrics"]).values
    return metrics_table(counts.records())


def adapter_bars(analysis):
    setup()
    supported = [r for r in analysis["adapters"] if r["expert_available"]]
    fig, axes = plt.subplots(1, 2, figsize=(15, 5))
    for ax, algo, offset in zip(axes, ("IF", "Clustering"), (0, 3)):
        x = np.arange(len(supported))
        for j, (color, label) in enumerate(zip(("#A5AFB8", "#376998", "#3A826E"), ("전체", "Adapter", "앙상블"))):
            values = [a["metrics"][offset+j]["f1"]*100 for a in supported]
            ax.bar(x+(j-1)*.24, values, width=.22, label=label, color=color)
        ax.set_xticks(x, [a["adapter"] for a in supported], rotation=25, ha="right")
        ax.set_ylabel("F1 (%)")
        ax.set_ylim(0, 100)
        ax.set_title(algo+": 동일한 Adapter 내 비교")
        ax.legend(fontsize=9)
    fig.suptitle("전문 모델이 있다고 항상 전체 모델보다 좋은 것은 아님")
    return fig


def event_table(summary):
    rows = []
    for d in summary["datasets"]:
        for record in d["events"]["per_type"]:
            if record["method"] in ("if_ensemble", "kmeans_ensemble"):
                rows.append({"평가셋": SCENARIO_NAMES[d["dataset"]], "이상 유형": SCENARIO_NAMES[record["scenario"]],
                             "모델": "IF" if record["method"].startswith("if") else "Clustering",
                             "Event Recall (%)": record["event_recall"]*100,
                             "탐지 이벤트": record["detected"], "전체 이벤트": record["events"],
                             "첫 탐지 지연(초)": record["median_delay_seconds"]})
    return pd.DataFrame(rows).round(2)


def timeline(dataset="input_stop", case_index=0):
    setup()
    with np.load(OUTPUT/dataset/"observed.npz", allow_pickle=False) as file:
        selected = file["case_index"] == case_index
        values, score, y = file["features"][selected], file["margin"][selected], file["truth"][selected]
        times = file["window_end_seconds"][selected]
    if not len(times): raise ValueError("Case absent")
    t = (times-times.min())/60
    fig, axes = plt.subplots(3, 1, figsize=(13, 8), sharex=True)
    if dataset == "gradual_degradation":
        for col, label, color in (("log_p95_duration_5m", "5분 P95", "#7D9AB5"),
                                  ("log_p50_duration_5m", "5분 중앙값", "#BD914A")):
            axes[0].plot(t, np.expm1(values[:, ALL_FEATURES.index(col)]), label=label, color=color)
        axes[0].set_ylabel("완료 지연 (ms)")
        axes[0].set_ylim(bottom=0)
        axes[0].legend(fontsize=9)
        axes[1].plot(t, values[:, ALL_FEATURES.index("residual_change")], color="#BD914A")
        axes[1].set_ylabel("5분-15분 지연 변화")
    else:
        from model_evaluation.windowing import PriorHistory
        history = PriorHistory(ROOT/"data/evaluation/reference_history_windows.csv")
        prior = history.lookup(pd.to_datetime(times-60, unit="s"), np.ones(len(times), int))
        counts = np.rint(values[:, ALL_FEATURES.index("arrival_dev_1m")]*(prior[:, 1]+1)+prior[:, 0])
        axes[0].bar(t, counts, color="#7D9AB5", width=.8)
        axes[0].set_ylabel("1분 실제 요청 수")
        axes[1].plot(t, np.expm1(values[:, ALL_FEATURES.index("zero_run")]), color="#BD914A")
        axes[1].set_ylabel("연속 요청 없음 (분)")
    for j, label, color in ((2, "IF 앙상블", "#376998"), (5, "Clustering 앙상블", "#3A826E")):
        axes[2].plot(t, score[:, j], label=label, color=color)
    axes[2].axhline(0, color="#A43F35", linestyle="--", label="각 모델의 탐지 경계")
    axes[2].set_ylabel("threshold 대비 점수")
    axes[2].legend(fontsize=9)
    axes[2].set_xlabel("에피소드 경과 분 (사례 번호 0을 고정 사용)")
    for ax in axes:
        for a, b in zip(t[y], t[y]+1): ax.axvspan(a-.5, b-.5, color="#CE795E", alpha=.15)
    fig.suptitle(SCENARIO_NAMES[dataset]+": 음영은 실제 주입 구간, 0 초과가 경보")
    return fig
