"""Plain bar charts, thresholds, readable executed notebook and audit report."""
import json
from pathlib import Path

import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import nbformat
import numpy as np
import pandas as pd

from adapter_feature_ablation.features import DESCRIPTIONS
from adapter_feature_ablation.run import OUTPUT, ROOT, MAIN_SCENARIOS
from model_research_v13.data import read, write
from model_evaluation.data import sha256

NAMES = {"if": "Isolation Forest", "kmeans": "MiniBatch K-means", "lof": "LOF"}
SCENARIOS = {"individual_delay": "개별 지연", "group_delay": "집단 지연", "column_relation": "컬럼 관계",
             "time_burst": "시간대 폭주", "input_stop": "입력 중단", "gradual_degradation": "점진적 악화", "mixed_hard": "Hard"}


def markdown_table(rows, columns):
    lines = ["| " + " | ".join(label for _, label in columns) + " |",
             "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row[key]) if key in ("method", "algorithm", "view", "scenario")
            else f"{row[key]*100:.2f}" for key, _ in columns) + " |")
    return "\n".join(lines)


def build():
    summary, frozen = read(OUTPUT/"summary.json"), read(OUTPUT/"frozen.json")
    # A global reference fallback can also mean insufficient support, not a new ID.
    if "unseen_adapter_reference_fallback" in summary:
        summary["global_adapter_reference_fallback"] = summary.pop("unseen_adapter_reference_fallback")
        write(OUTPUT/"summary.json", summary)
    figures = OUTPUT/"figures"; figures.mkdir(exist_ok=True)
    plt.rcParams.update({"font.family": "Malgun Gothic", "axes.unicode_minus": False,
                         "font.size": 11, "axes.spines.top": False, "axes.spines.right": False})
    rows = summary["pooled"]
    frame = pd.DataFrame(rows).set_index("method")
    fig, axes = plt.subplots(1, 4, figsize=(16, 4.8))
    for ax, metric, title in zip(axes, ("accuracy", "precision", "recall", "f1"), ("Accuracy", "Precision", "Recall", "F1")):
        for j, view in enumerate(("baseline", "adapter_features")):
            values = [frame.loc[a+"_"+view, metric]*100 for a in NAMES]
            bars = ax.bar(np.arange(3)+(j-.5)*.34, values, width=.32,
                          color=("#94a3b8", "#2563eb")[j], label=("기존 피처", "Adapter 피처 추가")[j])
            ax.bar_label(bars, fmt="%.1f", padding=3, fontsize=9)
        ax.set(xticks=range(3), xticklabels=["IF", "K-means", "LOF"], ylim=(0, 105), title=title)
        ax.set_ylabel("%")
    axes[0].legend(loc="upper left", fontsize=9)
    fig.suptitle("단일 탐지기 비교 · 기존 전체 벤치마크 · 정상 개발 상위 3% 기준", fontsize=14)
    fig.tight_layout(); fig.savefig(figures/"metrics.png", dpi=160); plt.close(fig)

    fig, axes = plt.subplots(1, 3, figsize=(17, 5.2), sharey=True)
    for ax, (algorithm, label) in zip(axes, NAMES.items()):
        for j, view in enumerate(("baseline", "adapter_features")):
            values = [next(r["f1"]*100 for r in d["metrics"] if r["method"] == algorithm+"_"+view)
                      for d in summary["datasets"] if d["dataset"] in MAIN_SCENARIOS]
            bars = ax.bar(np.arange(5)+(j-.5)*.34, values, width=.32, color=("#94a3b8", "#2563eb")[j],
                          label=("기존 피처", "Adapter 피처 추가")[j])
            ax.bar_label(bars, fmt="%.1f", padding=2, fontsize=8)
        ax.set(xticks=range(5), xticklabels=[SCENARIOS[s] for s in MAIN_SCENARIOS], ylim=(0, 105), title=label)
        ax.tick_params(axis="x", labelrotation=25)
    axes[0].set_ylabel("F1 (%)"); axes[0].legend(fontsize=9)
    fig.tight_layout(); fig.savefig(figures/"scenario_f1.png", dpi=160); plt.close(fig)

    samples = joblib.load(OUTPUT/"analysis_samples.joblib")
    model_records = {r["method"]: r for r in frozen["models"]}
    for algorithm, title in NAMES.items():
        fig, axes = plt.subplots(1, 2, figsize=(13, 4.5))
        for ax, view in zip(axes, ("baseline", "adapter_features")):
            method = algorithm+"_"+view
            scores = samples[method+"_score"].to_numpy()
            cutoff = model_records[method]["thresholds"]["0.03"]
            # Histogram range includes the threshold; clipped tails are explicitly counted.
            lo, hi = np.quantile(scores, [.001, .995])
            lo, hi = min(lo, cutoff), max(hi, cutoff)
            if hi <= lo: hi = lo+1
            edges = np.linspace(lo, hi, 36)
            for truth, label, color in ((False, "정상", "#94a3b8"), (True, "이상", "#ef4444")):
                values = scores[samples.actual_anomaly.to_numpy() == truth]
                ax.hist(values, bins=edges, weights=np.full(len(values), 1/max(len(values), 1)),
                        alpha=.6, color=color, label=label)
            outside = np.mean((scores < lo) | (scores > hi))*100
            ax.axvline(cutoff, color="black", linestyle="--", linewidth=1.7, label=f"threshold={cutoff:.4g}")
            ax.set(title=("기존 피처" if view == "baseline" else "Adapter 피처 추가")+f" · 범위 밖 {outside:.2f}%",
                   xlabel="이상 점수 (클수록 이상)", ylabel="각 집단 표본 중 비중")
            ax.legend(fontsize=9)
        fig.suptitle(title+" 점수 분포 · 설명용 표본, 전체 평가 지표는 전수 계산")
        fig.tight_layout(); fig.savefig(figures/(algorithm+"_scores.png"), dpi=150); plt.close(fig)

    columns = [("method", "모델"), ("accuracy", "Accuracy %"), ("precision", "Precision %"),
               ("recall", "Recall %"), ("f1", "F1 %"), ("normal_false_positive_rate", "정상 오탐률 %")]
    deltas = []
    for algorithm in NAMES:
        before, after = frame.loc[algorithm+"_baseline"], frame.loc[algorithm+"_adapter_features"]
        deltas.append(f"- {NAMES[algorithm]}: F1 {before.f1*100:.2f}% → {after.f1*100:.2f}% "
                      f"({(after.f1-before.f1)*100:+.2f}%p), 정상 오탐률 "
                      f"{before.normal_false_positive_rate*100:.2f}% → {after.normal_false_positive_rate*100:.2f}%")
    old = read(ROOT/"reports/model_research_v18/evaluation_4f5469a098b82372/summary.json")
    old_rows = [r for r in old["pooled"] if r["method"].endswith(("_global", "_ensemble"))]
    feature_lines = "\n".join(f"| `{name}` | {desc} |" for name, desc in DESCRIPTIONS.items())
    diagnostics = read(OUTPUT/"normal_shift_diagnostics.json")
    workload = next(r for r in diagnostics["feature_scale_shift"] if r["feature"] == "adapter_workload_z")
    text = f"""# Adapter 전문가 없이 단일 모델 + Adapter 파생 피처 비교

## 핵심 결과

{chr(10).join(deltas)}

{markdown_table(rows, columns)}

![전체 성능](figures/metrics.png)

## 비교 조건

- 각 알고리즘·피처 조합마다 탐지기 정확히 하나. Adapter 전문가, 전문가 fallback, Adapter별 threshold 없음.
- 동일 정상 학습 19,793분, 720개 재생 사례. Adapter 통계도 이 학습 분에서만 생성.
- 독립 정상 개발 17,925분으로 공통 목표 오탐률 3% threshold 설정. 최종 테스트 정답으로 설정 변경 없음.
- 기존 파생 피처 28개 대 기존 + Adapter 피처 10개. 기존 피처에도 이미 Adapter·Process·totalCount 조건부 정보가 있음. 'Adapter 정보 전혀 없음' 대비 실험이 아님.
- IF: 300 trees, max_samples=512. K-means: 16 clusters, batch=2048, n_init=10, 군집별 정상 P95 거리로 정규화. LOF: novelty=True, 35 neighbors, 정상 참조 상한 2,048분, Euclidean. seed=42.
- 입력 스케일은 정상 학습 중앙값·P90-P10, 결측값은 학습 중앙값 대체. 직접 ID·STATUS·시나리오·정답 피처 없음.
- 모든 테스트 {summary['protocol']['evaluation_decisions']:,}분을 그대로 평가. 기존 6개 시나리오 + Hard. 5개 주요 시나리오는 아래에 별도 집계.
- 시간창별 판정, point adjustment 없음. Accuracy는 클래스 비율 영향이 있어 F1·Precision·Recall·오탐률·AP·ROC-AUC 함께 확인.
- v18의 전체 경로는 여러 내부 분기를 결합한 모델. 아래 v18 비교는 역사적 참고이며, 동일 구조에서 피처만 바꾼 인과 비교는 위 6개만 해당.

## 추가한 피처

| 컬럼 | 의미 |
| --- | --- |
{feature_lines}

각 Adapter 통계는 **개별 Process 스트림의 분 집계 분포**다. 여러 Process를 합친 Adapter 전체 요청 수가 아니다. 처리건수는 최근 5분 평균 `totalCount`의 log2 구간. 정상 30분 미만이면 Adapter+건수구간 → Adapter → 전체 기준으로 넓힌다. 참고 행 수와 fallback 단계는 진단용이며 탐지기 입력에서 제외한다.

## 주요 5개 시나리오

{markdown_table(summary['main_five_pooled'], columns)}

![시나리오별 F1](figures/scenario_f1.png)

## 기존 v18 모델 참고

{markdown_table(old_rows, columns)}

새 모델이 v18을 대체할 만큼 좋은지와 Adapter 피처 자체가 유용한지는 다른 질문이다. 성능이 낮아도 테스트 결과로 재학습·threshold 변경하지 않는다.

## 해석과 제약

- 정상 데이터만의 추가 진단: `adapter_workload_z`의 정규화 절댓값 P95는 학습 {workload['train_abs_scaled_p95']:.2f}, 정상 개발 {workload['normal_calibration_abs_scaled_p95']:.2f}. 학습 스케일이 0.05 하한에 걸렸고, 정상 개발 {workload['normal_calibration_fraction_abs_scaled_gt_20']*100:.2f}%에서 정규화 절댓값 20 초과. 처리건수는 정상 문맥일 수도 있는데 수치 이탈로 크게 표현된 것. 거리 기반 모델의 높은 정상 개발 점수/threshold와 함께 관측되었으나 해당 피처 하나의 인과적 기여도를 분리한 실험은 아님.
- 다음 검증 후보는 처리건수 자체의 희귀도를 제외하고, totalCount를 소요시간 비교 조건으로만 쓰는 구조 및 정상 개발에서 거의 상수인 피처의 안정적 스케일링. 이번 최종 테스트를 본 뒤 이를 적용해 재선택하지 않았음. 따라서 'Adapter 파생 피처가 원래 무효'라고 일반화할 수 없음.
- 같은 정상 개발 오탐 목표라도 실제 테스트 오탐률은 다를 수 있음. 테스트에서 오탐률을 강제로 맞추거나 최적 F1 threshold를 고르지 않음.
- 학습·개발은 과거 성공(S) 정상 후보를 재생한 데이터. S가 성능 정상임을 보장하지 않으며, 운영 트래픽 빈도를 그대로 대표하지 않음.
- 현재 벤치마크는 사례마다 Adapter+Process 하나만 재생. Adapter 전체 동시 지연 비율·활동 Process 감소율은 계산/검증하지 않음. 서로 독립적인 사례를 같은 시간으로 합쳐 꾸며내지 않음.
- 시간대 실행량 기준은 합성 prior 및 정상 재생 분포에 의존. 운영 Adapter별 실제 과거 집계를 대체하지 않음.
- 알려지지 않은 Adapter는 통합 통계 사용. 낯선 이름 자체를 이상으로 취급하지 않음. 기존 기간 변화와 같은 완전한 Adapter cold-start 전용 성능 검증은 아님.
- 이미 살펴본 합성 벤치마크에서 수행한 연구 비교. 외부 블라인드 운영 성능 보장 아님.
- 점수와 threshold가 실제 경보 근거. 표본의 피처 값은 해석 단서이며 장애 원인이나 정확한 기여도를 증명하지 않음.

## ADBench 활용 검토

별도 [ADBENCH.md](../../code/adapter_feature_ablation/ADBENCH.md) 참고. ADBench를 설치·실행하거나 벤치마크를 변경하지 않았음.

## 재현

`python code/adapter_feature_ablation/run.py --stage train` → `--stage evaluate` → `--stage report`. 동결 파일이 있으면 train 재실행 거부. Python 경로에 code를 넣어 테스트 실행.
"""
    (OUTPUT/"RESULTS.md").write_text(text, encoding="utf-8")
    # Notebook displays exact saved results, not a second training implementation.
    nb = nbformat.v4.new_notebook()
    md = nbformat.v4.new_markdown_cell
    code = nbformat.v4.new_code_cell
    nb.cells = [md("# Adapter 전문가 제거 실험\n\n알고리즘별 단일 탐지기의 피처 추가 전·후 비교. 기존 벤치마크와 v18 모델은 보존한다."),
        md("## 1. 무엇을 비교했나\n\n- 기존 피처 28개 대 Adapter 파생 피처 10개 추가\n- IF·K-means·LOF 각각 탐지기 하나, 전문가/Adapter별 threshold 없음\n- 정상 개발 상위 3%로 경보 기준을 고정한 뒤 기존 테스트 전수 평가\n- 통계는 정상 학습에서만 계산. 최종 테스트로 튜닝하지 않음"),
        code("from pathlib import Path\nimport json, pandas as pd\nfrom IPython.display import display, Image, Markdown\nROOT = next(p for p in [Path.cwd(), *Path.cwd().parents] if (p/'reports/adapter_feature_ablation/summary.json').exists())\nOUT = ROOT/'reports/adapter_feature_ablation'\nresult = json.loads((OUT/'summary.json').read_text(encoding='utf-8'))\nfrozen = json.loads((OUT/'frozen.json').read_text(encoding='utf-8'))\nmetrics = ['accuracy','precision','recall','f1','normal_false_positive_rate','average_precision','roc_auc']\ndef table(rows, columns=metrics):\n    data = pd.DataFrame(rows).set_index('method')\n    return (data[[c for c in columns if c in data]]*100).round(2)"),
        md("## 2. 전체 성능\n\n단위 %. 전체 6개 시나리오와 Hard 포함. 오탐률은 낮을수록 좋다. AP는 Average Precision이다."),
        code("display(table(result['pooled']))\ndisplay(Image(filename=str(OUT/'figures/metrics.png')))"),
        md("## 3. 피처 추가 효과\n\n"+"\n".join(deltas)),
        md("## 4. 주요 5개 시나리오\n\n컬럼 관계·Hard를 제외한 합산도 별도 제공. 제외된 두 데이터도 삭제하거나 숨기지 않고 다음 셀에서 확인한다."),
        code("display(table(result['main_five_pooled']))\ndisplay(Image(filename=str(OUT/'figures/scenario_f1.png')))\nfor dataset in result['datasets']:\n    display(Markdown('### '+dataset['dataset']))\n    display(table(dataset['metrics']))"),
        md("## 5. 실제 threshold와 오탐 목표 민감도\n\nscore > threshold면 경보. 점수는 확률이 아니다. 1%·3%·5%는 정상 개발에서 설정한 목표이며, 테스트 오탐률을 보장하지 않는다."),
        code("display(pd.DataFrame([{'method':m['method'],**m['thresholds']} for m in frozen['models']]))\nfor rate, rows in result['sensitivity'].items():\n    display(Markdown('### 정상 개발 오탐 목표 '+str(float(rate)*100)+'%'))\n    display(table(rows))"),
        md("## 6. 점수 분포와 경보선\n\n일반 막대 히스토그램. 정상/이상 각각 표본 내 비중. 극단 꼬리는 표시 범위 밖 비중으로 명시. 표본 그림과 전수 지표를 혼동하지 않는다."),
        code("for algorithm in ['if','kmeans','lof']:\n    display(Image(filename=str(OUT/'figures'/f'{algorithm}_scores.png')))"),
        md("## 7. 추가된 Adapter 피처\n\n정상 학습에서 만든 참고 통계이며 별도 전문가 모델이 아니다. Adapter 전체 요청 수가 아니라 Process 스트림의 Adapter 기준 편차다."),
        code("display(pd.Series(frozen['added_feature_descriptions'],name='의미').to_frame())\ndisplay(pd.DataFrame([{'method':m['method'],'피처 수':len(m['columns']),'학습 분':m['fit_rows'],'탐지기 수':m['estimator_count']} for m in frozen['models']]))"),
        md("## 8. 오탐·미탐 사례 읽기\n\nTP=맞힌 이상, FP=오탐, FN=놓친 이상. 피처 값은 단서이며 모델의 인과적 기여도나 장애 원인을 의미하지 않는다."),
        code("examples = pd.DataFrame([{'dataset':d['dataset'],**e} for d in result['datasets'] for e in d['examples']])\ndisplay(examples[['dataset','method','outcome','adapterType','processId','score','threshold','zero_run','arrival_dev_1m','adapter_count_p95_z','adapter_stream_arrivals_z']].head(24))"),
        md("## 9. 기존 v18과 참고 비교\n\nv18은 내부 분기가 여러 개이므로 이번 단일 탐지기와 구조가 다르다. 여기서는 모델 대체 가능성을 보는 참고 비교이며 피처 효과만의 공정한 비교는 아니다."),
        code("old = json.loads((ROOT/'reports/model_research_v18/evaluation_4f5469a098b82372/summary.json').read_text(encoding='utf-8'))\ndisplay(table([r for r in old['pooled'] if r['method'].endswith(('_global','_ensemble'))]))"),
        md("## 10. 정상 데이터에서 피처가 안정적인가?\n\n추가한 Adapter 처리건수 편차는 학습에서 거의 상수인 구간이 많아 스케일 하한 0.05가 적용되었다. 정상 개발에서 정규화 크기가 커지고 거리 기반 모델의 경보선이 높아지는 현상이 함께 나타났다. 인과적 피처 기여도를 확정한 것은 아니다. 정상 처리건수 변화가 반드시 장애는 아니므로, 향후 이 피처 제외/안정적 스케일링을 별도 검증할 필요가 있다."),
        code("shift = json.loads((OUT/'normal_shift_diagnostics.json').read_text(encoding='utf-8'))\ndisplay(pd.DataFrame(shift['feature_scale_shift']))\ndisplay(pd.DataFrame(shift['score_shift']))"),
        md("## 11. 결론을 적용할 범위\n\n- 1회 고정 seed 비교이며 통계적 우월성을 확정하지 않는다.\n- 기존 피처에도 Adapter 조건부 정보가 있으므로 정보가 전혀 없는 모델과 비교한 것은 아니다.\n- 동시 Process 집단/Adapter 전체 트래픽은 현재 독립 재생 사례로 검증하지 못한다.\n- S 정상 후보, 합성 트래픽 prior, 같은 합성 생성기라는 제한이 있다.\n- 피처가 안 좋아진 결과도 그대로 보존하며 기존 v18 교체/배포를 수행하지 않는다.\n- ADBench 검토는 같은 폴더 ADBENCH.md. 기존 데이터/운영 모델/Notion/GitHub는 변경하지 않았다.")]
    nb.metadata = {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                   "language_info": {"name": "python"}}
    nbformat.write(nb, ROOT/"code/adapter_feature_ablation/result.ipynb")
    write(OUTPUT/"report_audit.json", {"summary_sha256": sha256(OUTPUT/"summary.json"),
        "frozen_sha256": sha256(OUTPUT/"frozen.json"), "notebook_executed": False,
        "png_count": len(list(figures.glob("*.png")))})
    print("Report, plain bar charts and notebook built", flush=True)
