"""Ordinary bars and exact tables for a benchmark-only sensitivity comparison."""
import json
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LABELS = ["IF 전체", "IF Adapter", "IF 앙상블", "K-means 전체", "K-means Adapter", "K-means 앙상블"]
TASKS = {"individual_delay": "개별 지연", "group_delay": "집단 지연", "column_relation": "컬럼 관계",
         "time_burst": "시간대 폭주", "input_stop": "입력 중단", "gradual_degradation": "점진적 악화", "mixed_hard": "혼합 Hard"}
METRICS = ("accuracy", "precision", "recall", "f1", "normal_false_positive_rate")


def read(version):
    return json.loads((ROOT / "reports" / version / "summary.json").read_text(encoding="utf-8"))


def setup():
    plt.rcParams.update({"font.family": "Malgun Gothic", "axes.unicode_minus": False, "font.size": 11})


def overall():
    old, new = read("model_research_v12"), read("benchmark_mild_v1")
    rows = []
    for i, (a, b) in enumerate(zip(old["pooled"], new["pooled"])):
        row = {"모델": LABELS[i]}
        for k in METRICS:
            row["원본 " + k + " (%)"] = a[k] * 100
            row["완화 " + k + " (%)"] = b[k] * 100
            row[k + " 변화 (%p)"] = (b[k] - a[k]) * 100
        rows.append(row)
    return pd.DataFrame(rows).round(2)


def task_table():
    old, new = read("model_research_v12"), read("benchmark_mild_v1")
    rows = []
    for a, b in zip(old["datasets"], new["datasets"]):
        for i, (x, y) in enumerate(zip(a["metrics"], b["metrics"])):
            rows.append({"task": TASKS[a["dataset"]], "모델": LABELS[i],
                "원본 F1 (%)": x["f1"] * 100, "완화 F1 (%)": y["f1"] * 100, "F1 변화 (%p)": (y["f1"] - x["f1"]) * 100,
                "원본 Recall (%)": x["recall"] * 100, "완화 Recall (%)": y["recall"] * 100,
                "원본 정상 오탐 (%)": x["normal_false_positive_rate"] * 100,
                "완화 정상 오탐 (%)": y["normal_false_positive_rate"] * 100})
    return pd.DataFrame(rows).round(2)


def bars():
    setup()
    old, new = read("model_research_v12")["pooled"], read("benchmark_mild_v1")["pooled"]
    fig, axes = plt.subplots(1, 2, figsize=(15, 5))
    x = np.arange(6)
    for ax, key, title in zip(axes, ("f1", "normal_false_positive_rate"), ("동일 모델의 벤치마크별 F1", "정상 구간 오탐률")):
        for offset, records, label, color in ((-.19, old, "원본", "#9DA9B5"), (.19, new, "20% 신호 강화", "#2875AA")):
            b = ax.bar(x + offset, [r[key] * 100 for r in records], .38, label=label, color=color)
            ax.bar_label(b, fmt="%.2f", padding=3, fontsize=9)
        ax.set_xticks(x, LABELS, rotation=20, ha="right")
        ax.set_title(title); ax.set_ylabel("%")
        ax.set_ylim(0, max(r[key] * 100 for r in old + new) * 1.2 + .5)
        ax.legend(); ax.grid(axis="y", alpha=.2); ax.set_axisbelow(True)
    fig.tight_layout()
    return fig


def task_bars():
    setup()
    old, new = read("model_research_v12")["datasets"], read("benchmark_mild_v1")["datasets"]
    fig, axes = plt.subplots(2, 1, figsize=(15, 9))
    x = np.arange(len(old))
    for ax, method, title in zip(axes, (0, 5), ("IF 전체", "K-means 앙상블")):
        for offset, data, label, color in ((-.19, old, "원본", "#9DA9B5"), (.19, new, "20% 신호 강화", "#2875AA")):
            b = ax.bar(x + offset, [d["metrics"][method]["f1"] * 100 for d in data], .38, label=label, color=color)
            ax.bar_label(b, fmt="%.2f", padding=3, fontsize=9)
        ax.set_xticks(x, [TASKS[d["dataset"]] for d in old]); ax.set_title(title + " task별 F1")
        ax.set_ylabel("F1 (%)"); ax.set_ylim(0, 105); ax.legend()
        ax.grid(axis="y", alpha=.2); ax.set_axisbelow(True)
    fig.tight_layout()
    return fig


def denominators():
    rows = []
    for a, b in zip(read("model_research_v12")["datasets"], read("benchmark_mild_v1")["datasets"]):
        x, y = a["metrics"][0], b["metrics"][0]
        rows.append({"task": TASKS[a["dataset"]], "사례 수": b["case_count"],
            "원본 판정 구간": x["n"], "완화 판정 구간": y["n"],
            "원본 이상 구간": x["anomaly"], "완화 이상 구간": y["anomaly"],
            "정상 대조군 공통 구간 예측 동일": b.get("normal_shared_predictions_equal", True)})
    return pd.DataFrame(rows)


def write_summary():
    old, new = read("model_research_v12"), read("benchmark_mild_v1")
    rows = ["# 벤치마크 난이도 소폭 완화 결과", "", "현재 기본 경로 `data/evaluation`에 난이도 완화를 반영했다. 변경 전 데이터는 `reports/benchmark_before_mild_20261014`에 복구·비교 근거로 보관했으며, 별도 활성 평가셋은 없다.", "", "모델과 threshold는 v12 그대로다. 성능 차이는 데이터 주입 강도 변경 효과이며 모델 개선으로 해석하지 않는다.", "",
        "정상 대조군과 혼합 Hard는 원본과 byte-identical. 6개 기본 task는 각각 5,000개, Hard 1,000개 그대로 유지했다.", "",
        "## 동일 모델의 전체 성능", "", "| 모델 | 원본 F1 | 완화 F1 | 변화 (%p) | 원본 Recall | 완화 Recall | 원본 정상 오탐 | 완화 정상 오탐 |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for i, (a, b) in enumerate(zip(old["pooled"], new["pooled"])):
        values = (100 * a["f1"], 100 * b["f1"], 100 * (b["f1"] - a["f1"]), 100 * a["recall"], 100 * b["recall"], 100 * a["normal_false_positive_rate"], 100 * b["normal_false_positive_rate"])
        rows.append("| " + LABELS[i] + " | " + " | ".join(f"{v:.2f}" for v in values) + " |")
    rows += ["", "## task별 F1", "", "| task | IF 전체 원본 | IF 전체 완화 | K-means 앙상블 원본 | K-means 앙상블 완화 |", "|---|---:|---:|---:|---:|"]
    for a, b in zip(old["datasets"], new["datasets"]):
        v = [a["metrics"][0]["f1"], b["metrics"][0]["f1"], a["metrics"][5]["f1"], b["metrics"][5]["f1"]]
        rows.append("| " + TASKS[a["dataset"]] + " | " + " | ".join(f"{100*x:.2f}" for x in v) + " |")
    rows += ["", "## 해석 주의", "", "- 20%는 정상 대비 주입 변화량의 배율이다. 난이도가 정확히 20% 감소하거나 F1이 20% 상승한다는 뜻이 아니다.",
        "- 지연/관계 이상은 정상 소요시간과 주입 소요시간의 차이를 1.2배로 늘린다. 고정 하드 룰 상한에서는 제한된다.",
        "- 점진적 악화는 같은 진행 모양에서 누적 증가량만 1.2배, 실제 상한 여유가 부족하면 제한된다.",
        "- 폭주는 전체 정상 요청이 아니라 기존 추가 실행 수를 1.2배로 늘린다. 정수 올림 효과를 기록한다.",
        "- 중단은 5/10/20분에서 6/12/24분으로 늘어난다. 이상 구간 분모도 늘어나므로 task-macro/사건 지표도 함께 확인한다.",
        "- 정상 대조군이 동일해도, 장애가 끝난 뒤 남는 피처 이력과 관측 입력의 이상 구간 정의 변화로 전체 정상 오탐률은 달라질 수 있다.",
        "- Adapter 경로에는 미지원 Adapter의 전체 fallback이 포함된다. supported-only 결과는 summary.json에 별도로 남긴다.",
        "- 원본 평가를 이미 확인하고 난이도를 변경한 민감도 실험이다. 새 외부 blind/실제 운영 벤치마크가 아니다.",
        "- 피처는 원본 v12 NPZ 평가와 같은 float32 round-trip 정밀도다. 직접 float64 평가의 경계 차이는 진단 폴더에 보존했다.",
        "- 소폭 완화해도 F1이 유지/하락하는 task를 숨기거나 재생성하지 않는다."]
    (ROOT / "reports/benchmark_mild_v1/COMPARISON.md").write_text("\n".join(rows) + "\n", encoding="utf-8")


if __name__ == "__main__":
    write_summary()
    folder = ROOT / "reports/benchmark_mild_v1/figures"
    folder.mkdir(exist_ok=True)
    for name, function in (("overall.png", bars), ("tasks.png", task_bars)):
        figure = function(); figure.savefig(folder / name, dpi=130, bbox_inches="tight"); plt.close(figure)
