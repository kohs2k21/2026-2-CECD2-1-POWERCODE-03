"""Readable improvement report, plain bars, actual threshold histograms."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"code"))
import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import nbformat
from nbclient import NotebookClient
from jupyter_client import KernelManager
import numpy as np
import pandas as pd

from shared_model_improvement.run import OUTPUT, OLD, ALGORITHMS, MAIN_SCENARIOS
from model_research_v13.data import read, write

NAMES = {"if": "Isolation Forest", "kmeans": "MiniBatch K-means", "lof": "LOF"}
SCENARIOS = {"individual_delay": "개별 지연", "group_delay": "집단 지연", "column_relation": "컬럼 관계",
             "time_burst": "시간대 폭주", "input_stop": "입력 중단", "gradual_degradation": "점진적 악화", "mixed_hard": "Hard"}


def paired_bootstrap(summary):
    """Resample paired observed/control case units within each scenario dataset."""
    selected = read(OUTPUT/"selection.json")
    original = read(OLD/"frozen.json")
    cutoffs = {r["method"]:r["thresholds"]["0.03"] for r in original["models"]}
    blocks = []
    for dataset in summary["datasets"]:
        name = dataset["dataset"]
        new = np.load(OUTPUT/(name+"_scores.npz"))
        old = np.load(OLD/(name+"_scores.npz"))
        np.testing.assert_array_equal(new["y"], old["y"])
        y, ids = new["y"], new["case_index"]
        block = np.zeros((int(ids.max())+1, 3, 2, 4), np.int64)
        for i, a in enumerate(ALGORITHMS):
            predictions = (old["scores"][:, i*2] > cutoffs[a+"_baseline"],
                           new["scores"][:, i] > selected["selected"][a]["threshold"])
            for view, pred in enumerate(predictions):
                masks = (~y & ~pred, ~y & pred, y & ~pred, y & pred)
                for j, mask in enumerate(masks):
                    block[:, i, view, j] = np.bincount(ids, weights=mask.astype(int), minlength=len(block))
        blocks.append(block)
    rng = np.random.default_rng(161042)
    deltas = []
    for _ in range(300):
        total = np.zeros((3, 2, 4), float)
        for block in blocks:
            ids = rng.integers(0, len(block), len(block))
            total += block[ids].sum(axis=0)
        tp, fp, fn = total[:, :, 3], total[:, :, 1], total[:, :, 2]
        f1 = 2*tp/np.maximum(2*tp+fp+fn, 1)
        deltas.append(f1[:, 1]-f1[:, 0])
    values = np.asarray(deltas)
    result = {"replicates": 300, "unit": "paired observed/control case within dataset", "used_for_selection": False,
        "records": [{"algorithm": a, "f1_delta_ci95_percentage_points": (np.quantile(values[:, i], [.025, .975])*100).tolist()}
                    for i, a in enumerate(ALGORITHMS)],
        "limitation": "Case-level synthetic variability only; shared source templates and generator prevent real-world independence claims."}
    write(OUTPUT/"paired_bootstrap.json", result)
    return result


def table(rows):
    keys = ("accuracy", "precision", "recall", "f1", "normal_false_positive_rate", "average_precision", "roc_auc")
    df = pd.DataFrame(rows).set_index("method")
    return (df[[k for k in keys if k in df]]*100).round(2)


def md_table(df):
    return "\n".join(["| 모델 | "+" | ".join(df.columns)+" |", "| --- | "+" | ".join("---" for _ in df.columns)+" |",
        *("| "+str(index)+" | "+" | ".join(str(v) for v in row)+" |" for index, row in df.iterrows())])


def build(folder=None):
    global OUTPUT
    if folder is not None: OUTPUT = Path(folder).resolve()
    summary, old, selected = read(OUTPUT/"summary.json"), read(OLD/"summary.json"), read(OUTPUT/"selection.json")
    confirmation = read(OUTPUT/"confirmation.json")
    ci = paired_bootstrap(summary)
    plots = OUTPUT/"figures"; plots.mkdir(exist_ok=True)
    plt.rcParams.update({"font.family": "Malgun Gothic", "axes.unicode_minus": False, "font.size": 11,
                         "axes.spines.top": False, "axes.spines.right": False})
    comparison = pd.DataFrame(summary["comparisons"]).set_index("algorithm")
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))
    colors = ("#94a3b8", "#fca5a5", "#2563eb")
    for j, (column, label) in enumerate((("before_adapter_f1", "피처 추가 전"),
            ("initial_adapter_f1", "첫 추가 실험"), ("improved_f1", "이번 개선"))):
        bars = axes[0].bar(np.arange(3)+(j-1)*.25, comparison[column]*100, width=.23, label=label, color=colors[j])
        axes[0].bar_label(bars, fmt="%.2f", padding=3, fontsize=9)
    axes[0].set(xticks=range(3), xticklabels=["IF", "K-means", "LOF"], ylim=(0, 65), ylabel="F1 (%)", title="F1 · 동일 전체 벤치마크")
    axes[0].legend(fontsize=9)
    for j, label in enumerate(("피처 추가 전", "이번 개선")):
        values = [next(r["normal_false_positive_rate"] for r in (old["pooled"] if j == 0 else summary["pooled"])
                       if r["method"] == a+("_baseline" if j == 0 else ""))*100 for a in ALGORITHMS]
        bars = axes[1].bar(np.arange(3)+(j-.5)*.3, values, width=.28, label=label, color=(colors[0], colors[2])[j])
        axes[1].bar_label(bars, fmt="%.2f", padding=3, fontsize=9)
    axes[1].set(xticks=range(3), xticklabels=["IF", "K-means", "LOF"], ylabel="정상 오탐률 (%)", title="오탐률 · 낮을수록 좋음")
    axes[1].legend(fontsize=9)
    fig.tight_layout(); fig.savefig(plots/"comparison.png", dpi=160); plt.close(fig)

    fig, axes = plt.subplots(1, 3, figsize=(17, 5.2), sharey=True)
    for ax, algorithm in zip(axes, ALGORITHMS):
        for j, label in enumerate(("피처 추가 전", "이번 개선")):
            source = old if j == 0 else summary
            method = algorithm+("_baseline" if j == 0 else "")
            values = [next(r["f1"]*100 for r in d["metrics"] if r["method"] == method)
                      for d in source["datasets"] if d["dataset"] in MAIN_SCENARIOS]
            bars = ax.bar(np.arange(5)+(j-.5)*.34, values, width=.32, color=(colors[0], colors[2])[j], label=label)
            ax.bar_label(bars, fmt="%.1f", padding=2, fontsize=8)
        ax.set(title=NAMES[algorithm], xticks=range(5), xticklabels=[SCENARIOS[n] for n in MAIN_SCENARIOS], ylim=(0, 105))
        ax.tick_params(axis="x", labelrotation=25)
    axes[0].set_ylabel("F1 (%)"); axes[0].legend(fontsize=9)
    fig.tight_layout(); fig.savefig(plots/"scenario_f1.png", dpi=160); plt.close(fig)

    samples = joblib.load(OUTPUT/"analysis_samples.joblib")
    for algorithm in ALGORITHMS:
        score = samples[algorithm+"_score"].to_numpy()
        cutoff = selected["selected"][algorithm]["threshold"]
        raw_cutoff = cutoff
        # Keep ordinary bars but make LOF's long right tail readable. Scores and
        # thresholds used for prediction/metrics are never transformed here.
        if algorithm == "lof":
            score, cutoff = np.log1p(score), np.log1p(cutoff)
        low, high = np.quantile(score, [.001, .995]); low, high = min(low, cutoff), max(high, cutoff)
        if high <= low: high = low+1
        fig, ax = plt.subplots(figsize=(9, 4))
        bins = np.linspace(low, high, 40)
        for truth, label, color in ((False, "정상", colors[0]), (True, "이상", "#ef4444")):
            values = score[samples.actual_anomaly.to_numpy() == truth]
            ax.hist(values, bins=bins, weights=np.full(len(values), 1/max(len(values), 1)), alpha=.6, label=label, color=color)
        ax.axvline(cutoff, color="black", linestyle="--", linewidth=1.7, label=f"원점수 threshold={raw_cutoff:.5g}")
        ax.set(title=NAMES[algorithm]+f" · 설명용 표본, 표시 밖 {np.mean((score<low)|(score>high))*100:.2f}%",
               xlabel=("log1p(이상 점수) · 표시만 변환" if algorithm == "lof" else "이상 점수 (클수록 이상)"),
               ylabel="정상/이상 각각의 표본 내 비중")
        ax.legend(); fig.tight_layout(); fig.savefig(plots/(algorithm+"_scores.png"), dpi=150); plt.close(fig)

    history = [{**r, "method":r["method"]} for r in old["pooled"]]
    history.extend({**r, "method":r["method"]+"_improved"} for r in summary["pooled"])
    complete_table = table(history)
    improvements = "\n".join(f"- {NAMES[r['algorithm']]}: F1 {r['before_adapter_f1']*100:.2f}% → {r['improved_f1']*100:.2f}% "
        f"({(r['improved_f1']-r['before_adapter_f1'])*100:+.2f}%p), Adapter 피처 {r['adapter_feature_count']}개, 목표 {'달성' if r['target_met'] else '미달'}"
        for r in summary["comparisons"])
    text = f"""# Adapter 피처를 유지한 단일 모델 개선 결과

## 핵심 결과

{improvements}

![전체 비교](figures/comparison.png)

{md_table(complete_table)}

## 무엇을 바꿨나

- Adapter 피처 10개 중 처리건수 자체의 특이도를 나타내는 `adapter_workload_z` 하나만 제외한 후보 또는 10개 유지 후보 비교. 실제 채택은 아래 설정 참조.
- 단순 강한 선형 스케일링 대신 안정적인 스케일 하한, clip/asinh, QuantileTransformer, Adapter 피처 블록의 양수 가중치를 개발 데이터에서 비교.
- IF는 트리 표본 수/피처 선택 비율, K-means는 군집 수, LOF는 이웃 수/참고 표본 수/중복 제거 비교.
- IF는 중복 기본 피처를 줄이는 full/balanced/compact 후보도 비교. Adapter 피처 9개는 그대로 유지하고 실제 선택한 view와 전체 입력 개수는 노트북 설정 표에 표시.
- LOF hybrid는 기본 28개 피처의 기존 스케일을 그대로 보존하고 Adapter 블록만 안정화.
- 각 알고리즘에 탐지기 하나, Adapter 전문가 없음. 세 알고리즘을 합친 앙상블도 없음.
- 하이퍼파라미터와 threshold도 바뀌었으므로 개선 전체는 순수한 피처 추가만의 효과가 아님.

## 선택과 검증

- 정상 train 19,793분에서만 탐지기·스케일러 학습, 정상 개발 17,925분의 점수 quantile로 경보선 후보 생성.
- search에서 기존 모델 대비 오탐률 +0.5%p 이내(최소 상한 3%), Precision -2%p 이내 제약 후 F1 선택.
- 파라미터 선택에 직접 쓰지 않은 기존 confirmation에서 설정 동결 후 확인. 반복 개선 중 기존 결과를 이미 확인한 연구이며 새로운 외부 블라인드 집단은 아님.
- 알고리즘 학습은 정상 후보만의 비지도, 하이퍼파라미터/경보선 선택은 개발 라벨 활용.
- 이전 실패 실험의 최종 결과를 이미 보고 구조를 개선한 연구. 외부 블라인드 성능으로 주장하지 않음.
- 모든 기존 2,167,766분 평가, 데이터/정답/기존 모델 변경 없음. point adjustment 없음.
- 주요 5개와 전체 6개+Hard를 구분. 점수는 확률이 아니며 score > 고정 threshold에서 경보.

## 주요 5개 합산

{md_table(table(summary['main_five_pooled']))}

![시나리오별](figures/scenario_f1.png)

## 설정과 성능을 읽을 때

- 세 모델의 강점은 이번 피처와 합성 평가에서 관측된 결과이지 알고리즘의 고유 시나리오 보장은 아님.
- 전수 F1이 올라도 모든 시나리오/Precision/Recall이 동시에 오르는 것은 아님. 전체/시나리오별 실제 표에서 확인.
- QuantileTransformer의 학습 범위 밖 포화와 상관 왜곡 한계: [공식 문서](https://scikit-learn.org/stable/modules/generated/sklearn.preprocessing.QuantileTransformer.html).
- 쌍체 부트스트랩 300회는 같은 사례의 정상/이상 쌍을 함께 재표집. 원본 템플릿/생성기를 공유하므로 운영 일반화의 신뢰구간이 아님.
- 상태 S는 정상 후보이지 성능 정상 보장이 아님. 시간대 prior는 합성. 실시간 운영에는 과거 상태와 빈 시간창 생성/지연 로그 처리 필요.
- 이번 모델을 자동으로 운영 배포하거나 기존 v18을 교체하지 않음.

설정은 selection.json, 경보선과 모델은 handoff/manifest.json, 재계산 검증은 audit.json, 실행된 표/분포는 result.ipynb.
"""
    (OUTPUT/"RESULTS.md").write_text(text, encoding="utf-8")

    md, code = nbformat.v4.new_markdown_cell, nbformat.v4.new_code_cell
    nb = nbformat.v4.new_notebook()
    nb.cells = [md("# 단일 모델 성능 개선\n\nAdapter 전문가 없이 파생 피처 9~10개를 유지한 개선. 기존 벤치마크와 기존 모델은 보존."),
        md("## 1. 핵심 결과\n\n"+improvements),
        code("from pathlib import Path\nimport json, pandas as pd\nfrom IPython.display import display, Image, Markdown\nROOT=next(p for p in [Path.cwd(),*Path.cwd().parents] if (p/'reports/shared_model_improvement/summary.json').exists())\nOUT=ROOT/'reports/shared_model_improvement'\nread=lambda p: json.loads(p.read_text(encoding='utf-8'))\nresult=read(OUT/'summary.json'); old=read(ROOT/'reports/adapter_feature_ablation/summary.json'); selected=read(OUT/'selection.json')\ndef table(rows):\n    df=pd.DataFrame(rows).set_index('method')\n    return (df[[c for c in ['accuracy','precision','recall','f1','normal_false_positive_rate','average_precision','roc_auc'] if c in df]]*100).round(2)"),
        md("## 2. 같은 전체 벤치마크에서 전후 비교\n\n추가 전 / 첫 추가 실패 / 이번 개선. 단위 %. 오탐률은 낮을수록 좋다. Accuracy는 정상 비중에 영향을 받으므로 F1·Recall과 함께 본다."),
        code("rows=[*old['pooled'],*[{**r,'method':r['method']+'_improved'} for r in result['pooled']]]\ndisplay(table(rows))\ndisplay(Image(filename=str(OUT/'figures/comparison.png')))"),
        md("## 3. 무엇을 바꾸었나\n\n- 불안정한 처리건수 자체 편차 피처 제외 후보(나머지 9개 유지)\n- 안정적 스케일/압축과 알고리즘별 설정 비교\n- IF는 기본 피처의 중복을 줄이는 view도 비교. Adapter 피처는 그대로 유지\n- LOF hybrid는 기본 피처 스케일을 그대로 두고 Adapter 추가 블록만 안정화\n- 모델과 경보선도 조정되었으므로 순수 피처 효과만의 비교는 아님"),
        code("manifest=read(OUT/'handoff/manifest.json')\nrelevant={'if':['trees','samples','max_features'],'kmeans':['clusters','cluster_radius'],'lof':['neighbors','reference_cap','metric','deduplicate']}\nfor r in manifest['models']:\n    a=r['algorithm']; settings=r['settings']\n    values={k:settings.get(k,'full' if k=='view' else None) for k in ['transform','view','adapter_weight',*relevant[a]]}\n    values.update({'normal_quantile':selected['selected'][a]['normal_quantile'],'threshold':r['threshold'],'total_features':len(r['columns']),'Adapter_features':r['adapter_feature_count'],'fit_or_reference_rows':r['fit_rows'],'detector_count':r['estimator_count']})\n    display(Markdown('### '+a))\n    display(pd.Series(values,name='값').to_frame())\ndisplay(Markdown('K-means 추가 고정값: batch_size=2048, n_init=10, max_iter=150. 군집별 정상 P95 거리로 정규화. 세 알고리즘의 seed=42.'))"),
        md("## 4. 개발 탐색과 확인 데이터\n\n설정은 search에서 선택하고 기존 confirmation에서 선택 후 확인. 이 분할들의 case_id는 상호 분리지만 반복 개선 중 이미 확인한 연구 집단이며 새로운 외부 블라인드 평가는 아니다. 최종 평가 결과가 구조 설계에 영향을 주었다는 사실도 메타데이터에 기록한다."),
        code("display(pd.DataFrame([{'algorithm':a,**r} for a,r in selected['baseline'].items()])[['algorithm','precision','recall','f1','normal_false_positive_rate']])\ndisplay(pd.DataFrame(read(OUT/'confirmation.json')['metrics'])[['algorithm','method','precision','recall','f1','normal_false_positive_rate']])"),
        md("## 5. 주요 5개와 시나리오별 결과\n\n전체 표에는 컬럼 관계/Hard도 포함하며 아래 주요 5개 집계와 구분. 일괄 F1 상승이 모든 시나리오의 상승을 뜻하지 않는다."),
        code("display(table(result['main_five_pooled']))\ndisplay(Image(filename=str(OUT/'figures/scenario_f1.png')))\nfor dataset in result['datasets']:\n    display(Markdown('### '+dataset['dataset']))\n    display(table(dataset['metrics']))"),
        md("## 6. 분포와 실제 경보선\n\n일반 막대 히스토그램. 각 정상/이상 집단 내 표본 비중. 점선보다 큰 점수가 경보. LOF는 긴 꼬리 때문에 표시 축만 log1p 변환하고 원점수 threshold를 범례에 표시. 판정/지표는 원점수 그대로 사용. 극단 꼬리는 표시 밖 비중 명시. 표본 그림과 전수 지표는 구분."),
        code("for a in ['if','kmeans','lof']:\n    display(Image(filename=str(OUT/'figures'/f'{a}_scores.png')))"),
        md("## 7. 유지한 Adapter 피처와 실제 사용\n\n참고 통계는 정상 train 전용. 새 Adapter는 전체 참고 분포를 쓰며 별도 전문가 fallback은 없다. 참고 표본 수/단계는 진단용이고 탐지기 입력은 아니다."),
        code("manifest=read(OUT/'handoff/manifest.json')\nfor model in manifest['models']:\n    display(Markdown('### '+model['algorithm']))\n    display(pd.DataFrame({'Adapter 피처':[c for c in model['columns'] if c.startswith('adapter_')]}))"),
        md("## 8. 개선 폭의 표본 불확실성\n\n300회 시나리오별 사례 단위 쌍체 부트스트랩. 정상/이상 재생 쌍을 함께 표집. 같은 원본 pool·생성기를 공유하므로 실제 운영 일반화 신뢰구간이 아님."),
        code("display(pd.DataFrame(read(OUT/'paired_bootstrap.json')['records']))"),
        md("## 9. 적용 범위\n\n- 정상 S 후보와 합성 prior에 기반, 실제 장애 전반의 성능 보장 아님.\n- 하이퍼파라미터 선택에는 개발 정답을 사용. 완전 무라벨 연구가 아님.\n- 실제 Adapter 전체 동시 Process 트래픽 피처는 이번 독립 재생에 없으므로 주장하지 않음.\n- 운영 모델 자동 교체·GitHub 업로드·Notion 수정 없음.\n- 모델은 연속 1분 grid와 완료 로그의 과거 문맥이 필요하며, backend 워커의 상태 관리가 별도로 필요.")]
    nb.metadata = {"kernelspec": {"display_name":"Python 3","language":"python","name":"python3"},"language_info":{"name":"python"}}
    relative = str(OUTPUT.relative_to(ROOT)).replace("\\", "/")
    for cell in nb.cells:
        if cell.cell_type == "code": cell.source = cell.source.replace("reports/shared_model_improvement",relative)
    path = ROOT/"code/shared_model_improvement/result.ipynb"
    manager = KernelManager(kernel_name="python3")
    manager.kernel_spec.argv = [sys.executable,"-m","ipykernel_launcher","-f","{connection_file}"]
    client = NotebookClient(nb, km=manager, timeout=180, resources={"metadata":{"path":str(path.parent)}})
    client.execute(cleanup_kc=True); nbformat.write(nb, path)
    print("Executed improvement notebook, saved report, bars and threshold plots", flush=True)


if __name__ == "__main__":
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path)
    build(parser.parse_args().output)
