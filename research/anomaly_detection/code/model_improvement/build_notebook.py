"""Author and execute a split-cell Korean report from frozen result artifacts."""
import argparse
import base64
from pathlib import Path
import sys

import nbformat as nbf
from nbclient import NotebookClient
from jupyter_client import KernelManager

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def build():
    cells = []
    def md(text): cells.append(nbf.v4.new_markdown_cell(text.strip()))
    def code(text): cells.append(nbf.v4.new_code_cell(text.strip()))
    md("""# 모델 개선 결과와 역할 비교

Process 입력을 이용해 **Isolation Forest와 MiniBatch K-means**, 각 모델의 **전체·Adapter·앙상블** 6개 전략을 비교한다.
기존 모델/데이터는 보존했다. 이 노트북은 저장된 평가 결과를 읽으며 학습이나 threshold 재선택을 하지 않는다.

먼저 전체 결과 → 개선 전후 → 검증 → 모델별 역할 → Adapter 효과 → 분포/판단 근거 → 남은 한계 순으로 읽으면 된다.
주요 지표는 Precision, Recall, F1와 정상 오탐률이다. Accuracy만 보면 정상 비중 때문에 성능을 오해할 수 있다.""")
    code("""from pathlib import Path
import sys, json
import numpy as np
import pandas as pd
from IPython.display import display, Markdown
import matplotlib.pyplot as plt
HERE = Path.cwd()
ROOT = next(p for p in [HERE, *HERE.parents] if (p/'code/model_improvement/report.py').exists())
sys.path.insert(0, str(ROOT/'code'))
from model_improvement import report
from model_improvement.refine import OUTPUT, ALL_FEATURES
from model_evaluation.models import METHODS
from model_improvement.features import DESCRIPTIONS
report.setup()
r = report.read_results()
s, selection, prep, validation, roles = (r[k] for k in ('summary','selection','preparation','validation_results','role_analysis'))
pd.set_option('display.max_colwidth', 90)
pd.set_option('display.max_columns', 15)
""")
    md("""## 1. 전체 평가 결과

6개 기본 시나리오는 각각 이상 사례 5,000개와 짝지은 정상 사례 5,000개, 혼합 Hard는 1,000+1,000개다.
아래는 모든 시나리오의 **1분 의사결정 행을 합산한 수치**다. Process 행 분류 F1와 다른 단위다.
동일한 원본 라벨/의사결정 수를 유지했으며, 한 번 탐지한 이벤트 전체를 정답으로 바꾸는 point adjustment는 하지 않았다.""")
    code("""display(report.metrics_table(s['pooled']))
first = s['pooled'][0]
display(Markdown(f"평가 의사결정 **{first['n']:,}개**: 정상 {first['normal']:,}, 이상 {first['anomaly']:,}. "
                 f"전부 정상으로 예측해도 Accuracy는 **{first['always_normal_accuracy']:.2%}**다."))
report.metric_bars(s); plt.show()""")
    md("""### 결과 해석

F1 개선을 정상 오탐 증가와 함께 읽는다. Recall 상승만으로 개선이라고 확정하지 않는다.
행 수가 긴 점진적 악화/입력 중단이 합산 성능에 더 큰 영향을 준다. 아래 시나리오별 수치와 macro 평균도 함께 확인한다.

**현재 남은 큰 문제:** 개별 지연·컬럼 관계 탐지가 약하다. 선택 IF 앙상블의 정상 오탐은 두 시나리오에서 각각 약 44.9%, 38.4%다.
합산 정상 오탐 4.22%만 보고 모든 업무/시나리오에서 안정적이라고 해석하면 안 된다. 기존 운영 모델을 교체하지 않은 이유다.""")
    code("""report.scenario_bars(s); plt.show()
display(report.metrics_table(s['basic_macro']))
rows=[]
for d in s['datasets']:
    for metric in d['metrics']:
        rows.append({'시나리오': report.SCENARIO_NAMES[d['dataset']], '모델': report.LABELS[list(METHODS).index(metric['method'])],
                     'F1 (%)': metric['f1']*100, 'Recall (%)': metric['recall']*100,
                     '오탐 (%)': metric['normal_false_positive_rate']*100})
display(pd.DataFrame(rows).round(2))""")
    md("""## 2. 기존 5분 모델보다 무엇이 바뀌었나

아래 비교는 같은 평가셋, 같은 1분 라벨/의사결정 분모다. 피처, 일부 모델 설정, threshold, 앙상블 가중치가 바뀌었다.
최종 평가 성능을 보고 파라미터를 선택하지 않았다. 이 평가셋 자체는 과거에도 사용했으므로 새로운 블라인드 외부 검증은 아니다.""")
    code("""comparison=report.baseline_comparison(s)
display(comparison)
for i,row in comparison.iterrows():
    display(Markdown(f"- {i}: F1 {row['기존 F1 (%)']:.2f}% → {row['개선 F1 (%)']:.2f}% "
                     f"({row['F1 변화 (%p)']:+.2f}%p), 정상 오탐 {row['기존 오탐 (%)']:.2f}% → {row['개선 오탐 (%)']:.2f}%."))""")
    md("""## 3. 튜닝 절차와 확인용 집단

학습·점수 보정·threshold 기준은 서로 다른 정상 원본 실행/재생 에피소드다. 학습 원본은 전체 371만 행이 아니라 기존 정상 샘플 아카이브다.
정상 참고 집단에서 제외한 원본 템플릿으로 **새 검증 사례 1,440개**를 생성했다. 짝지은 정상 사례도 1,440개다.
탐색 720개·확인 720개로 나누고 동일 사례의 모든 분/정상 짝을 같은 집단에 둔다. 두 집단은 원본 템플릿 풀과 생성기를 공유한다.

트리/군집은 정상 데이터만 학습한다. 주입 라벨로 피처·하이퍼파라미터·운영점을 고르는 과정은 **라벨을 이용한 모델 선택**이며 전체 절차를 완전 비지도라고 부를 수는 없다.
목표는 6개 기본 시나리오 F1의 동일 가중 평균을 3개 전략에 다시 평균한 값이다. 각 전략의 **합산 정상 오탐률 5% 이하**인 후보만 선택한다.
확인용 결과는 설정을 확정한 뒤 1회 계산했으며 재선택에 사용하지 않았다. 확인 집단에서 오탐 제한을 넘으면 그 사실을 숨기지 않는다.""")
    code("""display(pd.DataFrame([{'집단':k,'의사결정 수':v} for k,v in prep['normal_reference_counts'].items()]))
display(pd.DataFrame(prep['normal_reference_episodes']))
report.confirmation_bars(validation); plt.show()
display(report.metrics_table(validation['features_tuned']['confirmation']['pooled']))
display(Markdown('**확인용 검증 제한:** ' + ', '.join(
    f"{report.LABELS[i]} 오탐 {x['normal_false_positive_rate']:.2%}" for i,x in enumerate(validation['features_tuned']['confirmation']['pooled'])
    if x['normal_false_positive_rate'] > .05) if any(x['normal_false_positive_rate']>.05 for x in validation['features_tuned']['confirmation']['pooled'])
    else '확인용 집단에서 여섯 전략 모두 합산 정상 오탐 5% 이하. 운영 환경 보장은 아님.'))""")
    md("""### 첫 실험에서 확인한 실패도 보존

v3는 기존 12개 피처를 여러 새 피처로 통째로 교체했다. IF 지연 탐지가 약해졌고 일부 Clustering Adapter의 오탐이 늘었다.
v4는 기존 피처를 후보로 유지하고 관계·시계열·축소 피처를 비교한다. 새 seed의 검증/확인 사례를 쓴다.
v3는 시나리오 macro 정상 오탐 2.5% 제한, v4는 합산 정상 오탐 5% 제한으로 목표도 다르다. 동일한 조건의 반복 실험이라고 주장하지 않는다.""")
    code("""old_validation=json.loads((ROOT/'reports/model_improvement_v3/validation_results.json').read_text(encoding='utf-8'))
display(report.metrics_table(old_validation['features_tuned']['confirmation']['pooled']))""")
    md("""## 4. 실제 선택된 피처와 하이퍼파라미터

각 알고리즘에 피처 뷰 4개×모델 설정 3개=12개 모델 후보, 모델당 quantile 4개×전체 가중치 3개=12개 운영점을 동일하게 탐색했다.
IF는 128개 트리를 유지하고 max_samples=256/512/1024를 비교했다. v3에서는 256개 트리와 max_features=0.7도 비교했다.
Clustering은 K=6/12, 원거리/군집별 반경 보정 거리를 비교한다. batch_size=2048, n_init=5, max_iter=100, seed=42다.
신규 Adapter에는 전문 모델을 만들었다고 가정하지 않고 통합 모델로 fallback한다.""")
    code("""from model_improvement.refine import VIEWS
params=[]
for a,c in selection['families'].items():
    params.append({'모델':a,'선택 피처 뷰':c['view'],'피처 개수':len(VIEWS[c['view']]),
                   'trees':c['settings']['trees'] if a=='if' else None,
                   'max_samples':c['settings']['samples'] if a=='if' else None,
                   'K':c['settings']['k'] if a=='kmeans' else None,
                   '군집 반경 보정':c['settings']['radius_normalization'] if a=='kmeans' else None,
                   '정상 quantile':c['quantile'],'전체 가중치':c['global_weight'],'탐색 오탐 제한 만족':c['within_fpr_budget']})
display(pd.DataFrame(params))
for a,c in selection['families'].items(): display(Markdown(f"**{a} 입력:** `"+'`, `'.join(VIEWS[c['view']])+"`"))
display(pd.DataFrame([{'피처 계열':k,'의미':v} for k,v in DESCRIPTIONS.items()]))""")
    md("""### 피처의 근거와 한계

1·5·15분을 동시에 보면 짧은 급변과 지속적 변화가 함께 드러난다. 모델이 실제로 선택한 피처만 사용하며, 준비된 모든 피처를 모델이 본다고 해석하면 안 된다.
조건부 지연은 **Adapter+ProcessID+처리건수(log2 구간)** 정상 중앙값/IQR을 사용한다. 원본 학습 30행 미만이면 Adapter+건수 → Adapter+ProcessID → Adapter → 전체로 내려간다.
희소 집단의 참고값은 부정확할 수 있다. 작은 IQR은 최소 0.15로 제한한다. ProcessID는 참고값 조회에만 쓰고 ID 자체를 벡터에 넣지 않는다.
EWMA alpha=0.2는 변화 이력을 피처화하기 위한 고정 값이다. 정규분포 가정의 3-sigma 제어 한계를 적용한 것은 아니다.""")
    md("""## 5. Threshold는 어디서 나오나

각 모델의 별도 정상 threshold 참고 집단 점수에서 선택 quantile을 취한다. sklearn의 `predict(-1)`/contamination을 그대로 쓰지 않는다.
IF 원점수는 `-score_samples`라 높을수록 희귀하다. Clustering 원점수는 가장 가까운 정상 군집 중심까지의 거리이며 선택된 경우 해당 군집 정상 P95 반경으로 나눈다.
점수 보정은 정상 calibration의 중앙값과 (P95−중앙값)으로 한다. 앙상블은 보정된 전체·Adapter 점수를 선택 가중치로 결합하고 **결합 점수 자체의 threshold도 정상 집단에서 따로 계산**한다.

최종 margin = 보정 점수 − 해당 경로 threshold. **0 초과만 경보**, 0 이하는 정상 판정이다. margin 크기가 모델 간 공통 확률은 아니다.""")
    code("""thresholds=pd.DataFrame(s['thresholds'])
display(thresholds[['algorithm','route','quantile','global_weight','raw_threshold','normalized_threshold','ensemble_threshold','effective_k']].round(5))
report.score_histograms('mixed_hard'); plt.show()""")
    md("""### 히스토그램 읽는 법

일반 막대가 정상·이상 점수의 분포다. 붉은 점선 오른쪽 이상 막대는 TP, 오른쪽 정상 막대는 FP다. 왼쪽 이상 막대는 놓친 이상이다.
가독성을 위해 합산 점수 P0.5~P99.5 범위로 그림만 제한하고 그림 밖 수를 각 패널에 표기했다. 지표 계산은 전체 행을 사용한다.
막대 높이는 표시 구간만 재정규화하지 않고 각 집단 전체 행 수 대비 비중이다. 정상과 이상이 겹친다면 단일 threshold로 완벽하게 나누기 어렵다.""")
    code("""report.input_histograms(); plt.show()""")
    md("""## 6. IF와 Clustering은 실제로 무엇이 다른가

IF는 여러 피처 조합이 정상 데이터에서 얼마나 쉽게 분리되는지 본다. Clustering은 어느 정상 패턴(중심)에 가장 가깝고 그 패턴에서 얼마나 떨어졌는지 본다.
아래는 **두 알고리즘의 앙상블끼리** 비교한다. 'IF만 탐지'와 'Clustering만 탐지'가 실제 보완 영역이다.
정상 구간에도 같은 그림을 보이며, 정상에서만 늘어난 단독 경보를 장점으로 해석하지 않는다. 단순 OR 결합 모델을 새로 운영한 결과가 아니다.""")
    code("""report.overlap_bars(roles); plt.show()""")
    md("""### 정상 오탐 예산을 맞춘 보조 비교

서로 다른 운영점 때문에 역할 차이가 생기는지 점검한다. **확인용 정상 짝의 점수만** 사용해 각 전략을 오탐 1%에 맞춘 별도 진단 경계를 만들었다.
이 경계로 최종 평가 결과를 다시 비교하지만 모델/선택 threshold를 변경하지 않는다. 다른 분포의 최종 정상 로그에서도 정확히 1%를 보장하는 것은 아니다.""")
    code("""display(report.metrics_table(roles['matched_budget_pooled']))
report.overlap_bars(roles, matched=True); plt.show()
display(pd.DataFrame(roles['paired_bootstrap']).round(4))""")
    md("""95% 구간은 원본/정상 짝을 묶은 사례 단위로 시나리오별 재표본추출 400회에서 계산했다. 0을 포함하면 이 샘플링 수준에서 우열이 분명하지 않다.
같은 원본 템플릿 반복, 학습 seed 변동, 실제 운영 환경 차이를 모두 반영하는 신뢰구간은 아니다.""")
    md("""### 피처 차이와 알고리즘 차이 구분

최종 IF와 Clustering은 선택 피처가 다르다. 앞의 차이는 알고리즘뿐 아니라 **피처·설정·운영점 차이가 함께 만든 결과**다.
따라서 동일한 관계 피처 16개, 기본 모델 설정, 동일 정상 참고 집단으로 두 모델을 다시 비교하는 통제 실험을 추가했다.
확인용 정상 점수로 오탐 예산을 맞춘 진단이며 최종 라벨로 새 모델을 선택하거나 승격한 것이 아니다.
역할 결론은 이번 구성/데이터에서 관측된 범위에 한정한다.""")
    code("""display(report.metrics_table(roles['common_view_diagnostic']['matched_pooled']))
common_rows=[]
for d in roles['datasets']:
    for i in (2,5):
        m=d['common_view_matched_metrics'][i]
        common_rows.append({'시나리오':report.SCENARIO_NAMES[d['dataset']], '모델':report.LABELS[i],
                            '같은 피처 F1 (%)':m['f1']*100,'같은 피처 Recall (%)':m['recall']*100,
                            '같은 피처 오탐 (%)':m['normal_false_positive_rate']*100})
display(pd.DataFrame(common_rows).round(2))""")
    md("""## 7. 전체 모델과 Adapter 모델의 역할

전체 모델은 폭넓은 정상 패턴과 새로운 Adapter fallback을 담당한다. Adapter 모델은 같은 역할 집단의 정상 패턴만 학습하므로 전체에서 묻히는 이상을 찾을 수 있다.
반대로 Adapter 학습 집단이 좁으면 다른 정상 Process의 패턴도 이상으로 볼 수 있다. 전문 모델이 항상 더 좋거나 앙상블이 반드시 이기는 것은 아니다.
아래는 **동일한 전문 모델 지원 집단에서만** 세 전략을 비교하고, Adapter별 결과도 분리한다.""")
    code("""support=pd.DataFrame(s['support'])
display(support[['adapter','train','calibration','threshold','expert_available','reason']])
display(report.supported_table(s))
report.adapter_bars(roles); plt.show()
coverage=sum(d['coverage']*(2*d['metrics'][0]['n']/2) for d in s['datasets'])/sum(d['metrics'][0]['n'] for d in s['datasets'])
display(Markdown(f"평가 행의 전문 모델 지원 비중: **{coverage:.2%}**. 미지원에서는 세 전략이 같은 전체 모델 점수로 처리된다."))""")
    md("""## 8. Clustering 정상 패턴과 탐지 근거

군집은 실패 유형 정답이 아니라 정상 학습 로그의 대표 패턴이다. cluster 번호 순서는 의미/심각도 순위가 아니다.
반경은 정상 calibration에서 해당 중심에 가까운 로그들의 P95 거리다. 30행 미만이면 전체 반경을 쓴다. CBLOF를 그대로 구현한 것이 아니다.""")
    code("""centers=[]
for c in roles['clusters']:
    selected_features={k:c['features'].get(k) for k in ('log_arrivals','arrival_deviation','p95_duration_relative','zero_run','residual_change')}
    centers.append({'경로':c['route'],'군집':c['cluster'],'학습 창':c['train_windows'],
                     'P95 반경':c['radius_p95'],**selected_features})
center_frame=pd.DataFrame(centers).rename(columns={'log_arrivals':'요청 수(log1p)','arrival_deviation':'5분 요청 편차',
    'p95_duration_relative':'기존 집단 대비 P95 지연','zero_run':'연속 요청 없음(log1p)','residual_change':'지연 변화'})
for route,table in center_frame.groupby('경로',sort=False):
    display(Markdown(f'### {route} 정상 중심'))
    display(table.drop(columns='경로').round(3))""")
    md("""### 실제 탐지·미탐지·오탐 사례

각 평가셋/범주에서 첫 번째 해당 행을 사용했다. 최고 점수/성공한 사례만 골라 보여주지 않는다.
IF: 한 피처를 학습 중앙값으로 바꿨을 때 점수가 얼마나 낮아지는지 확인한 **민감도**다. SHAP/인과적 원인 분석은 아니다.
Clustering: 가장 가까운 중심까지의 **제곱 거리 기여 비중**이다. 운영 장애의 원인을 확정하는 설명은 아니다.
아래 정리에서 margin이 양수면 경보이며, 설명의 상위 피처가 실제 관측 변화와 맞는지 확인한다.""")
    code("""example_rows=[]
for e in roles['examples']:
    for detail in e['explanations']:
        top=detail['top_features'][:3]
        text=', '.join(f"{x['feature']} ({x.get('score_drop',x.get('distance_share')):.3f})" for x in top)
        example_rows.append({'평가셋':report.SCENARIO_NAMES[e['scenario']],'범주':e['category'],'사례':e['case_index'],
                              'Adapter':e['adapter'],'모델':detail['algorithm'],'경로':detail['route'],
                              'IF 앙상블 margin':e['margins']['if_ensemble'],'Clustering 앙상블 margin':e['margins']['kmeans_ensemble'],
                              '주요 피처 (IF=점수 감소, K=거리 비중)':text})
example_frame=pd.DataFrame(example_rows).round(4)
for dataset,table in example_frame.groupby('평가셋',sort=False):
    display(Markdown(f'### {dataset} 판단 근거'))
    display(table.drop(columns='평가셋'))""")
    md("""## 9. 시계열에서 경보가 언제 나오나

사례 0을 고정해 요청 중단과 점진적 악화를 보여준다. 음영은 주입 정답 구간, 점선은 모델별 경계다.
긴 창에서는 장애가 끝난 뒤에도 이전 이상 로그가 남아 경보가 지속될 수 있다. 이 경보는 원래 분 단위 라벨을 바꾸지 않으므로 FP가 될 수 있다.""")
    code("""report.timeline('input_stop',0); plt.show()
report.timeline('gradual_degradation',0); plt.show()""")
    md("""## 10. 이벤트 단위 결과와 Hard 한계

Event Recall은 주입 구간 중 한 번 이상 탐지한 이벤트 비중이다. 앞의 분 단위 Recall과 다르다.
Hard에서 6가지 중 일부만 찾은 것과 **6가지 모두 찾은 것**을 분리한다. 사례에 한 번만 경보가 떠도 모든 이상을 찾았다고 부풀리지 않는다.""")
    code("""display(report.event_table(s))
hard=next(d for d in s['datasets'] if d['dataset']=='mixed_hard')
display(pd.DataFrame([{'모델':report.LABELS[i], 'Hard 6종 모두 탐지 (%)':hard['events']['all_six_recall'][m]*100,
                       '정상 사례에서 한번 이상 경보 (%)':hard['events']['normal_case_false_alarm_rate'][m]*100}
                      for i,m in enumerate(METHODS)]).round(2))""")
    md("""## 11. 구현과 다음 판단

새 후보 모델은 `reports/model_improvement_v4/models_v4.joblib`에 피처 인코더·조건부 정상 참고값·전체/Adapter 모델·보정/threshold를 함께 저장했다.
기존 `process_if_v1.joblib`와 기존 v2 모델은 자동 교체하지 않았다. 운영 모델 선택은 확인 집단·실제 로그 오탐 검증 후 결정해야 한다.
`inference.py`는 hash로 검증한 모델을 불러오는 backend 연결 인터페이스다. 기존 하드 룰을 선행하고, Process를 집단별 상태/분 단위 창으로 만든 뒤 모델을 호출한다.
입력 중단은 로그가 안 들어와도 실행되는 스케줄러, 정상 수집기 상태 확인, 업무별 기대 트래픽이 필요하다.

아래 결과는 합성 벤치마크에서의 개선/역할 차이다. 실제 정상 시간대 이력, 더 다양한 원본 템플릿, 미지 Adapter 홀드아웃, 실장애 정답으로 재검증해야 한다.
모델은 STATUS=S 정상 후보에 맞춘 것이며 H/Z/N/P 정상 여부를 이 결과로 확정할 수 없다.""")
    code("""display(Markdown('\\n'.join('- '+x for x in s['protocol']['limitations'])))
display(Markdown(f"선택 모델 크기: **{(OUTPUT/'models_v4.joblib').stat().st_size/1024/1024:.2f} MiB**. "
                 f"전문 모델 수: 알고리즘별 **{sum(x['expert_available'] for x in s['support'])}개**, 전체 모델 1개씩."))""")
    md("""## 참고 논문·문서와 적용 범위

| 자료 | 이번 구현에 적용한 내용 | 적용하지 않은 내용 |
|---|---|---|
| [Liu, Ting, Zhou (2008), Isolation Forest](https://cs.nju.edu.cn/zhouzh/zhouzh.files/publication/icdm08b.pdf) | 짧은 분리 경로 기반 희귀성, 작은 subsample 크기 비교 | 논문의 성능 수치를 우리 데이터 성능으로 재사용하지 않음 |
| [He, Xu, Deng (2003), Discovering cluster-based local outliers](https://www.sciencedirect.com/science/article/pii/S0167865503000035) | 지역 정상 집단을 고려하는 관점의 참고 | CBLOF 알고리즘/작은 군집 가중치를 그대로 구현하지 않음; 자체 P95 반경 보정 실험 |
| [Kim et al. (ICLR 2022), Towards a Rigorous Evaluation of Time-series Anomaly Detection](https://arxiv.org/abs/2109.05257) | point adjustment 미사용, 분/이벤트 지표 구분 | 단순 탐지 한 번으로 이상 구간 전체를 정답 처리하지 않음 |
| [NIST, EWMA Control Charts](https://www.itl.nist.gov/div898/handbook/pmc/section3/pmc324.htm) | 오래된 관측에 감소 가중치를 두는 변화 추세 피처 | 정규분포·독립 관측 가정의 제어 한계는 적용하지 않음 |
| [scikit-learn, MiniBatchKMeans](https://scikit-learn.org/stable/modules/generated/sklearn.cluster.MiniBatchKMeans.html) | 미니배치 정상 중심 학습, K/초기화 설정 | 중심 거리가 이상확률이라는 주장은 하지 않음 |

피처 뷰, 1/5/15분 조합, count-bin fallback, ensemble 가중치와 정상 오탐 예산은 **이 데이터에 대한 실험 설계**다. 논문이 그 값들을 최적이라고 보증한 것은 아니다.""")
    nb = nbf.v4.new_notebook(cells=cells, metadata={"kernelspec": {"display_name": "Python (.analysis-venv)", "language": "python", "name": "python3"}})
    nbf.write(nb, HERE/"results.ipynb")
    print(f"Authored {len(cells)} readable cells", flush=True)


def execute():
    path = HERE/"results.ipynb"
    nb = nbf.read(path, as_version=4)
    manager = KernelManager(kernel_name="python3")
    manager.kernel_spec.argv = [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"]
    client = NotebookClient(nb, km=manager, timeout=600, resources={"metadata": {"path": str(HERE)}},
                            on_cell_start=lambda cell, cell_index: print(f"Report cell {cell_index}: {cell.cell_type}", flush=True))
    client.execute(cleanup_kc=True)
    nbf.write(nb, path)
    images = ROOT/"reports/model_improvement_v4/figures"
    images.mkdir(exist_ok=True)
    for i, cell in enumerate(nb.cells):
        for j, output in enumerate(cell.get("outputs", [])):
            png = output.get("data", {}).get("image/png")
            if png:
                if isinstance(png, list): png = "".join(png)
                (images/f"cell_{i:02d}_{j:02d}.png").write_bytes(base64.b64decode(png))
    print("Executed and saved notebook; figures extracted for visual verification", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    build()
    if args.execute: execute()
