# 선택한 구간만 사용하는 장기 이상 탐지

1·4·8·12·24시간 입력을 따로 보정한 연구 모델이다. 입력 집계 간격은 모두 **1시간**이다.
이전·다음 감시 배치와 누적 경보 상태를 사용하지 않는다. 기존 모델·평가셋은 보존했다.

## 무엇을 바꿨는가

1. 정상 분포의 위쪽·아래쪽 흔들림을 따로 계산한다. 정상 분포가 비대칭인데 편차의 절댓값만 사용하면 한쪽 꼬리 때문에 반대쪽 경보 기준까지 높아질 수 있었다.
2. 과거 정상 28일에서 시간대별 중앙값을 학습한다. 현재 값을 해당 시간대의 정상 기준과 비교한다. 최종 후보는 STL·PELT가 아니라 **시간대 정상분포 + 구간 통계·밀도** 모델이다.
3. 현재 H시간의 평균·중앙값·표준편차를 계산하고, 정상 참고 자료 안의 **같은 H시간 구간**들의 분포와 비교한다. 평균이 조금만 올라가도 여러 관측값이 일관되게 변하면 구간 통계가 달라질 수 있다.
4. 정상 분포에서 얼마나 드문 위치인지도 계산한다. kNN은 이웃 8개, KDE는 정상 표준편차 대비 대역폭 0.1·0.25·0.5를 개발셋에서 비교했다. 실제 길이별 선택·가중치는 저장 모델과 calibration.json에 있다.
5. 요청 수·소요시간의 경보 비중과 길이별 threshold를 따로 보정했다. 정상 참고 통계는 정상 자료만 학습한다. **가중치·설정 선택에는 개발셋 정답을 사용했다.** 테스트 정답으로 설정을 선택하지 않았다.

정상 구간의 평균·중앙값·표준편차마다 상위·하위 편차와 밀도 점수를 만들므로 총 18개 피처다. 모든 시나리오에 같은 길이별 설정을 적용하며, 시나리오 이름으로 모델을 고르지 않는다.

## 중요한 구분

- **정상 학습 참고 자료:** 검토된 과거 정상 672시간. 배치 사이에 유지할 수 있는 유일한 통계다.
- **현재 감시 입력:** 선택한 H시간. 이 안에서만 통계와 점수를 계산한다.
- **이전 감시 배치:** 다음 배치의 통계·예측에 사용하지 않는다. 감시 자료를 정상으로 자동 편입하지도 않는다.
- **점수 범위:** 배치 하나에 점수 하나다. 그 점수를 배치 안의 관측된 각 시간에 제공한다. 장애가 시작된 정확한 시간을 찾아낸다는 뜻이 아니다.
- **평가:** 원래 시간별 정답을 그대로 사용한다. 배치에 이상이 하나라도 있으면 배치 정답 전체를 이상으로 바꾸는 방식이나 point adjustment를 사용하지 않는다.
- **결과 제공 시각:** 마지막 시간의 집계가 끝난 뒤다. H시간을 모으는 대기 시간과 실제 CPU 계산 시간은 다르다.
- **1시간 모드:** 한 점이다. 시간 안의 증가 추세나 장기간 지속성을 직접 관측할 수는 없다. 정상 시간대 분포와 현재 값의 차이를 판단한다.

## 입력 컬럼

| 컬럼 | 의미 |
|---|---|
| case_id | 한 Adapter+Process 집단을 식별하는 값. 벤치마크에서는 사례 ID. 숫자로 인코딩해 피처로 넣지 않음 |
| timestamp | 한 시간 집계의 시작 시각. 같은 시간대 체계로 한 시간 간격·오름차순·중복 없음 |
| log_requests | log1p(시간당 Process 실행 수). API 요청 수나 Transaction 수와 같다는 뜻이 아님 |
| log_duration_ms | 그 시간의 Process 소요시간에 각각 log1p를 적용한 뒤 평균한 값 |
| log_items_per_process | Process별 처리 건수에 log1p를 적용한 뒤 평균한 값. 입력 호환성을 위해 유지하며 **현재 최종 점수에는 사용하지 않음** |
| collection_observed | 수집이 정상적으로 관측된 시간인지 여부 |
| latency_available | 실행이 있어 소요시간을 관측할 수 있었는지 여부 |

실행 수 0과 수집 중단을 구분한다. 수집 중단은 평가 불가(NaN)이며 정상 0으로 바꾸지 않는다. 실행이 없으면 소요시간 피처를 비활성화한다. 참고 정상 구간에 6시간 초과 공백이 있으면 학습을 거부한다. 감시 구간의 결측값은 평균 등의 분모에서 제외하며 다른 배치로 보간하지 않는다.

## 사용법

저장소 루트에서 PYTHONPATH에 code를 추가한다. joblib은 신뢰할 수 있는 파일만 로드한다.

```python
from long_term_anomaly.strict_intervals import load_bank

bank = load_bank("reports/long_term_anomaly/strict_intervals/models.joblib")
reference = bank.prepare_reference(normal_history_28days)
result = bank.predict_batch(reference, current_4hours, analysis_hours=4)
```

동일한 정상 참고 통계는 메모리에 유지할 수 있다. 이 통계가 이전 감시 배치의 상태라는 뜻은 아니다. 동일 집단·시각 체계를 검사하며, 참고 자료 이후 14일 범위를 넘으면 재검토를 요구한다. 업데이트할 때도 사람이 검토한 정상 구간을 다시 제공해야 한다.

CSV 입력은 집단별로 첫 672행이 정상 참고 자료, 나머지가 선택한 H시간이다. 여러 배치를 한 파일로 평가할 때만 --split-batches를 사용한다. 남는 행은 버리지 않고 오류로 처리한다.

```powershell
$env:PYTHONPATH = 'code'
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.strict_intervals `
  --model reports/long_term_anomaly/strict_intervals/models.joblib `
  --input input.csv --output predictions.csv --analysis-hours 4 --split-batches
```

## 결과와 재현

- result.ipynb: 실행된 결과표·일반 막대그래프·해석·한계
- reports/long_term_anomaly/strict_intervals/comparison.csv: 길이별 중앙값과 통합 지표
- scenario_metrics.csv: 여섯 시나리오 × 다섯 길이의 원래 시간별 성능
- calibration.json: 실제 사용한 가중치·threshold·정상 보정·개발셋 성능
- models.joblib: 길이별 설정과 threshold. 집단별 정상 참고 자료는 사용 시 별도 제공
- validation.json: 원본 보존·전체 지표 독립 재계산·API 일치 검증과 **성능 목표 충족 여부를 분리**

```powershell
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.strict_intervals.run fit
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.strict_intervals.run evaluate
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.strict_intervals.validate
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.strict_intervals.report
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.strict_intervals.notebook
./.analysis-venv/Scripts/python.exe -m pytest code/long_term_anomaly/strict_intervals/test_strict.py -q
```

이미 보정·평가한 파일을 조용히 덮어써서 재튜닝하지 못하도록 fit/evaluate에 보호 장치를 두었다. 재현용 설정과 탐색 결과를 남겼으며, 별도의 연구 변경은 새 실험으로 기록해야 한다.

## 평가를 해석할 때

목표는 각 H에서 여섯 시나리오의 Accuracy·Precision·Recall·F1 중앙값 각각 65% 이상, 전체·시나리오별 오탐률 6% 이하다. 이는 모든 시나리오가 각각 65%라는 뜻과 다르다. results.json의 goal_met_all_lengths가 false이면 전체 목표는 미달이다. 파일 생성·테스트 통과는 목표 달성이 아니다.

실제로 이번 모델은 4시간의 지속 입력 감소와 24시간의 일일 패턴 변화에 약하다. 배치 전체의 평균·중앙값을 중시하면 일일 변화가 상쇄될 수 있다. 중앙값 통과만으로 모든 운영 시나리오에 적합하다고 판단하지 않는다. 각 시나리오의 전체 결과를 반드시 함께 본다.

기존에 공개한 반합성 장기 벤치마크를 재사용했다. 독립적인 새 블라인드 평가나 실제 장애 검증으로 주장하지 않는다. 난도·주입 강도·정답·평가 대상을 변경하지 않았다. 관측된 동일한 시간들을 모든 길이에서 평가한다. 길이별 점수는 해당 배치 종료 뒤 제공하므로 긴 배치는 더 늦게 판단한다.

현재 벤치마크는 3~7일 이상이 지속되는 변화를 대상으로 한다. 실제 분 단위 장애, 정상 대량 처리, 다른 Adapter에 대한 운영 검증은 별도다. 정상 참고 구간이 정말 정상인지도 운영에서 검토해야 한다.

## 참고한 원리

- [NIST: Detection of Outliers](https://www.itl.nist.gov/div898/handbook/eda/section3/eda35h.htm): 분포 가정이 틀리면 이상값 판단이 왜곡될 수 있다는 주의. 위·아래 꼬리를 분리한 구체적 설계는 이번 데이터 분석에 따른 적용이다.
- [SciPy gaussian_kde 문서](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.gaussian_kde.html): 비모수 밀도 추정과 대역폭의 영향. 구간 통계의 희소도를 평가하는 데 Gaussian kernel 원리를 적용했다.
- [Gutmann & Hyvärinen, AISTATS 2010](https://proceedings.mlr.press/v9/gutmann10a.html): 정상 자료와 가상 잡음을 구분하는 원리를 참고한 별도 자기지도 후보도 비교했다. 논문의 정확한 likelihood 구현을 재현한 것은 아니며, **개발셋에서 더 나아지지 않아 최종 저장 모델에 포함하지 않았다.**

초기 탐색·실패 후보는 explore.py·moments.py·balance.py·joint.py·contrastive.py와 개발 탐색 결과에 보존했다. 배포·추론 경로는 model.py와 models.joblib이며, 탐색 스크립트가 실행될 필요는 없다.
