"""Read-only tables and ordinary bars/histograms for v4 -> v5/v6/v7."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = {"v4": "model_improvement_v4", "v5": "model_research_v5", "v6": "model_research_v6", "v7": "model_research_v7"}
LABELS = ("IF 전체", "IF Adapter", "IF 앙상블", "K-means 전체", "K-means Adapter", "K-means 앙상블")
SCENARIOS = {"individual_delay": "개별 지연", "group_delay": "집단 지연", "column_relation": "컬럼 관계",
             "time_burst": "시간대 폭주", "input_stop": "입력 중단", "gradual_degradation": "점진적 악화", "mixed_hard": "혼합 Hard"}
COLORS = ("#9DA9B5", "#E9A54D", "#BD6571", "#2674AA")


def setup():
    plt.rcParams.update({"font.family": "Malgun Gothic", "axes.unicode_minus": False, "figure.dpi": 110,
                         "axes.spines.top": False, "axes.spines.right": False, "font.size": 11})


def read(version, name="summary"):
    return json.loads((ROOT/"reports"/VERSIONS[version]/f"{name}.json").read_text(encoding="utf-8"))


def metrics(records):
    return pd.DataFrame([{ "모델": LABELS[i], **{k: r[c]*100 for k, c in
        (("Accuracy (%)", "accuracy"), ("Precision (%)", "precision"), ("Recall (%)", "recall"),
         ("F1 (%)", "f1"), ("정상 오탐 (%)", "normal_false_positive_rate"))}} for i, r in enumerate(records)]).round(2)


def changes(version="v7"):
    old, new = read("v4")["pooled"], read(version)["pooled"]
    return pd.DataFrame([{"모델": LABELS[i], "v4 F1 (%)": a["f1"]*100, f"{version} F1 (%)": b["f1"]*100,
                         "F1 변화 (%p)": (b["f1"]-a["f1"])*100, "v4 오탐 (%)": a["normal_false_positive_rate"]*100,
                         f"{version} 오탐 (%)": b["normal_false_positive_rate"]*100,
                         "오탐 변화 (%p)": (b["normal_false_positive_rate"]-a["normal_false_positive_rate"])*100}
                        for i, (a, b) in enumerate(zip(old, new))]).round(2)


def compare_bars():
    fig, axes = plt.subplots(1, 2, figsize=(15, 5))
    x = np.arange(6); width = .19
    for j, version in enumerate(VERSIONS):
        records = read(version)["pooled"]
        for ax, metric in zip(axes, ("f1", "normal_false_positive_rate")):
            ax.bar(x+(j-1.5)*width, [r[metric]*100 for r in records], width, label=version, color=COLORS[j])
    for ax, title in zip(axes, ("동일 평가 행의 F1", "정상 행 중 오탐 비율 - 낮을수록 좋음")):
        ax.set_xticks(x, LABELS, rotation=25, ha="right"); ax.set_ylabel("%"); ax.set_title(title)
        ax.set_ylim(bottom=0); ax.grid(axis="y", alpha=.2); ax.set_axisbelow(True); ax.legend(ncol=2)
    fig.tight_layout(); return fig


def six_bars(version="v7"):
    records = read(version)["pooled"]
    fig, axes = plt.subplots(2, 3, figsize=(15, 9))
    for i, ax in enumerate(axes.flat):
        values = [records[i][m]*100 for m in ("accuracy", "precision", "recall", "f1", "normal_false_positive_rate")]
        bars = ax.bar(["Accuracy", "Precision", "Recall", "F1", "오탐"], values, color=["#ABB3BC", "#438AB5", "#5C9F89", "#2674AA", "#CC7A65"])
        ax.bar_label(bars, fmt="%.1f", padding=3, fontsize=10)
        ax.set_ylim(0, 105); ax.set_title(LABELS[i]); ax.set_ylabel("%"); ax.grid(axis="y", alpha=.2); ax.set_axisbelow(True)
    fig.suptitle(f"{version}: 평가 분모 2,167,766개 1분 의사결정", y=1.01)
    fig.tight_layout(); return fig


def scenario_bars(version="v7"):
    datasets = read(version)["datasets"]
    fig, axes = plt.subplots(2, 1, figsize=(13, 10)); x = np.arange(len(datasets)); width = .19
    for j, (v, m, name, color) in enumerate((("v4", 2, "v4 IF 앙상블", COLORS[0]), (version, 2, f"{version} IF 앙상블", "#2674AA"),
                                           ("v4", 5, "v4 K-means 앙상블", "#D0B29D"), (version, 5, f"{version} K-means 앙상블", "#4D947D"))):
        data = {d["dataset"]: d for d in read(v)["datasets"]}
        for ax, metric in zip(axes, ("f1", "normal_false_positive_rate")):
            ax.bar(x+(j-1.5)*width, [data[d["dataset"]]["metrics"][m][metric]*100 for d in datasets], width, label=name, color=color)
    for ax, title in zip(axes, ("시나리오별 F1", "시나리오별 정상 오탐 - 합산 수치에 가려진 문제")):
        ax.set_xticks(x, [SCENARIOS[d["dataset"]] for d in datasets]); ax.set_title(title); ax.set_ylabel("%")
        ax.set_ylim(bottom=0); ax.grid(axis="y", alpha=.2); ax.set_axisbelow(True); ax.legend(ncol=2)
    fig.tight_layout(); return fig


def sample_arrays(version="v7", maximum_per_cohort=12000):
    sample = []; rng = np.random.default_rng(42)
    for d in read(version)["datasets"]:
        for cohort in ("observed", "normal"):
            with np.load(ROOT/"reports"/VERSIONS[version]/d["dataset"]/f"{cohort}.npz", allow_pickle=False) as a:
                rows = rng.choice(len(a["truth"]), min(len(a["truth"]), maximum_per_cohort), replace=False)
                sample.append({"truth": a["truth"][rows], "margin": a["margin"][rows], "phase": a["phase"][rows] if "phase" in a else None,
                               "dataset": d["dataset"], "cohort": cohort})
    return sample


def margin_histograms(version="v7"):
    sample = sample_arrays(version)
    y, score = np.concatenate([a["truth"] for a in sample]), np.vstack([a["margin"] for a in sample])
    fig, axes = plt.subplots(2, 3, figsize=(15, 8))
    for i, ax in enumerate(axes.flat):
        values = score[:, i]; low, high = np.quantile(values, [.005, .995]); low, high = min(low, -.01), max(high, .01)
        bins = np.linspace(low, high, 41)
        for truth, label, color in ((False, "정상", "#438AB5"), (True, "주입 이상", "#D68855")):
            z = np.clip(values[y == truth], low, high)
            # Relative frequency within each class, not density/KDE.
            ax.hist(z, bins=bins, weights=np.ones(len(z))*100/max(len(z), 1), label=f"{label} n={len(z):,}", color=color, alpha=.55)
        ax.axvline(0, color="#B73542", linestyle="--", linewidth=1.5, label="판정선: margin=0")
        ax.set_title(LABELS[i]); ax.set_xlabel("정규화 점수 - 해당 경로 threshold"); ax.set_ylabel("클래스 내 표본 비율 (%)")
        ax.legend(fontsize=8); ax.grid(axis="y", alpha=.15)
    fig.suptitle("막대 히스토그램: 오른쪽(>0)은 이상 판정. 양 끝 0.5%를 끝 bin에 합침", y=1.02)
    fig.tight_layout(); return fig


def selected_settings(version="v7"):
    s = read(version, "selection")
    return pd.DataFrame([{"알고리즘": a, "피처 view": r.get("view", "activity/latency 채널"),
                         "max_samples": r["settings"]["samples"] if a == "if" else None,
                         "K": r["settings"]["k"] if a == "kmeans" else None,
                         "회전 블록": r["settings"]["rotations"] if a == "if" else None,
                         "군집 모양 보정": r["settings"]["shape_distance"] if a == "kmeans" else None,
                         "정상 기준 quantile": r["quantile"], "전체 모델 가중치": r["global_weight"],
                         "초기/정상 구간별 threshold": r["phase_aware"], "탐색 오탐 제약 충족": r["within_budget"]}
                        for a, r in s["families"].items()])


def validation_table(version="v7"):
    rows = []
    for name, splits in read(version, "validation_results").items():
        for i, record in enumerate(splits["confirmation"]["pooled"]):
            rows.append({"설정": name, "모델": LABELS[i], "확인 F1 (%)": record["f1"]*100,
                         "확인 정상 오탐 (%)": record["normal_false_positive_rate"]*100})
    return pd.DataFrame(rows).round(2)


def agreement_bars(audit):
    rows = audit["alert_agreement"]
    fig, axes = plt.subplots(1, 2, figsize=(13, 4))
    for ax, truth, title in zip(axes, (False, True), ("정상 행: 막대는 오탐", "이상 행: 막대는 탐지")):
        selected = [r for r in rows if r["truth"] == truth]
        bars = ax.bar([r["agreement"] for r in selected], [r["fraction"]*100 for r in selected], color=["#438AB5", "#4D947D", "#D29D57", "#B2B8C0"])
        ax.bar_label(bars, fmt="%.2f", padding=3); ax.set_title(title); ax.set_ylabel("해당 클래스의 행 비율 (%)")
        ax.set_ylim(0, 105); ax.grid(axis="y", alpha=.2); ax.set_axisbelow(True)
    fig.tight_layout(); return fig


def source_shift_bars():
    from model_research_v5.run import load as v5load
    from model_research_v5.run_matched import load as v7load
    a, b = v5load(), v7load()
    cohorts = [a["original_partitions"]["train"], b["partitions"]["train"],
               pd.concat([d["normal"].loc[d["normal"].validation_split.eq("confirmation")] for d in b["bank"].values()])]
    labels = ["기존 정상 학습", "v7 정상 학습", "독립 확인 정상"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    vals = [[p.phase.eq("startup").mean()*100 for p in cohorts], [p.missing_duration_1m.eq(1).mean()*100 for p in cohorts]]
    for ax, values, title in zip(axes, vals, ("초기 5분 행 비중", "현재 분에 완료 로그 없는 행 비중")):
        bars = ax.bar(labels, values, color=[COLORS[0], COLORS[3], "#4D947D"]); ax.bar_label(bars, fmt="%.1f", padding=3)
        ax.set_ylim(0, 100); ax.set_ylabel("각 집단 내 비율 (%)"); ax.set_title(title); ax.grid(axis="y", alpha=.2); ax.set_axisbelow(True)
    fig.tight_layout(); return fig
