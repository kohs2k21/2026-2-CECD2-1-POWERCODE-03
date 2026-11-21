"""Latest nine-strategy results, keeping model and difficulty effects distinct."""
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from model_research_v14 import report as previous
from model_research_v13.data import read

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT/"reports/model_research_v18"
previous.OUTPUT = OUTPUT
LABELS, TASKS = previous.LABELS, previous.TASKS
setup, table, bars = previous.setup, previous.table, previous.bars
scenario_bars, score_histograms, event_table = previous.scenario_bars, previous.score_histograms, previous.event_table
summary, location = previous.summary, previous.location


def stage_summary(stage): return read(ROOT/read(OUTPUT/f"{stage}_evaluation_index.json")["current"] / "summary.json")


def effects():
    before = read(ROOT/"reports/model_research_v14/evaluation_7f2dc4351af61664/summary.json")["pooled"]
    parent, adjusted = stage_summary("parent")["pooled"], stage_summary("adjusted")["pooled"]
    rows = []
    for i, (old, same, new) in enumerate(zip(before, parent, adjusted)):
        rows.append({"모델": LABELS[i], "기존 F1 (%)": old["f1"]*100, "모델만 변경 F1 (%)": same["f1"]*100,
                     "모델 개선 F1 (%p)": (same["f1"]-old["f1"])*100,
                     "난도 조정 후 F1 (%)": new["f1"]*100, "난도 효과 F1 (%p)": (new["f1"]-same["f1"])*100,
                     "기존 Precision (%)": old["precision"]*100, "모델만 변경 Precision (%)": same["precision"]*100,
                     "기존 오탐 (%)": old["normal_false_positive_rate"]*100,
                     "모델만 변경 오탐 (%)": same["normal_false_positive_rate"]*100})
    return pd.DataFrame(rows).round(2)


def metric_changes():
    before = read(ROOT/"reports/model_research_v14/evaluation_7f2dc4351af61664/summary.json")["pooled"]
    after = stage_summary("parent")["pooled"]
    return pd.DataFrame([{"모델": LABELS[i], **{name+" 변화 (%p)": 100*(b[key]-a[key])
        for name,key in (("Accuracy","accuracy"),("Precision","precision"),("Recall","recall"),
                         ("F1","f1"),("정상 오탐","normal_false_positive_rate"))}}
        for i,(a,b) in enumerate(zip(before,after))]).round(2)


def macro_effects():
    before = read(ROOT/"reports/model_research_v14/evaluation_7f2dc4351af61664/summary.json")["basic_macro"]
    after = stage_summary("parent")["basic_macro"]
    return pd.DataFrame([{"모델": LABELS[i], "기존 macro-F1 (%)": a["f1"]*100,
                         "모델만 변경 macro-F1 (%)": b["f1"]*100,
                         "모델 변화 (%p)": 100*(b["f1"]-a["f1"])}
                         for i,(a,b) in enumerate(zip(before,after))]).round(2)


def input_histograms():
    frame = joblib.load(location()/"analysis_samples.joblib")
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))
    for ax, column, title in zip(axes.flat,
        ("native_execution_arrivals", "log_max_duration_1m", "prefix_duration_z", "duration_trend_z"),
        ("1분 실행 수 — log(1+x)", "완료 소요시간 최대값 — log(1+ms)", "초반 관측 대비 소요시간 변화", "최근 관측 소요시간 추세")):
        values = frame[column].to_numpy(float)
        if column == "native_execution_arrivals": values = np.log1p(values)
        valid = np.isfinite(values)
        if column == "log_max_duration_1m": valid &= frame.latency_assessed.to_numpy(bool)
        elif column in ("prefix_duration_z", "duration_trend_z"): valid &= frame.temporal_assessed.to_numpy(bool)
        low, high = np.quantile(values[valid], [.005, .995]); high = max(high, low+.01)
        bins = np.linspace(low, high, 32)
        samples = [np.clip(values[valid & frame.is_anomaly.eq(a).to_numpy()], low, high) for a in (False, True)]
        ax.hist(samples, bins=bins, weights=[np.ones(len(v))*100/len(v) for v in samples], histtype="bar",
                color=["#3479A8", "#D67854"], alpha=.9, label=["정상", "주입 이상"])
        ax.set_title(f"{title}\n정상 n={len(samples[0]):,} · 주입 이상 n={len(samples[1]):,}", fontsize=11)
        ax.set_ylabel("각 집단 내 비중 (%)")
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", bbox_to_anchor=(.5, .005), ncol=2, frameon=False)
    fig.tight_layout(rect=(0, .04, 1, 1)); return fig


def thresholds():
    records = []
    for model in read(OUTPUT/"handoff/manifest.json")["models"]:
        for row in model["thresholds"]:
            records.append({"알고리즘": model["algorithm"], **row})
    frame = pd.DataFrame(records)
    return frame.rename(columns={"adapter": "Adapter", "phase": "단계", "evidence_gate": "관측 gate",
        "normal_quantile": "정상 분위수", "threshold": "결합 점수 경계", "global_weight": "전체 가중치",
        "independent_calibration_cases": "독립 정상 사례 수"})[
        ["알고리즘", "Adapter", "단계", "관측 gate", "정상 분위수", "결합 점수 경계", "전체 가중치", "독립 정상 사례 수"]].round(5)


def source_thresholds():
    rows = []
    for model in read(OUTPUT/"handoff/manifest.json")["models"]:
        for route, key in (("전체", "global_thresholds"), ("Adapter", "adapter_thresholds")):
            for r in model[key]:
                if route == "전체" and r["adapter"] != "GLOBAL": continue
                rows.append({"알고리즘": model["algorithm"], "경로": route,
                    "Adapter 문맥": r["adapter"], "단계": r["phase"], "관측 gate": r["evidence_gate"],
                    "정상 분위수": r["quantile"], "기존 margin의 보정 경계": r["threshold_on_base_margin"],
                    "보정 정상 분": r["normal_reference_windows"],
                    "보정 alpha": r["calibration_shrinkage"]})
    return pd.DataFrame(rows).round(5)


def task_metrics():
    return pd.DataFrame([{"시나리오": TASKS[d["dataset"]], "모델": LABELS[i],
        **{k: r[k]*100 for k in ("precision", "recall", "f1", "normal_false_positive_rate")}}
        for d in summary()["datasets"] for i, r in enumerate(d["metrics"])]).round(2)
