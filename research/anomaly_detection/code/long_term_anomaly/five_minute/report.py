"""Latest frozen model only: ordinary bars, scenario tables and actual alarm margins."""
import argparse
import numpy as np
import matplotlib.pyplot as plt
from ..common import read_json
from . import confirmation, localized
from .serialization import load_portable

TASKS = {"long_latency_shift": "지속 지연", "gradual_degradation": "점진적 악화",
    "sustained_overload": "지속 폭주", "sustained_input_drop": "지속 입력 감소",
    "daily_pattern_change": "일주기 패턴 변화", "volatility_growth": "변동성 증가"}
HOURS = (1, 4, 8, 12, 24)
METRICS = ("accuracy", "precision", "recall", "f1")
COLORS = ("#416c9a", "#38948c", "#d78b42", "#9875aa")


def save(fig, name):
    folder = localized.OUT / "figures"
    folder.mkdir(exist_ok=True)
    fig.tight_layout()
    fig.savefig(folder / name, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main():
    plt.rcParams.update({"font.family": "Malgun Gothic", "font.size": 11, "axes.unicode_minus": False,
                         "axes.spines.top": False, "axes.spines.right": False})
    r = read_json(localized.OUT / "confirmation_results.json")["results"]
    fig, ax = plt.subplots(figsize=(12, 5))
    x = np.arange(5)
    for j, (metric, color) in enumerate(zip(METRICS, COLORS)):
        values = [r[str(h)]["median"][metric] * 100 for h in HOURS]
        bars = ax.bar(x + (j - 1.5) * .19, values, .18, label=metric.capitalize() if metric != "f1" else "F1", color=color)
        ax.bar_label(bars, labels=[f"{v:.1f}" for v in values], padding=3, fontsize=9)
    ax.set_xticks(x, [f"{h}시간" for h in HOURS]);ax.set_ylim(0, 112)
    ax.set_ylabel("6개 시나리오 지표의 중앙값 (%)")
    ax.set_title("고정 모델의 새 확인용 표본 결과")
    ax.legend(frameon=False, ncol=4, loc="upper left")
    save(fig, "median_metrics.png")
    fig, ax = plt.subplots(figsize=(8, 4))
    values = [r[str(h)]["maximum_scenario_fpr"] * 100 for h in HOURS]
    bars = ax.bar([f"{h}시간" for h in HOURS], values, color=COLORS[0], width=.65)
    ax.bar_label(bars, labels=[f"{v:.2f}%" for v in values], padding=4)
    ax.set_ylim(0, max(values) * 1.25);ax.set_ylabel("정상 시간 중 잘못 경보한 비율 (%)")
    ax.set_title("각 구간에서 가장 높았던 시나리오 오탐률")
    save(fig, "maximum_false_positive_rate.png")
    for metric in ("recall", "f1"):
        fig, axes = plt.subplots(2, 3, figsize=(13, 7), sharey=True)
        for ax, (task, title) in zip(axes.flat, TASKS.items()):
            v = [r[str(h)]["scenarios"][task][metric] * 100 for h in HOURS]
            bars = ax.bar([f"{h}h" for h in HOURS], v, color=COLORS[2 if metric == "recall" else 3], width=.65)
            ax.bar_label(bars, labels=[f"{z:.1f}" for z in v], padding=3, fontsize=9)
            ax.set_title(title);ax.set_ylim(0, 110);ax.set_ylabel(f"{metric.upper()} (%)")
        save(fig, f"scenario_{metric}.png")
    d = confirmation.load(True)
    bank = load_portable()
    with np.load(localized.OUT / "confirmation_predictions.npz", allow_pickle=False) as z:
        margins = [z[f"score_h{h}"] - (bank.settings[1]["threshold"] if h == 1 else 0) for h in HOURS]
    finite = np.concatenate([s[np.isfinite(s)] for s in margins])
    lo, hi = min(-1., float(np.quantile(finite, .001))), max(1., float(np.quantile(finite, .99)))
    edges = np.linspace(lo, hi, 29);width = edges[1] - edges[0]
    fig, axes = plt.subplots(2, 3, figsize=(14, 7.5), sharex=True, sharey=True)
    for ax, h, s in zip(axes.flat, HOURS, margins):
        for direction, label, truth, color in ((-.21, "정상 정답", False, COLORS[0]), (.21, "이상 정답", True, COLORS[2])):
            a = s[np.isfinite(s) & (d["y"] == truth)]
            counts, _ = np.histogram(np.clip(a, np.nextafter(lo, hi), np.nextafter(hi, lo)), edges)
            ax.bar(edges[:-1] + width / 2 + direction * width, counts * 100 / len(a), width * .4, label=label, color=color)
        ax.axvline(0, color="#ab3544", ls="--", lw=1.5, label="실제 경보 경계")
        ax.set_title(f"{h}시간 분석");ax.set_xlabel("실제 경계 대비 점수 차이");ax.set_ylabel("정답 집단 내부 비중 (%)")
    axes.flat[0].legend(frameon=False, fontsize=9)
    axes.flat[-1].axis("off")
    axes.flat[-1].text(.02, .9, "0 초과이면 경보\n음수이면 경보하지 않음\n\n범위 밖 값은 양끝 막대에 포함\n각 정답 집단의 막대 합계는 100%\n점수는 장애 확률이 아님", va="top", fontsize=12, transform=axes.flat[-1].transAxes)
    save(fig, "alarm_margin_distribution.png")
    print("Latest native report: five figures", flush=True)


if __name__ == "__main__": argparse.ArgumentParser(description=__doc__).parse_args();main()
