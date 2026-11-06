"""Create/execute a standalone readable result.ipynb; existing notebooks untouched."""
import argparse
import base64
import json
from pathlib import Path
import sys
import nbformat as nbf
from nbclient import NotebookClient
from jupyter_client import KernelManager

HERE = Path(__file__).resolve().parent; ROOT = HERE.parents[1]


def build():
    cells = []
    def md(text): cells.append(nbf.v4.new_markdown_cell(text.strip()))
    def code(text): cells.append(nbf.v4.new_code_cell(text.strip()))
    md("""# IF · MiniBatch K-means · LOF — 9가지 탐지 전략 비교

읽는 순서: **같은 데이터의 성능 → 시나리오별 차이 → 분포/판정선 → 탐지·오탐·미탐 사례 → 모델 전달**.

전체 모델, Adapter 전문가, 두 점수의 앙상블을 알고리즘마다 비교한다. 여기서 앙상블은
**IF+K-means+LOF 혼합이 아니라, 같은 알고리즘의 전체+Adapter 결합**이다.
이번 비교에서는 벤치마크를 바꾸지 않았다. 데이터 난도를 낮춘 효과를 모델 개선으로 계산하지 않는다.
과거에 이미 살펴본 합성 벤치마크이므로 외부 블라인드 운영 성능이라고 주장하지 않는다.""")
    code("""from pathlib import Path
import sys, json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from IPython.display import display, Markdown
ROOT = next(p for p in (Path.cwd(), *Path.cwd().parents) if (p/'code/model_research_v14/report.py').exists())
sys.path.insert(0, str(ROOT/'code'))
from model_research_v14 import report
report.setup()
pd.set_option('display.max_columns', 14)
pd.set_option('display.max_colwidth', 70)
s = report.summary()
selection = report.read(ROOT/'reports/model_research_v14/selection.json')
display(s['protocol'])""")
    md("""## 1. 무엇을 몇 개 평가했나?

6개 시나리오는 각각 **5,000개 이상 사건 사례**, 혼합 Hard는 **1,000개 사례**다.
각 사례에는 정상 배경 로그와 이상 구간이 함께 있고, 별도의 정상 대조 로그가 있다.
STATUS=S여도 주입된 지연·폭주·중단 때문에 이상 정답일 수 있다.

아래 성능표의 분모는 CSV 행이나 사건 수가 아니라 **Process 업무 스트림의 1분 의사결정 수**다.
아무 로그도 없는 분을 보존하므로 입력 중단도 평가할 수 있다. 완료 로그만 받는 모델은 중단을 알 수 없다.""")
    code("""display(pd.DataFrame([{'시나리오': report.TASKS[d['dataset']], '이상 사례 수': d['case_count'],
    '평가 분 수': d['metrics'][0]['n'], '정상 분': d['metrics'][0]['normal'], '이상 분': d['metrics'][0]['anomaly']}
    for d in s['datasets']]))
display(Markdown(f\"모든 분을 정상이라고 하면 Accuracy는 **{s['pooled'][0]['always_normal_accuracy']*100:.2f}%**다. Accuracy만으로 성능을 판단하지 않는다.\"))""")
    md("""## 2. 전체 성능 — 9개 전략

- Precision: 경보 중 실제 이상 비율. 높아도 Recall이 낮으면 대부분 놓칠 수 있다.
- Recall: 이상 분 중 탐지한 비율. 오탐률과 같이 읽는다.
- F1: 두 지표의 조화 평균. 긴 점진적 악화 사건이 분 단위 합산을 많이 차지한다.
- 정상 오탐률: 정상 분 중 경보가 난 비율. 분 단위와 사건 단위 오탐은 다르다.

일반 막대그래프다. 알고리즘 3개 × 경로 3개이며, 아래는 전체 평가 분을 집계했다.""")
    code("""display(report.table(s['pooled']))
report.bars(); plt.show()""")
    md("""## 3. 기존 v12보다 실제로 좋아졌나?

아래 비교는 **현재 동일한 벤치마크**에서 기존 v12와 새 후보를 비교한다.
양의 F1 변화가 Precision 감소 또는 오탐 증가를 동반했는지 확인한다.
검증에서 기존 모델을 넘지 못한 후보는 자동 채택하지 않았다. LOF는 새 비교 대상이라 기존 수치가 없다.""")
    md("""**이번 IF 후보는 Recall/F1 중심의 tradeoff이지 네 지표의 전면 개선이 아니다.**
IF 전체 F1은 34.43%→41.05%지만 Precision은 77.50%→63.13%, 정상 오탐은 1.33%→3.68%다.
그래서 보수적인 운영 경보에는 기존 v12를 보존한다. K-means는 기존 v12와 예측이 동일하다.
LOF도 확인/탐색 경보 예산을 충족한 운영 대체품이라고 주장하지 않는다.""")
    code("""display(report.before_after())
display(pd.DataFrame([{'알고리즘': a, '선택 후보': r['candidate'], '새 임계값 보정': r['recalibrated'],
    '보정 문맥': r.get('mode'), '정상 분위수 (전체/Adapter/앙상블)': r.get('quantiles'),
    '검증 경보 예산 충족': r.get('within_budget', '기존 모델 보존'), '주의': r.get('warning', '')}
    for a, r in selection['families'].items()]))""")
    md("""## 4. 시나리오별 강점과 약점

합산 F1만 높여 특정 장애를 버리지 않았는지 확인한다. 왼쪽은 F1, 오른쪽은 정상 오탐률이다.
**점진적 악화의 낮은 F1을 입력 중단·폭주까지 모두 시계열 탐지가 안 된다고 해석하지 않는다.**
기본 6개 시나리오를 같은 비중으로 평균한 macro 성능도 합산과 따로 본다.""")
    code("""report.scenario_bars(); plt.show()
display(report.characteristics())
display(report.table(s['basic_macro']))""")
    md("""### 모델의 성격 — 점수를 만드는 방법이 다르다

| 모델 | 이상 점수를 만드는 근거 | 기대하는 역할 | 주의점 |
|---|---|---|---|
| IF | 정상 벡터에서 드문 조합이 트리에서 쉽게 분리되는가 | 희귀 값/조합 후보 | 점수가 포화되면 작은 지속 변화가 잘 구분되지 않을 수 있음 |
| K-means | 정상 군집 중심에서 얼마나 먼가 | 정상 작동 유형의 중심에서 벗어남 | 넓은 정상 군집/거리 스케일에 민감 |
| LOF | 근처 정상 이웃보다 밀도가 얼마나 낮은가 | 전체 기준에서는 작아도 국소 정상 집단에서 드문 패턴 | 정상 표본 중복·희소 이웃·reference 크기에 민감, 추론 비용 증가 |

이것은 알고리즘의 원리다. 위 성능표의 실제 강점은 **피처·학습 표본·threshold까지 포함한 현재 설정**의 결과다.
세 모델 모두 시간창 피처를 입력받지만, 원시 시퀀스를 예측하는 신경망 모델은 아니다.""")
    md("""## 5. 입력 피처 분포를 먼저 보자

각 500사례 처리 청크에서 고정 seed로 최대 120개 분을 추출한 **설명용 표본**이다.
정상/이상 집단별 합이 100%인 일반 히스토그램이다. 표본은 실제 전체 빈도 비율의 추정치가 아니다.
실행 수와 소요시간만 `log(1+x)` 축으로 변환했다. 양끝 0.5%는 끝 막대에 합쳤고, 성능 집계에서는 자르지 않았다.

같은 소요시간도 업무·처리량에 따라 다르므로 조건부 소요시간 꼬리 피처가 필요하다.
누적 편차 분포가 다르더라도 실제 IF/K-means 검증 성능이 좋아지는지는 별개다.""")
    code("""report.input_histograms(); plt.show()""")
    md("""## 6. 이상 점수 분포와 실제 판정선

X축은 **해당 모델 점수 − 해당 문맥의 threshold**다. 0보다 크면 이상이다.
점선 오른쪽 정상은 오탐, 왼쪽 주입 이상은 미탐이다. 0은 원래 LOF 비율이 0이라는 뜻이 아니다.
알고리즘별 score 단위가 달라 그래프의 절대 너비를 알고리즘 사이에서 비교하지 않는다.

파일당 최대 5,000분을 고정 seed로 추출했고, 양끝 0.5%는 끝 막대에 합쳤다.
각 집단의 합을 100%로 정규화해 정상 분이 많아서 이상 막대가 사라지는 문제를 줄였다.""")
    md("""0을 히스토그램 구간 경계로 고정했다. `margin = 0`인 동률은 실제로 정상 판정이므로 왼쪽 막대에 포함했다.
두 집단을 겹치지 않은 나란한 막대로 표시해 겹침 색을 읽어야 하는 불편을 줄였다.""")
    code("""report.score_histograms(); plt.show()
thresholds = report.threshold_table()
display(thresholds.groupby(['알고리즘','경로'], sort=False).head(2))
km_model = next(m for m in report.read(ROOT/'reports/model_research_v14/handoff/manifest.json')['models'] if m['algorithm']=='kmeans')
display(pd.DataFrame([{'경로': r['original'].get('route'), '단계': r['original'].get('phase'),
    '분기': r['original'].get('regime'), '정규화 threshold': r['original'].get('normalized_threshold'),
    '앙상블 threshold': r['original'].get('ensemble_threshold')} for r in km_model['thresholds']]).head(12))
display(Markdown('모든 보정 문맥의 실제 숫자는 handoff/manifest.json의 thresholds에 저장되어 있다. 기존 모델을 보존한 경로의 원래 threshold도 같은 파일에 포함된다.'))""")
    md("""### 임계값은 어떻게 정했나?

원본 정상 학습·calibration·threshold 템플릿 집단은 서로 분리했다.
새 검증 NORMAL 사례의 절반은 추가 정상 보정용, 나머지는 탐색 성능용으로 분리했다.
**정상 탐색 집단과는 분리했지만, 보정 정상 사례 270개의 paired 이상 사례는 탐색 집단에 남아 있었다.**
따라서 탐색을 완전한 사례 단위 독립 검증이라고 부르지 않는다. `case_partition_audit.json`에 분할 메타데이터 정정을 기록했다.
탐색에서 각 경로의 정상 분위수를 골랐으며, 확인 사례와 최종 벤치마크로 threshold를 다시 맞추지 않았다.
관측 단계/트래픽·완료 근거 여부에 따른 문맥 보정을 시험했다. 미지원 Adapter는 전체 threshold를 그대로 쓴다.

LOF는 `novelty=True`로 정상 reference에 fit하고 새로운 관측만 `score_samples`한다.
`contamination='auto'`의 sklearn 기본 offset을 최종 서비스 threshold로 쓰지 않았다.
`log(-score_samples)`의 보정 점수를 사용하므로 최종 판정선 0과 원래 LOF 비율 1은 다른 값이다.
검증에서 설정한 오탐 예산은 실제 운영 오탐률 보장이 아니다.""")
    md("""### 선택된 하이퍼파라미터

| 알고리즘 | 선택 설정 |
|---|---|
| IF | v12 tree/분기 보존. 조건부 sparse/established IF 설정 max_samples=256, rotations=1. 이번 변경은 별도 정상 보정의 전체/Adapter/앙상블 분위수 0.975/0.995/0.925 |
| K-means | v12 보존. 초기 sparse 채널 K=12, established 군집 K=6. 새 K/공분산 후보는 탈락 |
| LOF | k=75, Euclidean, novelty=True, contamination='auto', seed=42. 중복 reference 제거, 채널별 최대 2,048 reference. 기존 6개 트래픽 + 5개 조건부 지연 + 5개 누적 지연 피처를 2개 채널로 사용. 보정 분위수 0.9995/0.9995/0.9995 |

LOF reference cap은 **학습 참고 이웃 수의 상한**이지 평가 사례 수나 원본 데이터 수가 아니다.
Adapter의 실제 reference는 채널별 약 427~543개, 전체는 두 채널 각각 2,048개다.
초기/안정 분기의 IF 설정을 적은 것이며 v12 내부의 모든 보존된 보조 모델을 256개 표본으로 재학습했다는 뜻은 아니다.""")
    md("""## 7. 실제 탐지·오탐·미탐 사례

각 전략에서 찾은 TP/FP/FN 사례를 제시한다. 실제 판정 근거는 저장된 margin이 0을 넘었는지다.
실행 수·무실행 연속 길이·조건부 지연은 **설명 단서**이며, 인과적 원인이나 SHAP 기여도로 단정하지 않는다.
처음 발견된 사례들이다. 전체 사례의 대표 통계로 해석하지 않는다.""")
    code("""examples = pd.DataFrame(report.read(report.location()/'decision_examples.json'))
display(examples.groupby(['method','outcome'], sort=False).head(1)[['method','outcome','dataset','adapterType','score_minus_threshold','native_execution_arrivals',
                  'zero_run','duration_p95_ms','duration_upper_max_1m','expert_available']])""")
    md("""## 8. Adapter 모델의 의미와 지원 범위

전체: 모든 학습 업무의 정상 패턴. Adapter: 해당 어댑터의 정상 패턴. 앙상블: 두 점수 결합.
새/희소 Adapter에는 전문가가 없으므로 **Adapter·앙상블 경로도 전체와 완전히 동일하게 fallback**한다.
Adapter별 표본 수는 더 많은 독립 원본 업무가 있다는 뜻과 같지 않다. 반복 재생 행 수와 원본 지원을 구분한다.
모델마다 지원 범위가 다르면 지원 표본의 분모도 달라진다.""")
    code("""manifest = report.read(ROOT/'reports/model_research_v14/handoff/manifest.json')
display(pd.DataFrame([{'알고리즘': m['algorithm'], '지원 Adapter': ', '.join(m['expert_adapters']),
    '모델 크기 (MiB)': m['bytes']/1024**2} for m in manifest['models']]).round(2))
display(report.table(s['supported_only']))
display(report.table(s['fallback_only']))""")
    md("""## 9. 지속 장애는 사건 탐지율·지연도 읽자

사건 Recall은 이상 구간 중 한 번이라도 탐지한 사례 비율이다.
그 사건의 모든 분을 탐지한 것으로 바꾸는 **point adjustment는 하지 않았다**.
첫 탐지 지연의 중앙값은 **탐지된 사건만** 포함한다. 미탐 사건을 0초로 처리하지 않는다.
한 분의 작은 오탐률도 긴 정상 사건에서는 한 번 이상 경보가 나는 비율이 커질 수 있다.""")
    code("""display(report.event_table())
display(pd.DataFrame([{'시나리오': report.TASKS[d['dataset']], **d['events']['normal_case_false_alarm_rate']} for d in s['datasets']]).round(4))""")
    md("""## 10. 새 확인 사례 — 튜닝에 쓰지 않은 결과

확인 사례는 모델·threshold를 정한 후 처음 평가한다. 확인 성능이 탐색보다 낮다면 일반화 문제가 남아 있는 것이다.
같은 원본 pool/생성기를 공유하므로 새로운 실제 운영 기간을 대신하지 못한다.""")
    code("""v = report.read(ROOT/'reports/model_research_v14/validation_results.json')
display(report.read(ROOT/'reports/model_research_v14/case_partition_audit.json'))
display(pd.concat([report.table(v[a]['confirmation']['pooled'], offset=i*3).assign(알고리즘=a)
    for i,a in enumerate(('if','kmeans','lof'))], ignore_index=True))""")
    md("""## 11. 무엇을 시도했고 무엇을 채택하지 않았나?

1. 정상 train의 업무·처리량 기준 median/MAD를 고정한 누적 양의 편차·EWMA·지속 비율 피처.
   최근 기준이 이상을 따라 움직이는 위험을 줄이려는 실험이다. 정확한 CUSUM 제어도나 새 하드 룰은 아니다.
2. 중복 정상 벡터 제거, LOF k/거리/reference 한도 변경.
3. 기존 학습 범위의 서로 다른 원본 정상 템플릿으로 60분 스트림 다양성을 추가.
   새로 늘어난 창이 독립 원본 거래인 것은 아니다. 검증 템플릿을 fit에 넣지 않았다.
4. 독립 정상 사례로 단계/관측 여부별 threshold 보정.
5. IF max_samples·회전, K-means K·거리 공분산 후보.
   회전·군집 거리 변경은 이전에도 시도한 항목이며 이번에 처음 고려했다고 주장하지 않는다.

v13 실패 실험과 v14 탈락 후보를 남겼다. 새 버전이라는 이유로 F1이 낮아진 모델을 배포하지 않는다.""")
    code("""display(report.read(ROOT/'reports/model_research_v14/normal_diversity.json'))
search = report.read(ROOT/'reports/model_research_v14/search_results.json')
display(pd.DataFrame([{'알고리즘': r['algorithm'], '후보': r['candidate'], '문맥': r.get('mode'),
    '탐색 macro F1 (%)': r['objective']*100, '최대 경로 FPR (%)': r['worst_pooled_fpr']*100,
    '경보 예산 충족': r.get('within_budget')} for r in search if 'route_search' not in r]).round(2))""")
    md("""## 12. 논문·저널 근거와 이번 구현 범위

| 자료 | 참고한 내용 | 실제 적용 / 미적용 |
|---|---|---|
| [Breunig 등, LOF: Identifying Density-Based Local Outliers, SIGMOD 2000](https://sigmodrecord.org/?download_id=6631&smd_process_download=1) | 국소 이웃의 밀도 비교 | sklearn novelty LOF 구현. 중복 제거·reference cap은 이번 데이터에 대한 엔지니어링 선택이지 논문의 필수 설정이 아님 |
| [Hariri 등, Extended Isolation Forest, IEEE TKDE 2021](https://arxiv.org/abs/1811.02141) | 축 정렬 분할의 편향 | 기존 회전 후보 재검토. random-hyperplane EIF 전체를 구현한 것은 아님 |
| [NIST CUSUM Control Charts](https://www.itl.nist.gov/div898/handbook/pmc/section3/pmc323.htm) | 작은 지속 평균 변화의 누적 | 고정 정상 기준 누적 피처 실험. 제어 한계 보장이 있는 정식 CUSUM 탐지기 아님 |
| [Kim 등, Towards a Rigorous Evaluation of Time-series Anomaly Detection, AAAI 2022](https://arxiv.org/abs/2109.05257) | point adjustment의 과대평가 | 원시 분 단위 지표 보존, PA 미사용. 논문의 다른 탐지 모델은 재현하지 않음 |
| [Tatbul 등, Precision and Recall for Time Series, NeurIPS 2018](https://arxiv.org/abs/1803.03639) | 점 이상과 구간 이상의 평가 차이 | 사건 Recall·지연을 분 지표와 함께 보고. 논문의 range precision/recall 전체 공식을 재현하지 않음 |
| [Schmidl 등, Anomaly Detection in Time Series: A Comprehensive Evaluation, PVLDB 2022](https://timeeval.github.io/evaluation-paper/) | 알고리즘·이상 종류별 성능 차이 | 시나리오별 성능/추론 비용 분리. TimeEval 전체 벤치마크 재현은 아님 |

## 13. 남은 문제와 서비스 연결

데이터 누적·빈 시간창·지연 도착·신규 업무·실제 계절성·드리프트를 backend에서 관리해야 한다.
Process 파생 연속 분 grid와 완료 Process 로그가 입력이고 Transaction 테이블은 필요 없다.
신뢰된 joblib hash를 확인하고 로드한다. 모델 내부의 합성 prior는 실제 운영 이력으로 검증해야 한다.
이번 실험은 하드 룰을 바꾸거나 자동 배포하지 않았다.

기존 벤치마크 난도 조정 방향과 기존 results.ipynb 수정 대상은 사용자 확인 대기다.
이 파일은 새 실험의 독립 실행 결과이며 기존 노트북을 덮어쓰지 않았다.""")
    code("""display(pd.DataFrame([{'알고리즘': m['algorithm'], '파일': m['file'], '크기 bytes': m['bytes'], 'sha256': m['sha256']}
    for m in manifest['models']]))
display(manifest['dependencies'])
display(pd.DataFrame([{'알고리즘': a, **d['score_seconds']} for a, d in [(d['dataset'], d) for d in s['datasets']]]))""")
    md("""## 14. 서로 다른 이상을 실제로 잡았나?

한 알고리즘만 탐지한 이상 분과 한 알고리즘만 낸 오탐을 함께 센다.
다른 모델이 놓친 이상을 LOF가 찾더라도 추가 오탐이 더 많을 수 있다.
그래서 무조건 세 모델의 경보를 OR해서 합치는 것은 개선으로 보장되지 않는다.""")
    code("""complement = report.read(report.location()/'complementarity.json')
display(pd.DataFrame(complement['records']))
display(complement['all_three_missed_anomaly_minutes'])""")
    nb = nbf.v4.new_notebook(cells=cells, metadata={"kernelspec": {"display_name": "Python (.analysis-venv)", "language": "python", "name": "python3"}})
    nbf.write(nb, HERE / "result.ipynb")


def execute():
    path = HERE / "result.ipynb"; nb = nbf.read(path, as_version=4)
    km = KernelManager(kernel_name="python3"); km.kernel_spec.argv = [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"]
    client = NotebookClient(nb, km=km, timeout=600, resources={"metadata": {"path": str(HERE)}},
        on_cell_start=lambda cell, cell_index: print(f"v14 report cell {cell_index}: {cell.cell_type}", flush=True))
    client.execute(cleanup_kc=True); nbf.write(nb, path)
    folder = ROOT / "reports/model_research_v14/figures"; folder.mkdir(exist_ok=True)
    figures = []
    for i, cell in enumerate(nb.cells):
        for output in cell.get("outputs", []):
            png = output.get("data", {}).get("image/png")
            if not png: continue
            if isinstance(png, list): png = "".join(png)
            filename = f"figure_{len(figures)+1:02d}.png"; (folder / filename).write_bytes(base64.b64decode(png))
            figures.append({"cell": i, "file": filename})
    (folder / "manifest.json").write_text(json.dumps(figures, indent=2), encoding="utf-8")
    print(f"Executed {len(nb.cells)} cells, {len(figures)} figures", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--execute", action="store_true"); args = parser.parse_args()
    build()
    if args.execute: execute()
