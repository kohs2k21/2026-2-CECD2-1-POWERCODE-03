"""Read-only analysis of the frozen evaluation; figures contain measured values.

No target/minimum lines are drawn. The only threshold line is an actual fitted
anomaly decision threshold. Ground-truth shading is report-only, not an input.
"""
import argparse
from pathlib import Path
import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from .common import ROOT, DATA, REPORT, read_json, write_json, sha256
from .benchmark import load_split
from .models import feature_names, case_components

NAMES = {"seasonal_naive": "시간대 기준 · 단순 비교", "stl_window": "STL + 장기 시간창",
         "stl_pelt": "STL + PELT"}
TASKS = {"long_latency_shift": "지속 지연", "gradual_degradation": "점진적 악화",
         "sustained_overload": "지속 폭주", "sustained_input_drop": "지속 입력 감소",
         "daily_pattern_change": "일일 패턴 변화", "volatility_growth": "변동성 증가"}
METRICS = {"accuracy": "Accuracy", "precision": "Precision", "recall": "Recall", "f1": "F1"}
COLORS = ["#446a91", "#32958e", "#de9450", "#9b78af"]


def context():
    plt.rcParams.update({"font.family": "Malgun Gothic", "axes.unicode_minus": False,
                         "font.size": 11, "axes.spines.top": False, "axes.spines.right": False,
                         "figure.dpi": 120})
    r = read_json(REPORT / "test_results.json")
    package = joblib.load(REPORT / "models.joblib")
    assert sha256(REPORT / "models.joblib") == r["model_sha256"]
    return r, package


def save(fig, name):
    folder = REPORT / "figures"
    folder.mkdir(exist_ok=True)
    fig.tight_layout()
    fig.savefig(folder / name, dpi=150, bbox_inches="tight")
    return fig


def source_table():
    a = read_json(REPORT / "source_audit.json")
    f = pd.DataFrame(a["coverage"])
    return f.rename(columns={"period": "수집 폴더", "start": "첫 관측", "end": "마지막 관측",
        "occupied_hours": "로그 있는 시간 수", "span_hours": "달력상 시간 수",
        "occupied_ratio": "로그 있는 시간 비중", "occupied_days": "로그 있는 날짜 수",
        "longest_all_log_hour_run": "전체 로그 최장 연속 시간"})


def source_plot():
    a = read_json(REPORT / "source_audit.json")
    f = pd.DataFrame(a["coverage"])
    fig, ax = plt.subplots(figsize=(10, 4))
    y = f.occupied_ratio * 100
    bars = ax.bar(f.period, y, color=COLORS[0], width=.55)
    ax.bar_label(bars, labels=[f"{v:.1f}%" for v in y], padding=5)
    ax.set_ylim(0, max(y) * 1.25); ax.set_ylabel("달력상 시간 중 로그가 관측된 비중 (%)")
    ax.set_title("긴 수집 기간 ≠ 빈틈없이 관측된 시계열")
    return save(fig, "source_coverage.png")


def composition():
    f = pd.read_csv(DATA / "case_metadata.csv")
    t = f.groupby(["split", "injected"]).size().unstack(fill_value=0).reindex(
        ["train", "calibration", "development", "test"]).rename(columns={False: "정상 사례", True: "이상 주입 사례"})
    return t


def composition_plot():
    t = composition()
    fig, ax = plt.subplots(figsize=(9, 4))
    x = np.arange(len(t))
    for k, col in enumerate(t.columns):
        bars = ax.bar(x + (k - .5) * .34, t[col], width=.32, color=COLORS[k], label=col)
        ax.bar_label(bars, padding=4)
    ax.set_xticks(x, t.index); ax.set_ylabel("독립 시뮬레이션 사례 수"); ax.set_ylim(0, t.to_numpy().max() * 1.2)
    ax.set_title("한 사례: 정상 참고 28일 + 분석 14일"); ax.legend(frameon=False)
    return save(fig, "benchmark_composition.png")


def model_table():
    r, _ = context()
    return pd.DataFrame([{ "모델": NAMES[k], **{label: 100 * v["median"][m] for m, label in METRICS.items()},
        "전체 오탐률": 100 * v["pooled"]["false_positive_rate"],
        "시나리오 최대 오탐률": 100 * v["maximum_scenario_fpr"]} for k, v in r["results"].items()]).round(2)


def model_plot():
    r, _ = context()
    models = list(r["results"])
    fig, axes = plt.subplots(1, 2, figsize=(14, 4.8), gridspec_kw={"width_ratios": [2, 1]})
    x = np.arange(len(models))
    for j, (key, label) in enumerate(METRICS.items()):
        values = [100 * r["results"][k]["median"][key] for k in models]
        bars = axes[0].bar(x + (j - 1.5) * .19, values, width=.18, color=COLORS[j], label=label)
        axes[0].bar_label(bars, labels=[f"{v:.1f}" for v in values], padding=3, fontsize=9)
    axes[0].set_xticks(x, [NAMES[k] for k in models]); axes[0].set_ylim(0, 112)
    axes[0].set_ylabel("시나리오별 지표의 중앙값 (%)"); axes[0].legend(ncol=4, frameon=False, loc="upper center")
    axes[0].set_title("최종 평가: 실제 측정값")
    values = [100 * r["results"][k]["maximum_scenario_fpr"] for k in models]
    bars = axes[1].bar(x, values, color=COLORS[2], width=.5)
    axes[1].bar_label(bars, labels=[f"{v:.2f}%" for v in values], padding=4)
    axes[1].set_xticks(x, [NAMES[k] for k in models], rotation=15)
    axes[1].set_ylim(0, max(max(values) * 1.3, 1)); axes[1].set_ylabel("정상 시간 중 오경보 비중 (%)")
    axes[1].set_title("시나리오별 오탐률 중 최댓값")
    return save(fig, "model_metrics.png")


def scenario_table():
    r, _ = context()
    return pd.DataFrame([{ "시나리오": TASKS[k], **{label: v[m] * 100 for m, label in METRICS.items()},
                          "오탐률": 100 * v["false_positive_rate"], "탐지 시간": v["tp"],
                          "놓친 시간": v["fn"], "오경보 시간": v["fp"]}
                        for k, v in r["results"][r["primary"]]["scenarios"].items()]).round(2)


def scenario_plot():
    r, _ = context()
    rows = r["results"][r["primary"]]["scenarios"]
    fig, axes = plt.subplots(2, 2, figsize=(13, 8))
    for ax, (metric, label), color in zip(axes.flat, METRICS.items(), COLORS):
        values = [100 * v[metric] for v in rows.values()]
        bars = ax.bar([TASKS[k] for k in rows], values, color=color, width=.65)
        ax.bar_label(bars, labels=[f"{v:.1f}%" for v in values], padding=4, fontsize=10)
        ax.set_ylim(0, 112); ax.set_ylabel("%")
        ax.set_title(label); ax.tick_params(axis="x", rotation=20)
    return save(fig, "scenario_metrics.png")


def score_histogram():
    r, package = context(); method = r["primary"]
    with np.load(REPORT / f"test_scores_{method}.npz") as z:
        scores = z["score"]
    y = load_split("test", with_truth=True)["y"]
    normal = scores[~y & np.isfinite(scores)]; abnormal = scores[y & np.isfinite(scores)]
    upper = float(np.quantile(np.r_[normal, abnormal], .995)); lower = min(float(np.min(scores[np.isfinite(scores)])), -1)
    bins = np.linspace(lower, upper, 61)
    fig, ax = plt.subplots(figsize=(11, 4.5))
    for values, color, name in ((normal, COLORS[0], "정상"), (abnormal, COLORS[2], "이상")):
        ax.hist(values, bins=bins, weights=np.full(len(values), 100 / len(values)), alpha=.65,
                color=color, label=f"{name} ({len(values):,}시간)")
    threshold = package["models"][method].threshold
    ax.axvline(threshold, color="#a1333a", ls="--", lw=1.8, label=f"실제 탐지 기준 {threshold:.3f}")
    tails = int((scores[np.isfinite(scores)] > upper).sum())
    ax.set_xlabel("이상 점수: 클수록 정상 참고 패턴과 다름"); ax.set_ylabel("각 집단 전체 시간 대비 비중 (%)")
    ax.set_title(f"{NAMES[method]} 점수 분포 · 오른쪽 극단 {tails:,}시간은 그림 범위 밖 (평가에는 포함)")
    ax.legend(frameon=False)
    return save(fig, "score_distribution.png")


def timeline_examples():
    r, package = context(); model = package["models"][r["primary"]]
    data = load_split("test", with_truth=True)
    meta = pd.read_csv(DATA / "case_metadata.csv").set_index("case_id").loc[data["case_id"]]
    figures = []
    for task in TASKS:
        # First injected case in file order, never the best-looking success.
        index = int(np.flatnonzero(meta.scenario.eq(task) & meta.injected)[0])
        x, observed, available = data["X"][index], data["collection_observed"][index], data["latency_available"][index]
        prediction = model.predict(x, observed, available)
        channel = 1 if task in ("long_latency_shift", "gradual_degradation", "volatility_growth") else 0
        c = model.setting.context_hours; time = np.arange(len(x) - c) / 24
        unit = "대표 소요시간 (초, 로그평균 역변환)" if channel == 1 else "시간당 Process 실행 수"
        fig, axes = plt.subplots(2, 1, figsize=(12, 5.2), sharex=True, height_ratios=[1.2, 1])
        physical = np.expm1(x[c:, channel]) / (1000 if channel == 1 else 1)
        axes[0].plot(time, physical, lw=1, color=COLORS[0], label="관측값")
        forecast = prediction["details"]["forecast"][:, channel].copy()
        if channel == 1:
            detail = prediction["details"]
            forecast += detail["workload_beta"] * (x[c:, 2] - detail["work_center"])
        physical_forecast = np.expm1(forecast) / (1000 if channel == 1 else 1)
        axes[0].plot(time, physical_forecast, lw=1.5, color=COLORS[1], label="28일 정상 참고 구간에서 구한 기준")
        axes[0].set_ylabel(unit); axes[0].legend(frameon=False, loc="upper left")
        axes[0].set_title(f"{TASKS[task]} · 첫 번째 주입 사례 ({data['case_id'][index]})")
        axes[1].plot(time, prediction["score"], color=COLORS[0], lw=1, label="실제 이상 점수")
        axes[1].axhline(model.threshold, color="#a1333a", ls="--", label=f"실제 탐지 기준 {model.threshold:.3f}")
        for ax in axes:
            ax.fill_between(time, 0, 1, where=data["y"][index], transform=ax.get_xaxis_transform(),
                            color=COLORS[2], alpha=.18, label="주입한 이상 구간 (정답)")
            ax.fill_between(time, 0, 1, where=~observed[c:], transform=ax.get_xaxis_transform(),
                            color="grey", alpha=.3)
        axes[1].set_ylabel("이상 점수"); axes[1].set_xlabel("분석 구간 시작 후 경과일 (앞선 28일은 정상 참고 구간)")
        axes[1].legend(frameon=False, loc="upper left")
        figures.append(save(fig, f"timeline_{task}.png"))
    return figures


def reasons_table():
    r, package = context(); model = package["models"][r["primary"]]
    d = load_split("test", with_truth=True)
    with np.load(REPORT / f"test_scores_{r['primary']}.npz", allow_pickle=False) as saved:
        scores, reasons = saved["score"], saved["reason"]
    rows = []
    for i in range(len(d["X"])):
        score, reason = scores[i], reasons[i]
        alarms = np.isfinite(score) & (score > model.threshold)
        for kind, mask in (("정탐 TP", alarms & d["y"][i]), ("오탐 FP", alarms & ~d["y"][i])):
            unique, counts = np.unique(reason[mask], return_counts=True)
            rows.extend({"판정": kind, "주요 점수 근거": str(a), "시간 수": int(n)} for a, n in zip(unique, counts))
    out = pd.DataFrame(rows).groupby(["판정", "주요 점수 근거"], as_index=False)["시간 수"].sum()
    out.to_csv(REPORT / "detection_reasons.csv", index=False, encoding="utf-8-sig")
    return out.sort_values(["판정", "시간 수"], ascending=[True, False])


def reason_plot(table):
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.5))
    def readable(name):
        block, channel = name.split(":")
        scope = "구간" if block.startswith("segment") else "시간창"
        value = {"duration": "소요시간", "requests": "실행 수", "items_per_process": "처리 건수"}[channel]
        kind = "편차 크기" if "energy" in block else "평균 변화"
        return f"{scope} {value} {kind}"
    for ax, kind, color in zip(axes, ("정탐 TP", "오탐 FP"), COLORS):
        part = table.loc[table["판정"].eq(kind)].nlargest(5, "시간 수").iloc[::-1]
        bars = ax.barh(part["주요 점수 근거"].map(readable), part["시간 수"], color=color)
        ax.bar_label(bars, padding=4, fmt="%.0f")
        ax.set_title(kind + " · 가장 크게 기여한 점수 항목"); ax.set_xlabel("시간 수")
        ax.set_xlim(0, part["시간 수"].max() * 1.25)
    return save(fig, "detection_reasons.png")


def uncertainty(repeats=400):
    """Pair-cluster bootstrap, not independent-hour resampling."""
    r, package = context(); method = r["primary"]
    d = load_split("test", with_truth=True)
    meta = pd.read_csv(DATA / "case_metadata.csv").set_index("case_id").loc[d["case_id"]]
    with np.load(REPORT / f"test_scores_{method}.npz") as z:
        scores = z["score"]
    threshold = package["models"][method].threshold
    counts = []
    for i in range(len(scores)):
        valid = np.isfinite(scores[i]); y = d["y"][i, valid]; p = scores[i, valid] > threshold
        counts.append([int((~y & ~p).sum()), int((~y & p).sum()), int((y & ~p).sum()), int((y & p).sum())])
    counts = np.asarray(counts)
    def values(a):
        tn, fp, fn, tp = a.T
        return np.column_stack([(tn + tp) / (tn + fp + fn + tp),
                                tp / np.maximum(tp + fp, 1), tp / np.maximum(tp + fn, 1),
                                2 * tp / np.maximum(2 * tp + fp + fn, 1)])
    rng = np.random.default_rng(838386)
    medians = []
    for _ in range(repeats):
        by_scenario = []
        for task in TASKS:
            ids = np.flatnonzero(meta.scenario.eq(task))
            pairs = [ids[j:j + 2] for j in range(0, len(ids), 2)]
            selected = np.concatenate([pairs[i] for i in rng.integers(len(pairs), size=len(pairs))])
            by_scenario.append(counts[selected].sum(axis=0))
        medians.append(np.median(values(np.asarray(by_scenario)), axis=0))
    ci = np.quantile(np.asarray(medians), [.025, .975], axis=0)
    return pd.DataFrame({"지표": list(METRICS.values()),
                         "측정 중앙값 (%)": [100 * r["results"][method]["median"][k] for k in METRICS],
                         "쌍 단위 bootstrap 하한 (%)": ci[0] * 100,
                         "쌍 단위 bootstrap 상한 (%)": ci[1] * 100}).round(2)


def summary_markdown():
    r, package = context(); method = r["primary"]; result = r["results"][method]
    lines = ["# 장기 시계열 이상탐지 — 최종 평가", "", f"선정 모델: **{NAMES[method]}**", "",
        "평가 단위는 한 시간이다. 28일 정상 참고 구간으로 기준을 추정하고, 뒤 14일을 오프라인으로 판정한다.",
        "표의 중앙값은 여섯 장기 시나리오별 지표의 중앙값이다. 전체 시간 통합 지표와 다르다.", "",
        "| Accuracy | Precision | Recall | F1 | 전체 오탐률 | 최대 시나리오 오탐률 |",
        "|---:|---:|---:|---:|---:|---:|",
        "| " + " | ".join(f"{100 * result['median'][k]:.2f}%" for k in METRICS)
        + f" | {100 * result['pooled']['false_positive_rate']:.2f}% | {100 * result['maximum_scenario_fpr']:.2f}% |", "",
        f"사용자 조건 충족: **{'예' if result['target_met'] else '아니오'}**", "",
        "중앙값 조건을 충족했다는 뜻이지 모든 시나리오가 같은 성능이라는 뜻은 아니다.", "",
        "## 전체 시간 통합 지표", "",
        "| Accuracy | Precision | Recall | F1 |", "|---:|---:|---:|---:|",
        "| " + " | ".join(f"{100 * result['pooled'][k]:.2f}%" for k in METRICS) + " |", "",
        f"특히 전체 통합 Recall은 {100 * result['pooled']['recall']:.2f}%로 시나리오별 중앙값과 다르다. 이상 기간이 긴 시나리오는 전체 통합 계산에서 더 큰 비중을 가진다.", "",
        "## 시나리오별 실제 성능", "",
        "| 시나리오 | Accuracy | Precision | Recall | F1 | 오탐률 |",
        "|---|---:|---:|---:|---:|---:|",
        *["| " + TASKS[k] + " | " + " | ".join(f"{100 * v[m]:.2f}%" for m in (*METRICS, "false_positive_rate")) + " |"
          for k, v in result["scenarios"].items()], "",
        "지속 입력 감소·폭주·수준 지연·변동성 증가에서 상대적으로 강하며, 일일 패턴 변화에는 약하다. 점진적 악화 초반의 약한 변화도 미탐으로 계산했다. 장애 원인 분류 모델은 아니다.", "",
        "## 현재 데이터에 대한 근거", "",
        "- 세 기간 총 3,714,199개 Process 행의 시간·상태 점검; 기간 공백은 입력 중단으로 취급하지 않음",
        "- 개별 Adapter+Process의 최장 연속 정상 후보 관측 159시간; 7일 연속 관측 집단 없음",
        "- 주·월 단위 신경망 학습·실제 장애 검증을 주장할 근거 부족; 통계 기반 STL과 오프라인 PELT 우선 적용",
        "- STL은 정상 참고 구간에만 적합; 이상 구간까지 재적합해 점진 악화를 정상 추세로 흡수하는 문제 방지", "",
        "## 입력 컬럼의 정확한 의미", "",
        "- `log_requests`: log(1 + 시간당 정상 후보 Process 실행 행 수). Transaction 요청 건수가 아님",
        "- `log_duration_ms`: 시간당 Process `log(1+endTime-startTime(ms))`의 평균. 산술평균 소요시간의 로그와 다름",
        "- `log_items_per_process`: 시간당 Process `log(1+totalCount)`의 평균. 소요시간을 처리 건수 조건에 맞춰 비교하기 위한 공변량",
        "- `collection_observed`: 수집 상태가 알려진 시간인지 여부. False는 0건이 아니라 평가 제외",
        "- `latency_available`: 실행이 있어 소요시간을 비교할 수 있는지 여부", "",
        "## 합성 평가의 범위", "",
        "- ADBench 공식 장기 시계열 데이터가 아닌 직접 작성한 반합성 데이터셋",
        "- 원본 초기 구간의 정상 후보 7,894개 시간×집단 집계, 82개 집단에서 분포·단기 상관 추정",
        "- 참고 집단은 DB.READER 40개·DB.WRITER 40개·FILE.READER/FILE.WRITER 각 1개; 다른 Adapter 일반화는 미검증",
        "- 시간당 연속성·장기 장애 구간은 시뮬레이션 가정; 원본에 6주 연속 이력이 있다는 뜻이 아님",
        "- 학습 64개 정상 사례, 정상 보정 48개, 개발 288개, 최종 평가 576개; 각 사례 1,008시간",
        "- 여섯 시나리오별 최종 평가: 이상 주입 48개 + 짝지은 정상 48개",
        "- 평가 데이터는 점수 확인 전에 해시 고정; 최종 정답 개봉 전 모델·기준·점수 해시 저장",
        "- 정상 후보 S도 진짜 정상 보장은 없음. 모든 생성 파티션은 같은 생성 가정을 공유하므로 실제 장애 성능 보장 불가",
        "- 시간별 원래 정답 그대로 평가. 한 시간 적중으로 이상 구간 전체를 정탐 처리하는 point adjustment 미사용", "",
        "## 운영 연결 시 주의", "",
        "- 기존 IF·K-means·LOF 및 원본/기존 평가셋 수정 없음; 이 모델은 별도 배치 분석용",
        "- API는 `long_term_anomaly.inference.score_frame`; 신뢰된 `models.joblib`만 역직렬화",
        "- 같은 Adapter+Process의 시간순 집계와 검토된 정상 참고 28일 필요. S만으로 참고 구간을 자동 확정하면 안 됨",
        "- 최대 6시간 단기 결측은 중립 잔차/보간; 참고 구간 긴 결측은 중단, 분석 구간 긴 수집 결측은 앞뒤 분리. 수집 건강 정보 없이는 0건의 원인을 단정하지 않음",
        "- PELT와 중심 시간창은 분석 구간 내 뒤쪽 시간도 사용. 실시간 조기 탐지 성능으로 해석하지 않음", "",
        "## 근거 자료", "",
        "- [STL 공식 구현과 원 논문 안내](https://www.statsmodels.org/stable/generated/statsmodels.tsa.seasonal.STL.html): 정상 시간대 패턴·수준 분해에 적용",
        "- [Killick et al., Optimal detection of changepoints with a linear computational cost](https://arxiv.org/abs/1101.1438): PELT 변화점 최적 분할에 적용; 경보 기준은 별도 정상 보정",
        "- [ADBench (NeurIPS 2022) 공식 저장소](https://github.com/Minqi824/ADBench): 표 형식 생성기와 임의 분할을 장기 시계열 검증으로 오인하지 않기 위해 이번 생성에는 직접 사용하지 않음",
        "- [Kim et al., Towards a Rigorous Evaluation of Time-series Anomaly Detection](https://arxiv.org/abs/2109.05257): point adjustment 없이 시간별 평가 적용",
        "- [DLinear 공식 저장소](https://github.com/cure-lab/LTSF-Linear), [PatchTST](https://arxiv.org/abs/2211.14730): 연속 장기 이력 부족으로 이번 학습 후보에서 보류", ""]
    (REPORT / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    context()  # Set Korean fonts before the first source/composition figure.
    figures = [source_plot(), composition_plot(), model_plot(), scenario_plot(), score_histogram()]
    figures.extend(timeline_examples())
    reasons = reasons_table(); figures.append(reason_plot(reasons))
    uncertainty().to_csv(REPORT / "median_uncertainty.csv", index=False, encoding="utf-8-sig")
    summary_markdown()
    for figure in figures:
        plt.close(figure)
    print("Report figures and summary written", len(figures), flush=True)


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    main()
