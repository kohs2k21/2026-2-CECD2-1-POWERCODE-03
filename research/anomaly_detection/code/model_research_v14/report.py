"""Plain bar charts, thresholds and honest comparisons for nine strategies."""
from pathlib import Path
import json
import joblib
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "reports/model_research_v14"
LABELS = tuple(f"{a} {r}" for a in ("IF", "K-means", "LOF") for r in ("전체", "Adapter", "앙상블"))
TASKS = {"individual_delay": "개별 지연", "group_delay": "집단 지연", "column_relation": "컬럼 관계",
         "time_burst": "시간대 폭주", "input_stop": "입력 중단", "gradual_degradation": "점진적 악화", "mixed_hard": "혼합 Hard"}


def read(path): return json.loads(Path(path).read_text(encoding="utf-8"))
def location(): return ROOT / read(OUTPUT / "evaluation_index.json")["current"]
def summary(): return read(location() / "summary.json")


def setup():
    plt.rcParams.update({"font.family": "Malgun Gothic", "axes.unicode_minus": False, "figure.dpi": 115,
                         "font.size": 11, "axes.spines.top": False, "axes.spines.right": False})


def table(records, offset=0):
    return pd.DataFrame([{"모델": LABELS[i+offset], "평가 분 수": r.get("n"), **{title: r[key]*100 for title, key in (
        ("Accuracy (%)", "accuracy"), ("Precision (%)", "precision"), ("Recall (%)", "recall"),
        ("F1 (%)", "f1"), ("정상 오탐 (%)", "normal_false_positive_rate"))}} for i, r in enumerate(records)]).round(2)


def bars():
    records = summary()["pooled"]; fig, axes = plt.subplots(2, 2, figsize=(15, 10)); x = np.arange(3)
    for ax, key, title in zip(axes.flat, ("accuracy", "precision", "recall", "f1"), ("Accuracy", "Precision", "Recall", "F1")):
        for route, name, color in ((0, "전체", "#3479A8"), (1, "Adapter", "#5A9C77"), (2, "앙상블", "#DA9D46")):
            b = ax.bar(x+(route-1)*.25, [records[a*3+route][key]*100 for a in range(3)], .25, label=name, color=color)
            ax.bar_label(b, fmt="%.1f", padding=3, fontsize=10)
        ax.set_xticks(x, ["Isolation Forest", "MiniBatch K-means", "LOF"])
        ax.set_ylim(0, 105); ax.set_ylabel("%"); ax.set_title(title); ax.grid(axis="y", alpha=.2); ax.set_axisbelow(True)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", bbox_to_anchor=(.5, .005), ncol=3, frameon=False)
    fig.tight_layout(rect=(0, .06, 1, 1)); return fig


def before_after():
    old = read(ROOT / "reports/benchmark_mild_v1/summary.json")["pooled"]
    new = summary()["pooled"][:6]
    rows = []
    for i, (a, b) in enumerate(zip(old, new)):
        rows.append({"모델": LABELS[i], **{f"{title} 변화 (%p)": (b[key]-a[key])*100 for title, key in (
            ("Accuracy", "accuracy"), ("Precision", "precision"), ("Recall", "recall"), ("F1", "f1"), ("정상 오탐", "normal_false_positive_rate"))},
            "기존 F1 (%)": a["f1"]*100, "새 F1 (%)": b["f1"]*100})
    return pd.DataFrame(rows).round(2)


def scenario_bars():
    data = summary()["datasets"]; fig, axes = plt.subplots(3, 2, figsize=(17, 15)); x = np.arange(len(data))
    for a in range(3):
        for j, key in enumerate(("f1", "normal_false_positive_rate")):
            ax = axes[a, j]
            for route, color in enumerate(("#3479A8", "#5A9C77", "#DA9D46")):
                ax.bar(x+(route-1)*.25, [d["metrics"][a*3+route][key]*100 for d in data], .25,
                       label=("전체", "Adapter", "앙상블")[route], color=color)
            ax.set_xticks(x, [TASKS[d["dataset"]] for d in data], rotation=25, ha="right")
            ax.set_title(("IF", "K-means", "LOF")[a]+(" — F1" if j == 0 else " — 정상 오탐"))
            ax.set_ylabel("%"); ax.set_ylim(bottom=0); ax.legend(); ax.grid(axis="y", alpha=.2); ax.set_axisbelow(True)
    fig.tight_layout(); return fig


def score_histograms():
    # Equal per-file sampling for readability; metrics use ALL decisions.
    rng = np.random.default_rng(42); scores, truth = [], []
    for d in summary()["datasets"]:
        for cohort in ("observed", "normal"):
            with np.load(location() / d["dataset"] / (cohort+".npz"), allow_pickle=False) as arrays:
                ids = rng.choice(len(arrays["truth"]), min(5000, len(arrays["truth"])), replace=False)
                scores.append(arrays["margin"][ids]); truth.append(arrays["truth"][ids])
    score, y = np.vstack(scores), np.concatenate(truth)
    fig, axes = plt.subplots(3, 3, figsize=(17, 13))
    for i, ax in enumerate(axes.flat):
        lo, hi = np.quantile(score[:, i], [.005, .995]); lo, hi = min(lo, -.01), max(hi, .01)
        # Zero is a BIN EDGE. Exact ties are non-alerts (strict >0), so their
        # plotted bin is the left/negative side instead of a misleading right bin.
        step = (hi-lo)/36
        bins = np.arange(np.floor(lo/step), np.ceil(hi/step)+1) * step
        groups = []
        for mask in (~y, y):
            vals = np.clip(score[mask, i], lo, hi)
            vals[vals == 0] = np.nextafter(0., -np.inf)
            groups.append(vals)
        ax.hist(groups, bins=bins, weights=[np.ones(len(v))*100/len(v) for v in groups],
                alpha=.9, color=["#3479A8", "#D67854"], label=["정상", "주입 이상"], histtype="bar")
        ax.axvline(0, color="#222222", linestyle="--", linewidth=1.5, label="판정선 0")
        ax.set_title(LABELS[i]); ax.set_xlabel("점수 - 해당 문맥의 threshold"); ax.set_ylabel("각 집단 내 비중 (%)"); ax.legend(fontsize=9)
    fig.tight_layout(); return fig


def input_histograms():
    frame = joblib.load(location() / "analysis_samples.joblib")
    columns = (("native_execution_arrivals", "1분 실행 수"), ("duration_p95_ms", "완료 소요시간 P95 (ms)"),
               ("duration_upper_max_1m", "조건부 소요시간 위쪽 꼬리"), ("fixed_positive_cusum", "정상 기준 누적 양의 편차"))
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))
    for ax, (col, title) in zip(axes.flat, columns):
        v = frame[col].to_numpy(float)
        if col in ("native_execution_arrivals", "duration_p95_ms"): v = np.log1p(v)
        valid = np.isfinite(v); lo, hi = np.quantile(v[valid], [.005, .995]); hi = max(hi, lo+.01)
        bins = np.linspace(lo, hi, 32)
        groups = [np.clip(v[valid & frame.is_anomaly.eq(a).to_numpy()], lo, hi) for a in (False, True)]
        ax.hist(groups, bins=bins, weights=[np.ones(len(vals))*100/len(vals) for vals in groups], histtype="bar",
                alpha=.9, color=["#3479A8", "#D67854"], label=["정상", "주입 이상"])
        ax.set_title(title); ax.set_xlabel("log(1+x)" if col in ("native_execution_arrivals", "duration_p95_ms") else "피처 값")
        ax.set_ylabel("각 집단 내 비중 (%)"); ax.legend()
    fig.tight_layout(); return fig


def characteristics():
    rows = []
    for d in summary()["datasets"]:
        for algorithm in range(3):
            i = algorithm*3+2; r = d["metrics"][i]
            rows.append({"시나리오": TASKS[d["dataset"]], "알고리즘": ("IF", "K-means", "LOF")[algorithm],
                         "앙상블 F1 (%)": r["f1"]*100, "Recall (%)": r["recall"]*100,
                         "정상 오탐 (%)": r["normal_false_positive_rate"]*100})
    return pd.DataFrame(rows).round(2)


def threshold_table():
    manifest = read(OUTPUT / "handoff/manifest.json"); rows = []
    for model in manifest["models"]:
        for row in model["thresholds"]:
            if "threshold_on_base_margin" in row:
                rows.append({"알고리즘": model["algorithm"], "경로": ("전체", "Adapter", "앙상블")[row["route"]],
                             "Adapter 문맥": row["adapter"], "단계": row["phase"], "관측 gate": row["evidence_gate"],
                             "정상 분위수": row["quantile"], "기존 margin에 적용한 실제 threshold": row["threshold_on_base_margin"],
                             "보정 정상 분 수": row["normal_reference_windows"]})
    return pd.DataFrame(rows).round(5)


def event_table():
    rows = []
    for d in summary()["datasets"]:
        for r in d["events"]["per_type"]:
            if not r["method"].endswith("ensemble"): continue
            rows.append({"데이터셋": TASKS[d["dataset"]], "사건 종류": TASKS[r["scenario"]], "모델": r["method"],
                         "사건 수": r["events"], "탐지 사건 수": r["detected"], "사건 Recall (%)": r["event_recall"]*100,
                         "첫 탐지 지연 중앙값 — 탐지된 사건만 (초)": r["median_delay_seconds_detected_only"]})
    return pd.DataFrame(rows).round(2)
