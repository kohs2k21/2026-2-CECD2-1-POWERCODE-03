"""Measured-value bar charts for the horizon ablation; no target lines."""
from pathlib import Path
import argparse
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from ..common import DATA, read_json, sha256
from ..benchmark import load_split
from ..experiment import metadata_for_split
from ..report import TASKS
from .model import HORIZONS, load_bank
from .run import OUT

METRICS = ("accuracy", "precision", "recall", "f1")
LABELS = ("Accuracy", "Precision", "Recall", "F1")
COLORS = ("#446a91", "#32958e", "#de9450", "#9b78af")


def context():
    plt.rcParams.update({"font.family": "Malgun Gothic", "axes.unicode_minus": False,
                         "font.size": 11, "axes.spines.top": False, "axes.spines.right": False})
    result = read_json(OUT / "results.json")
    assert sha256(OUT / "models.joblib") == result["model_sha256"]
    return result, load_bank(OUT / "models.joblib")


def table(mode="median"):
    r, _ = context()
    rows = []
    for h in HORIZONS:
        a = r["results"][str(h)]
        rows.append({"한 번에 분석하는 시간": h,
            **{label: a[mode][key] * 100 for key, label in zip(METRICS, LABELS)},
            "전체 오탐률": 100 * a["pooled"]["false_positive_rate"],
            "시나리오 최대 오탐률": 100 * a["maximum_scenario_fpr"]})
    return pd.DataFrame(rows).round(2)


def save(fig, name):
    (OUT / "figures").mkdir(exist_ok=True)
    fig.tight_layout()
    fig.savefig(OUT / "figures" / name, dpi=150, bbox_inches="tight")
    return fig


def metric_plot(mode="median"):
    r, _ = context()
    fig, ax = plt.subplots(figsize=(12, 4.8))
    x = np.arange(len(HORIZONS))
    for j, (metric, name, color) in enumerate(zip(METRICS, LABELS, COLORS)):
        values = [100 * r["results"][str(h)][mode][metric] for h in HORIZONS]
        bars = ax.bar(x + (j - 1.5) * .19, values, width=.18, label=name, color=color)
        ax.bar_label(bars, labels=[f"{v:.1f}" for v in values], padding=3, fontsize=9)
    ax.set_xticks(x, [f"{h}시간" for h in HORIZONS]); ax.set_ylim(0, 112)
    ax.set_xlabel("한 번에 판정하는 구간 길이 (입력 집계 간격은 모두 1시간)")
    ax.set_ylabel("시나리오별 지표 중앙값 (%)" if mode == "median" else "전체 시간 통합 지표 (%)")
    ax.set_title("분석 구간 길이별 실제 성능")
    ax.legend(ncol=4, frameon=False, loc="upper left")
    return save(fig, f"metrics_{mode}.png")


def fpr_plot():
    r, _ = context()
    fig, ax = plt.subplots(figsize=(10, 4))
    x = np.arange(len(HORIZONS))
    maximum = 0
    for offset, key, name, color in ((-.18, "pooled", "전체 오탐률", COLORS[0]),
                                    (.18, "maximum", "시나리오 최대 오탐률", COLORS[2])):
        values = [100 * (r["results"][str(h)]["maximum_scenario_fpr"] if key == "maximum"
                        else r["results"][str(h)]["pooled"]["false_positive_rate"]) for h in HORIZONS]
        bars = ax.bar(x + offset, values, width=.34, label=name, color=color)
        ax.bar_label(bars, labels=[f"{v:.2f}%" for v in values], padding=4)
        maximum = max(maximum, max(values))
    ax.set_xticks(x, [f"{h}시간" for h in HORIZONS]); ax.set_ylim(0, maximum * 1.3)
    ax.set_ylabel("정상 시간 중 잘못 경보한 비중 (%)")
    ax.set_title("각 길이에서 따로 보정한 경보 기준의 오탐률")
    ax.legend(frameon=False, loc="upper left")
    return save(fig, "false_positive_rates.png")


def scenario_plot(metric="f1"):
    r, _ = context()
    fig, axes = plt.subplots(2, 3, figsize=(14, 7.5), sharey=True)
    for ax, task in zip(axes.flat, TASKS):
        values = [100 * r["results"][str(h)]["scenarios"][task][metric] for h in HORIZONS]
        bars = ax.bar([f"{h}h" for h in HORIZONS], values, width=.65, color=COLORS[3 if metric == "f1" else 2])
        ax.bar_label(bars, labels=[f"{v:.1f}" for v in values], padding=4, fontsize=10)
        ax.set_ylim(0, 112); ax.set_title(TASKS[task]); ax.set_ylabel(f"{metric.upper()} (%)")
    return save(fig, f"scenario_{metric}.png")


def timeline(task="gradual_degradation"):
    _, bank = context()
    d = load_split("test", with_truth=True)
    meta = metadata_for_split("test", d["case_id"])
    i = int(np.flatnonzero(meta.scenario.eq(task) & meta.injected)[0])
    fig, axes = plt.subplots(5, 1, figsize=(12, 9), sharex=True, sharey=True)
    t = np.arange(336) / 24
    for ax, h in zip(axes, HORIZONS):
        with np.load(OUT / f"scores_{h}h.npz") as f:
            s = f["score"][i] / bank.models[h].threshold
        ax.plot(t, s, color=COLORS[0], lw=1)
        ax.axhline(1, color="#ad3c46", ls="--", label="실제 경보 기준 = 점수/기준 1")
        ax.fill_between(t, 0, 1, where=d["y"][i], transform=ax.get_xaxis_transform(), color=COLORS[2], alpha=.18)
        ax.set_ylabel(f"{h}시간\n점수/기준")
    axes[0].set_title(f"{TASKS[task]}: 동일한 첫 주입 사례를 길이별로 비교 ({d['case_id'][i]})")
    axes[0].legend(frameon=False, loc="upper left")
    axes[-1].set_xlabel("분석 기간 시작 후 경과일 · 각 배치 점수는 그 배치가 끝난 뒤 제공")
    return save(fig, f"timeline_{task}.png")


def summary():
    r, bank = context()
    header = ["# 분석 구간 길이 선택 결과", "",
        "정상 참고 28일과 입력 집계 간격 1시간은 유지하고, 한 번에 보는 구간만 바꿨다.",
        "길이별 정상 점수 통계·경보 기준은 따로 보정했다. 기존 14일 모델·평가셋은 변경하지 않았다.", "",
        "## 시나리오별 지표 중앙값", "",
        "| 분석 구간 | Accuracy | Precision | Recall | F1 | 전체 오탐률 | 최대 시나리오 오탐률 |",
        "|---:|---:|---:|---:|---:|---:|---:|"]
    for h in HORIZONS:
        a = r["results"][str(h)]
        header.append(f"| {h}시간 | " + " | ".join(f"{100*a['median'][k]:.2f}%" for k in METRICS)
                      + f" | {100*a['pooled']['false_positive_rate']:.2f}% | {100*a['maximum_scenario_fpr']:.2f}% |")
    header += ["", "## 전체 시간 통합 지표", "",
        "| 분석 구간 | Accuracy | Precision | Recall | F1 |", "|---:|---:|---:|---:|---:|"]
    for h in HORIZONS:
        a = r["results"][str(h)]
        header.append(f"| {h}시간 | " + " | ".join(f"{100*a['pooled'][k]:.2f}%" for k in METRICS) + " |")
    best = max(HORIZONS, key=lambda h: r["results"][str(h)]["median"]["f1"])
    header += ["", f"이번 동일 데이터 비교에서 F1 중앙값이 가장 높은 길이는 {best}시간이다. 모든 운영 상황의 최적 길이라는 뜻은 아니다.", "",
        "- 1시간 입력은 1행이다. 시간 내부 추세를 학습·판단한 결과가 아니다.",
        "- 1~12시간에는 PELT 12시간 최소 구간 때문에 변화점 분할이 불가능하다. 정상 기준과 배치의 수준·흔들림을 비교한다.",
        "- 긴 배치는 지속성을 더 볼 수 있지만, 그 길이만큼 데이터가 모여야 결과를 낼 수 있다.",
        "- 다음 배치 데이터로 앞선 배치를 다시 계산하지 않는다. 일주기 기준의 시각 정렬도 유지한다.",
        "- 경보 기준을 길이마다 재보정했으므로 14일용 기준을 그대로 입력 길이만 바꿔 쓴 실험이 아니다.",
        "- 기존에 공개한 반합성 장기 평가셋을 재사용한 비교다. 새로운 독립 블라인드 평가나 실제 운영 성능으로 주장하지 않는다.",
        "- 시나리오는 3~7일 지속되는 변화 중심이다. 짧은 장애 성능이나 분 단위 추세 성능은 별도 검증이 필요하다.", "",
        "## 입력 예", "", "```python", "from long_term_anomaly.intervals import load_bank",
        "bank = load_bank('reports/long_term_anomaly/intervals/models.joblib')",
        "reference = bank.prepare_reference(normal_history_28days)",
        "result = bank.predict_batch(reference, current_4hours, analysis_hours=4)", "```", "",
        "입력 계약·CSV 명령은 code/long_term_anomaly/intervals/README.md에 있다.", ""]
    met = [h for h in HORIZONS if r["results"][str(h)]["target_met"]]
    header += ["## 기존 수치 목표와의 관계", "",
        (f"네 지표 중앙값 각각 70% 이상·오탐률 6% 이하를 모두 충족한 분석 길이: {met}시간."
         if met else "이번 다섯 길이 중 네 지표 중앙값 각각 70% 이상·오탐률 6% 이하를 모두 충족한 것은 없다. 기능 구현·길이 비교 완료와 성능 목표 달성은 별개다."), ""]
    (OUT / "summary.md").write_text("\n".join(header), encoding="utf-8")


def main():
    figures = [metric_plot(), metric_plot("pooled"), fpr_plot(), scenario_plot(), scenario_plot("recall"),
               timeline(), timeline("daily_pattern_change")]
    for f in figures: plt.close(f)
    summary()
    print("Interval comparison: 7 figures and summary written", flush=True)


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    main()
