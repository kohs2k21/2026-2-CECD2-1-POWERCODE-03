# 현재 구간만 사용하는 정상 분위수 보정 모델

1시간 모드에서 집단의 정상 분위수 차이를 약하게 보정한 최신 고정 후보다. 4·8·12·24시간 모델은 그대로 재사용한다. 이전 감시 배치의 로그·경보 상태를 이어받지 않는다.

## 결과와 목표

여섯 시나리오 지표의 중앙값이다. 모든 개별 시나리오가 이 값을 만족하는 것은 아니다. 전체 관측 시간을 합친 통합 지표도 노트북에 별도로 표시한다.

| 구간 | Accuracy | Precision | Recall | F1 | 최대 시나리오 오탐률 |
|---:|---:|---:|---:|---:|---:|
| 1시간 | 88.89% | 70.69% | 61.01% | 65.49% | 5.77% |
| 4시간 | 90.94% | 75.93% | 69.67% | 72.74% | 4.69% |
| 8시간 | 92.29% | 80.88% | 75.95% | 75.87% | 5.37% |
| 12시간 | 92.81% | 78.38% | 76.53% | 77.39% | 5.17% |
| 24시간 | 93.44% | 78.23% | 80.90% | 79.86% | 5.64% |

**전체 목표는 미달이다.** 각 구간에서 네 지표의 중앙값이 각각 65% 이상이고 전체·각 시나리오 오탐률이 6% 이하이어야 한다. 1시간 Recall이 아직 부족하다. results.json의 goal_met_all_lengths=false를 유지한다.

576개 기존 테스트 사례의 원래 관측 시간 192,178개를 각 길이에서 평가했다. 정답 확장·point adjustment·어려운 시나리오 제외·평가 난도 변경은 없다. 1시간 Recall은 이전 59.72%에서 61.01%, F1은 64.56%에서 65.49%로 개선됐다. 최대 시나리오 오탐률은 5.89%에서 5.77%로 낮아졌다.

개발셋의 정상 분위수 보정 후보 하나를 선택해 설정·모델을 고정한 뒤 테스트했다. 이전에 보고한 테스트 집단을 재사용한 연구 비교이며 새 독립 블라인드 평가가 아니다. 최종 설정을 테스트 정답에 맞춰 변경하지 않았다. 선택·모델·코드·프로토콜·예측 봉인은 calibration.json과 prediction_seal.json에 기록한다.

## 바꾼 부분

정상 train 64개 사례의 앞 672시간에서 정상 시간대 기준·추세·변동 폭·자기상관·새 오차 성분을 학습했다. 64개는 고유 Adapter 수가 아니다. 정상 오차 재표본으로 학습한 분포는 이전 모델과 동일한 원리다. 평가셋을 새로 만든 것이 아니다.

이번 변경은 두 가지다.

1. 정상 변동 폭 추정 안정화: 실행 횟수는 정상 학습 집단의 공통 변동 폭을 사용한다. 소요시간은 해당 집단 분산 25%와 정상 학습 공통 분산 75%를 결합한다. 표준편차 자체의 단순 평균이 아니다.
2. 소요시간 분위수 보정: 해당 집단의 정상 분위수를 학습한 정상 분포의 분위수에 맞추는 변환을 25%만 적용한다. 실행 횟수에는 분위수 변환을 적용하지 않는다. 값의 순서를 뒤집지 않는 보간이며 범위 밖 값도 버리지 않고 제한된 기울기로 외삽한다.

보정은 정상 참고 통계로 만든 잠재 잔차 변환이다. 원본 CSV·현재 입력 배열을 덮어쓰지 않는다. 감시값으로 정상 분포를 다시 맞추지 않으며, 각 한 시간은 다른 감시 시점과 독립적으로 점수를 계산한다.

### 설정

| 항목 | 값 |
|---|---|
| 정상 참고 길이 | 672시간, 검토된 정상 28일 |
| 정상 학습 사례 | train 64개 사례의 앞 672시간만 |
| 시간대·선형 정상 추세 강도 | 각각 0.5 |
| 자기상관 | 정상 구간의 1차 자기상관, 0~0.95 |
| 정상 밀도 평활 폭 | 정상 표준편차 대비 0.03 |
| 일반 수준 이동 가설 | 정상 표준편차 대비 1~3배의 상위·하위 이동 |
| 일반 분포 확대 가설 | 추가 표준편차 0.5·1·2 혼합 |
| 정상 학습 공통 표준편차 | 실행 횟수 0.12049945, 소요시간 0.11903314, log 피처 잔차 단위 |
| 분위수 | 1·5·25·50·75·95·99% |
| 분위수 보정 강도 | 실행 횟수 0, 소요시간 0.25 |
| 점수 결합 temperature | 약 0.0653853, 최대값에 가까운 완화 결합 |
| 경보 기준 | 정상 calibration 및 개발셋 각 시나리오 정상 시간의 보수적인 상위 분위수 |
| 실제 1시간 경보 값 | 점수 > 1.98271326. 정확한 저장 값은 calibration.json의 threshold |

가중치는 이전 모델에서 개발셋으로 선택한 값을 유지한다. 순서는 실행 횟수 상위·하위·분포 확대, 소요시간 상위·하위·분포 확대다.

```text
[1.0, 15.12666255, 2.28340546, 5.93040784, 0.10048850, 13.25292800]
```

가중치·분위수 보정 설정 선택에는 개발셋 정답을 사용했다. 정상 분포 자체의 학습에 실제 평가 이상 정답을 넣지는 않았지만, 전체 설정 선택이 완전히 정답 없이 진행된 것은 아니다.

점수는 확률이 아니다. reason은 가장 크게 기여한 통계 가설이지 확정 장애 원인이 아니다. 학습 표 범위 밖 점수는 포화될 수 있으므로 큰 수치를 장애의 확정도나 영향도로 해석하지 않는다.

## backend 입력과 실행

원본 Process CSV를 그대로 입력하지 않는다. Adapter+Process 집단별 시간당 집계가 필요하다.

| 컬럼 | 뜻 |
|---|---|
| case_id | 동일 Adapter+Process 집단 식별자. 정합성 확인용이며 ID를 모델 피처로 인코딩하지 않음 |
| timestamp | 시간 집계 시작 시각. 오름차순, 정확한 한 시간 간격, 중복 없음 |
| log_requests | log1p(시간 내 Process 실행 수). Transaction 수나 API 요청 수가 아님 |
| log_duration_ms | 각 Process 소요시간의 log1p 값의 시간 내 평균 |
| log_items_per_process | 각 처리 건수의 log1p 값의 시간 내 평균. 현재 점수에는 사용하지 않는 호환 컬럼 |
| collection_observed | 수집 여부. False는 실행 0이 아니라 평가 불가 |
| latency_available | 실행이 있어 소요시간을 측정할 수 있었는지 |

```python
from long_term_anomaly.strict_alignment.model import load_bank

bank = load_bank('reports/long_term_anomaly/strict_alignment/models.joblib')
reference = bank.prepare_reference(normal_history_28days)
result = bank.predict_batch(reference, selected_batch, analysis_hours=1)
```

analysis_hours는 1·4·8·12·24 중 하나이며 정확히 H개의 시간 집계 행을 입력한다. 정상 참고 통계만 메모리에 유지할 수 있다. 감시 자료를 정상 참고 자료에 자동 편입하지 않는다. 참고 구간 뒤 테스트한 14일 범위를 넘으면 새 정상 참고 구간을 별도로 준비해야 한다.

```powershell
$env:PYTHONPATH = 'code'
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.strict_alignment `
  --model reports/long_term_anomaly/strict_alignment/models.joblib `
  --input input.csv --output predictions.csv --analysis-hours 1 --split-batches
```

CLI CSV는 집단별 처음 672행을 정상 참고 자료로 사용하고 나머지를 감시 배치로 나눈다. --split-batches는 여러 감시 배치를 한 파일에서 재생할 때만 사용한다. 기존 출력을 덮어쓰지 않으며 남는 행도 버리지 않고 오류로 처리한다.

backend에는 모델 파일, code/long_term_anomaly Python 패키지, 상위 requirements.txt와 호환되는 환경이 필요하다. Python 클래스가 들어 있는 joblib이므로 파일 하나만 복사하고 코드 경로를 없애면 로드되지 않는다. 신뢰할 수 있는 joblib만 로드한다. 평가 정답·탐색 캐시는 추론에 필요하지 않지만 검토된 정상 참고 자료는 별도로 필요하다.

## 검증·재현과 한계

- 모든 기존 원본·평가 자료·모델·노트북을 해시로 보존 확인
- 원래 시간 정답으로 전체와 30개 시나리오 혼동행렬을 독립 재계산
- 30개 전체 backend API 재생을 저장 점수와 비교하고 추론 시 파일 I/O 없음 확인
- 4·8·12·24시간의 모든 점수가 이전과 정확히 일치하는지 확인
- 감시 배치 밖 값 변경이 현재 배치 결과에 영향을 주지 않는지 단위 테스트
- 정상 참고 통계·입력 원본 불변성, 결측·실행 0 구분, 직렬화·CLI 보호 테스트

```powershell
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.strict_alignment.run fit
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.strict_alignment.run evaluate
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.strict_alignment.validate
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.strict_alignment.report
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.strict_alignment.notebook
```

이미 고정한 학습·평가 파일을 조용히 덮어쓰는 실행은 거부한다. 보고서·노트북은 저장된 결과에서 재생성한다.

1시간은 한 점이므로 장기 추세나 지속성을 현재 구간에서 직접 관측한 모델이 아니다. 4시간 이상 점수는 해당 배치가 끝난 후 제공되는 배치 점수이며 장애 시작 시각의 정확한 위치 탐지로 해석하지 않는다. 중앙값이 통과해도 일부 시나리오가 매우 약하다는 한계가 남는다.

평가셋의 정상 연속성도 AR형으로 생성되어 모델에 유리할 수 있다. STATUS=S도 실제 정상의 보장은 아니다. 운영 정상 참고 자료 검토와 실제 독립 장애 시계열 검증이 필요하다. 공식 ADBench 장기 벤치마크 결과가 아니라 기존 반합성 장기 시나리오의 연구 결과다.

[Bühlmann, Sieve Bootstrap for Time Series, Technical Report 431](https://statistics.berkeley.edu/sites/default/files/tech-reports/431.pdf)의 자기회귀 근사·잔차 재표본 원리를 참고했다. 고정 AR(1)·집단 간 오차 공유·분위수 보정·밀도비 탐지로 변형한 것이므로 원 논문 구현이나 이론 보장을 그대로 재현한 것은 아니다.
