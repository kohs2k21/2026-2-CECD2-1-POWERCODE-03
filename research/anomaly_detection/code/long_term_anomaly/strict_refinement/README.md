# 현재 구간만 사용하는 정상 AR 오차 분포 모델

1시간 모드를 개선한 연구 버전이다. 4·8·12·24시간 모델과 예측은 이전 strict_intervals 버전 그대로 유지한다. 어느 모드에서도 이전·다음 감시 배치나 누적 경보 상태를 사용하지 않는다.

## 성능과 목표를 구분

실제 수치는 같은 폴더의 result.ipynb와 reports/long_term_anomaly/strict_refinement/comparison.csv에 있다. 목표는 **각 분석 길이에서 여섯 시나리오별 Accuracy·Precision·Recall·F1 중앙값이 각각 65% 이상**, 전체와 각 시나리오의 오탐률이 6% 이하인 것이다.

results.json의 goal_met_all_lengths가 false이면 전체 목표는 미달이다. 코드 실행·단위 테스트·검증 통과가 성능 목표 달성이라는 뜻은 아니다. 중앙값 조건과 모든 시나리오 각각 65% 조건도 다르며, 모든 시간을 합친 통합 지표 역시 별도로 표시한다.

## 무엇을 학습했는가

정상 train 64개 구간의 앞 672시간만 사용했다. 이는 64개 고유 Adapter라는 뜻이 아니며, 실제 이상 정답을 모델의 정상 분포 학습에 넣지 않았다.

1. 정상 참고 구간의 시간대별 중앙값·완만한 변화·평균·표준편차를 추정한다.
2. 정상 값이 바로 전 정상 값과 얼마나 비슷하게 이어지는지(1차 자기상관)를 계산한다.
3. 그 관계로 설명되지 않는 새 오차 성분인 혁신(innovation)을 정상 학습 구간들에서 모은다.
4. 모은 오차를 재표본하여 자기상관별 정상 흔들림 분포를 추정한다. **이 모의 계산은 모델 내부의 분포 학습이지 평가 데이터셋 생성·수정이 아니다.**
5. 현재 한 점이 정상 분포보다 일반적인 평균 이동·변동성 증가 가설에 더 잘 맞는지 비교한다.

과거 정상 학습 구간을 사용한다는 것과 이전 감시 배치의 상태를 사용한다는 것은 다르다. 혁신·자기상관을 추정하는 1~3번은 정상 참고 자료에서만 한다. 현재 감시값을 판단할 때 직전 감시 로그를 가져오지 않는다.

1시간 모드는 여전히 한 점이다. 점진적 악화의 **현재 수준이 평소 분포와 다른지**는 판단할 수 있지만, 현재 한 시간 입력만으로 긴 증가 추세를 직접 관측한다는 뜻은 아니다.

## 실제 설정

- 정상 학습: train 64개 참고 구간 × 672시간. 오차 성분 42,548개.
- 시간대 기준: 시간대별 중앙값과 전체 중앙값의 차이를 0.5배로 완화.
- 완만한 정상 추세: 정상 일별 중앙값의 선형 추세를 0.5배 반영. 감시 자료로 갱신하지 않음.
- AR(1) 자기상관: 정상 참고 구간의 연속 관측 쌍에서 추정, 0~0.95 범위. 0.05 간격 분포 사이 보간.
- 정상 분포 추정: 독립적인 Monte Carlo 난수로 조건별 160,000개 정상 표본. 평가셋 생성 난수·정답·사건 경계를 사용하지 않음.
- 밀도 평활 폭: 정상 표준편차 대비 0.03 선택.
- 일반 이동 가설: 현재 선택된 상·하위 이동 범위는 정상 표준편차의 1~3배. 변동성 가설도 함께 사용. 특정 시나리오를 알거나 분류하는 규칙이 아님.
- 점수 결합: temperature 약 0.0654의 최대값에 가까운 완화 결합. 설정의 temperature가 실제 결합을 결정하며 mode 문자열은 초기 탐색 설정명이다.
- 가중치·변형 범위·결합 설정: **개발셋 정답으로 선택**. 정상 분포 자체는 정상 학습 자료만 사용.
- threshold: 정상 calibration 분포와 개발셋 각 시나리오의 정상 시간 분포 중 보수적인 기준. 실제 값은 calibration.json에 기록.

가중치는 점수의 배율이며 중요도 백분율이 아니다. 점수도 확률이 아니다. reason은 가장 크게 기여한 통계 가설이며 장애의 확정 원인은 아니다. 학습 밀도 표 범위 밖의 극단값은 희소도 점수가 포화되므로 방향 분류보다 지원 범위 밖 경보로 해석해야 한다.

유한 표본으로 정상 밀도를 추정하므로 정상의 희귀 스파이크에도 큰 점수가 나올 수 있다. 노트북의 첫 점진적 악화 사례에는 정상 시간의 큰 오탐 점수도 표시했다. 점수 크기를 장애 확정도나 업무 영향 크기로 해석하지 않는다.

## 입력과 사용법

원본 Process CSV를 그대로 넣는 인터페이스가 아니다. **한 Adapter+Process 집단의 시간당 집계**를 사용한다. case_id는 집단 식별·정합성 확인용이며 숫자로 인코딩해 모델에 넣지 않는다.

| 컬럼 | 의미 |
|---|---|
| case_id | 동일 Adapter+Process 집단 식별자. 벤치마크에서는 사례 ID |
| timestamp | 시간 집계 시작 시각. 같은 시간대 체계, 오름차순, 한 시간 간격, 중복 없음 |
| log_requests | log1p(그 시간의 Process 실행 수). Transaction 수나 API 요청 수가 아님 |
| log_duration_ms | Process 소요시간마다 log1p를 적용한 뒤 시간 내 평균 |
| log_items_per_process | 처리 건수마다 log1p를 적용한 뒤 평균. 입력 호환용이며 현재 점수에는 사용하지 않음 |
| collection_observed | 수집이 정상적으로 관측되었는지 |
| latency_available | 실행이 있어 소요시간을 측정할 수 있었는지 |

실행 0과 수집 중단은 다르다. 수집 중단은 평가 불가로 유지하고, 실행 0일 때는 소요시간 피처를 비활성화한다. 참고 정상 자료에 6시간 초과 공백이 있으면 학습을 거부한다. 감시 자료를 정상 참고 자료에 자동 편입하지 않는다.

```python
from long_term_anomaly.strict_refinement.model import load_bank

bank = load_bank("reports/long_term_anomaly/strict_refinement/models.joblib")
reference = bank.prepare_reference(normal_history_28days)
result = bank.predict_batch(reference, current_1hour, analysis_hours=1)
```

reference를 메모리에 유지할 수 있다. 포함하는 것은 검토된 정상 참고 통계뿐이다. 1·4·8·12·24시간을 선택할 수 있고, 정확히 H행을 받는다. 결과는 해당 H시간의 집계 완료 뒤 제공된다. 4시간 이상은 배치 전체 점수이므로 장애 시작 시각의 정확한 위치 추정으로 해석하지 않는다.

```powershell
$env:PYTHONPATH = 'code'
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.strict_refinement `
  --model reports/long_term_anomaly/strict_refinement/models.joblib `
  --input input.csv --output predictions.csv --analysis-hours 1 --split-batches
```

CSV는 집단마다 처음 672행이 정상 참고 자료, 나머지가 감시 자료다. 여러 배치를 한 파일로 재생할 때만 --split-batches를 사용한다. 남는 행을 버리지 않고 오류로 처리하며 기존 출력도 덮어쓰지 않는다.

backend에는 joblib 파일과 code/long_term_anomaly 패키지 및 해당 requirements.txt 환경이 필요하다. joblib에는 Python 클래스가 포함되므로 파일 하나만 복사하고 코드 경로를 없애면 로드되지 않는다. 신뢰할 수 있는 joblib만 로드한다. 결과표·탐색 캐시·평가 정답은 추론에 필요하지 않다. 정상 참고 자료는 사용 시 별도 제공해야 한다.

## 검증과 한계

- 기존 반합성 장기 평가셋을 재사용한 연구 비교. 새 독립 블라인드 평가가 아님.
- 평가셋 강도·정답·시나리오·원본 기간 데이터 변경 없음. 모든 원래 관측 시간을 평가.
- 원래 시간별 정답 유지. 배치 정답 확장·point adjustment·어려운 task 제외 없음.
- 4·8·12·24시간의 모든 저장 점수가 기존 모델과 같은지 대조.
- 30개 전체 backend API 재생과 독립 혼동행렬 계산으로 수치·평가 불가 처리·목표 조건 검증.
- 자기상관·혁신 분포 공유는 모델 가정이다. 평가셋의 정상 연속성도 AR형으로 생성되어 이 모델에 유리할 수 있으므로 실제 독립 시계열 검증이 필요.
- STATUS=S는 실제 운영 정상의 보장이 아니다. 운영 정상 참고 구간은 별도 검토 필요.
- 새로운 집단도 즉시 충분한 정상 참고 자료가 생긴다는 보장은 없다. 이 연구 API는 검토된 정상 672시간을 요구한다.

```powershell
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.strict_refinement.run fit
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.strict_refinement.run evaluate
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.strict_refinement.validate
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.strict_refinement.report
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.strict_refinement.notebook
```

보정·평가 완료 후 조용히 재튜닝하거나 결과를 덮어쓰는 실행은 거부한다. 추가 개선은 새 실험으로 기록해야 한다.

### 추가 개발셋 탐색 — 최종 모델에 미반영

정상 변동 폭을 집단 간 함께 추정하는 방법, 시간대·추세·자기상관 보정, 집단별 점수 보정, 정상 관측 분포 직접 학습, 한 시간 안의 실행 횟수·소요시간 공동 신호를 비교했다. 추가 후보는 모두 개발셋에서만 선택했고 테스트를 새로 열거나 기존 모델을 덮어쓰지 않았다. 분포 학습에는 정상 학습 참고 구간만 사용했다. 후보 가중치·설정 선택에는 개발셋 정답을 사용하므로 전체 과정이 완전한 무감독 설정 선택인 것은 아니다.

추가로 작은 이동·변동 가설을 세분화하고, 정상 분위수에 맞춰 분포를 보정하고, 정상 AR 피처와 인공 잡음을 구분하는 자기지도 후보도 비교했다. 현재 추가 후보 중 최고 개발셋 Recall 중앙값은 64.19%, 해당 후보 F1 중앙값은 67.00%였다. 개발셋에서도 Recall 목표에 못 미쳐 현재 저장 모델에 반영하지 않았다. 개발셋 후보 수치를 앞의 최종 테스트 성능으로 인용하면 안 된다. 후보별 상세 값은 노트북의 마지막 비교표와 `*_search.json`에 남겼다.

자기지도 후보는 정상 train의 앞 672시간을 정상 참고 자료로, 뒤 336시간의 정상 관측을 학습 질의로 사용하고 5가지 일반 변형을 무작위 조합한 8회 복제본을 만들었다. 기존 최종 모델의 정상 학습 범위와 다르며 평가셋 생성·수정이 아니다. [NCE의 데이터·잡음 구분 원리](https://proceedings.mlr.press/v9/gutmann10a.html)를 참고한 변형이며 논문의 밀도 추정량·이론 보장을 그대로 구현한 것은 아니다. 네 후보 모두 개발셋 성능이 더 낮아 배포 경로에 포함하지 않았다. `ar_contrastive_candidate_*.joblib`는 실험용 파일이며 최종 모델 파일이 아니다.

별도로 개발셋 정답을 학습하는 보조 분류기를 4개 폴드로 진단했다. 같은 정상 참고 자료를 공유하는 정상·이상 쌍은 반드시 같은 폴드에 넣었다. 이 분류기는 **지도학습 진단 전용**이고 비지도 모델의 성능이 아니며 최종 모델에 포함하지 않았다. 정답을 학습한 이 후보조차 더 좋지 않았지만 모든 가능한 모델의 성능 한계를 증명하는 것은 아니다. 진단 세부 내용은 `point_supervised_diagnostic.json`에 별도로 기록했다.

현재 평가셋은 한 시간당 한 행이므로, 1시간 입력에는 관측점이 하나뿐이다. 이 조건에서 장기간의 실제 증가 추세를 관측했다고 주장하지 않는다. 더 촘촘한 평가 자료를 별도로 추가하는 방안은 사용자 답변 전까지 진행하지 않았으며, 기존 자료·정답·난도는 그대로 보존했다.

추가 정상 시간 관계 실험에서는 과거 1·2·4·8시점까지의 관계를 정상 참고 구간에서만 학습하고, 해당 모델의 정상 오차 재표본으로 현재 한 점의 비교 분포를 만들었다. 정규화 강도·평활 폭·가중치를 개발셋에서 비교했으며 최고 Recall 중앙값은 64.16%, F1 중앙값은 66.59%였다. 원 논문의 증가하는 AR 차수에 대한 이론 보장을 그대로 적용한 것은 아니다. 탐지 시 과거 감시 시점을 추가로 읽지 않았고 최종 모델에도 반영하지 않았다.

정상 집단별 경보 기준 회귀 보정은 정상 train 64개 구간의 앞 672시간 통계만 입력으로, 뒤 336시간 정상 경보 점수의 상위 분위수를 추정했다. 정상 분위수·자기상관·변동 폭·추세 등의 통계만 회귀 입력이며 현재 감시값·시나리오·ID는 사용하지 않았다. 최고 개발셋 Recall 중앙값은 63.78%, F1 중앙값은 66.31%였다. 이 후보도 미반영이며 `conditional_calibration_candidate_*.joblib`는 최종 모델이 아니다. 별도의 정상 calibration 48개 구간과 개발셋의 정상 시간으로 보수적인 경보 기준을 확인했다. 시계열 의존성 아래 유한 표본의 오탐률이 이론적으로 보장된다는 주장은 하지 않는다.

## 참고한 원리

- [Bühlmann, Sieve Bootstrap for Time Series, Technical Report 431](https://statistics.berkeley.edu/sites/default/files/tech-reports/431.pdf): 자기회귀 근사와 잔차 재표본 원리를 참고. 원 논문은 증가하는 AR 차수를 다루며 여기서는 고정 AR(1)·집단 간 혁신 공유·밀도비 탐지를 적용했으므로 논문 알고리즘·이론 보장을 그대로 재현한 것이 아님.
- [SciPy lfilter](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.lfilter.html): 재표본한 정상 혁신으로 AR 정상 분포를 모의 계산하는 필터.
- [scikit-learn LOF](https://scikit-learn.org/stable/modules/generated/sklearn.neighbors.LocalOutlierFactor.html): 별도 비교 후보에서 novelty=True와 학습 표본의 leave-one-out 계수를 사용. 더 좋지 않아 최종 추론 경로에 포함하지 않음.

모델 내부의 정상 모의 표본, 보조 탐색, 최종 평가셋을 구분한다. 실제 모델 파일은 models.joblib이며, 선택한 설정과 보정 근거는 calibration.json·final_protocol.json에 있다.
