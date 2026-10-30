# 1·4·8·12·24시간 선택형 분석

`analysis_hours`로 **한 번에 판정하는 구간 길이**를 고른다. 정상 참고 이력은 28일로 유지한다. 입력 집계 간격은 여전히 1시간이다. 원본이나 기존 14일 모델·노트북·결과를 덮어쓰지 않는다.

## 무엇이 달라졌나

| 설정 | 1회 입력할 분석 행 수 | 의미 |
|---|---:|---|
| `analysis_hours=1` | 1 | 한 시간 집계값과 정상 기준 비교. 시간 내부의 추세는 볼 수 없음 |
| `analysis_hours=4` | 4 | 4시간을 함께 보고 판정 |
| `analysis_hours=8` | 8 | 8시간을 함께 보고 판정 |
| `analysis_hours=12` | 12 | 12시간을 함께 보고 판정 |
| `analysis_hours=24` | 24 | 하루를 함께 보고 판정 |

같은 STL+PELT 구현을 공유하지만 **정상 점수 통계와 경보 기준은 길이별로 따로 학습·보정**했다. Adapter별 모델 5개가 아니라, 분석 구간 길이별 보정 5개다. 코드와 검토된 정상 참고 구간은 공유할 수 있다.

현재 입력이 시간당 1행이므로 1시간 분석은 1개 시점의 판정이다. 1시간 내의 증가 추세를 보려면 분/5분 단위 집계가 필요하며, 이번 작업에서 그 데이터를 임의로 만들어 넣지는 않았다. PELT 최소 구간은 원래 12시간으로 유지했으므로 1~12시간 배치 안에서는 변화점으로 둘 이상 나눌 수 없다. 24시간 배치에서만 조건에 따라 12시간 이상 구간 둘로 나눌 수 있다.

## 사용 방법

프로젝트 `code/`가 `PYTHONPATH`에 있어야 한다.

```python
from long_term_anomaly.intervals import load_bank

bank = load_bank('reports/long_term_anomaly/intervals/models.joblib')

# 같은 Adapter+Process의 검토된 정상 28일, 시간당 1행
reference = bank.prepare_reference(normal_history_28days)

# 이번에 분석할 4시간, 시간당 1행 → 총 4행
result = bank.predict_batch(reference, current_4hours, analysis_hours=4)

# 동일 reference를 유지하고 24시간치 입력으로 바꿀 수도 있음
result_24h = bank.predict_batch(reference, current_24hours, analysis_hours=24)
```

`reference`는 정상 참고 이력에서 얻은 시간대 기준·변동 폭 등을 메모리에 보관한 객체다. 매번 28일 원본을 다시 전송하거나 STL을 다시 적합할 필요는 없다. 서로 다른 Adapter+Process끼리 참고 구간을 섞으면 안 된다. `case_id`로 같은 집단인지 검사한다. 이 ID는 수치 피처로 학습하지 않는다.

정상 참고 구간에 분석 중인 이상 데이터를 자동으로 편입하지 않는다. 참고 구간 종료 이후 14일 범위에서 검증한 모델이므로 그 범위를 넘으면 정상 기준의 검토·갱신을 요구한다. S 상태만으로 정상 참고 구간이 검증되었다고 간주하면 안 된다.

### 입력 컬럼

| 컬럼 | 의미 |
|---|---|
| `case_id` | 같은 Adapter+Process의 분석 묶음 식별자 |
| `timestamp` | 시간대가 일관된 시간순 시각, 정확히 1시간 간격 |
| `log_requests` | log(1 + 시간당 정상 후보 Process 실행 행 수). API 요청 수가 아님 |
| `log_duration_ms` | 각 Process log(1 + 소요시간 ms)의 시간당 평균 |
| `log_items_per_process` | 각 Process log(1 + totalCount)의 시간당 평균 |
| `collection_observed` | 수집 여부를 알고 있는 시간인지 |
| `latency_available` | 실제 실행이 있어 소요시간을 관측할 수 있는지 |

수집 결측은 0건이나 정상 판정으로 바꾸지 않는다. 같은 배치 안에서만 짧은 결측을 보간하며, 6시간 초과 수집 결측 앞뒤는 분리한다. 다음 배치를 보면서 이전 배치 결측을 보간하지 않는다. 전체 결측 배치는 `assessable=False`다.

### 반환값

`timestamp`, `analysis_hours`, `batch_start`, `batch_end_exclusive`, `available_at`, `score`, `threshold`, `is_anomaly`, `assessable`, `reason` 등을 반환한다. 한 배치 안의 각 시간에 대해 결과를 준다. `reason`은 점수 근거이며 장애 원인 확정/시나리오 분류는 아니다.

분석 구간 안에서는 뒤쪽 시간도 함께 보기 때문에 **해당 배치가 끝난 뒤** 결과를 제공한다. 예를 들어 00~04시 배치는 00·01·02·03시 행을 입력하고 `available_at=04시`다. 04시 이후 배치의 데이터는 사용하지 않는다. 처리 지연 자체를 측정한 값은 아니다.

## CSV 추론

정상 참고 672행 + 이번 분석 H행으로 이루어진 CSV:

```powershell
$env:PYTHONPATH='code'
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.intervals --model reports/long_term_anomaly/intervals/models.joblib --input input.csv --output predictions.csv --analysis-hours 4
```

기존 평가 CSV처럼 참고 28일 + 분석 14일이 들어 있으면 **4시간씩 끊어서** 순서대로 처리할 수 있다.

```powershell
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.intervals --model reports/long_term_anomaly/intervals/models.joblib --input data/evaluation_long_term/test_hourly.csv.gz --output predictions_4h.csv --analysis-hours 4 --split-batches
```

Python에서는 `score_frame(bank, frame, analysis_hours=4, split_batches=True)`를 쓴다. 마지막에 H시간 미만이 남으면 조용히 버리지 않고 오류를 낸다. 사용자가 다음 입력까지 대기하거나 별도로 지원된 길이를 선택해야 한다. 1·4·8·12·24 외의 값은 거부한다.

## 평가 방식과 한계

- 기존 장기 시나리오 평가셋을 수정하지 않고 같은 336시간을 각 길이의 비중복 배치로 나눔
- 학습 정상 64사례로 길이별 점수 통계 적합, 정상 보정 48사례 및 개발 데이터의 정상 시간으로 길이별 기준 설정
- 알고리즘 설정은 고정하고 시간창을 배치 길이 이하로 제한하여 비교
- 최종 평가는 모두 같은 관측 시간·원래 시간별 정답 사용. 배치에 이상이 한 시간만 있어도 배치 전체를 정답 이상으로 바꾸는 평가는 하지 않음
- 해당 평가셋은 이미 기존 14일 모델 결과를 공개한 데이터이므로 **새로운 독립 블라인드 평가가 아닌 입력 길이 비교 실험**
- 다섯 길이의 성능이 모두 같거나 기존 중앙값 목표를 모두 달성한다고 보장하지 않음. 특히 1시간은 여러 시간에 걸친 지속성을 확인하기 어려움
- 기존 데이터의 이상은 3~7일 지속되는 반합성 시나리오다. 짧은 장애에 대한 최적 시간이나 실제 운영 성능을 이 결과만으로 확정하지 않음
- 기존 자료의 원본 관측 공백·DB/FILE 중심 분포·합성 연속성 한계는 그대로 적용

## 파일

| 파일 | 역할 |
|---|---|
| `model.py` | 길이 선택형 API, 정상 기준 객체, 배치 안에서만 계산하는 추론 |
| `protocol.json` | 모델 점수 확인 전 고정한 길이·기준 보정·비교 방식 |
| `run.py` | 길이별 통계 적합·기준 보정·같은 평가셋 비교 |
| `test_intervals.py` | 지원 길이·시간대 정렬·파일 없는 추론·미래 배치 미사용 등 테스트 |
| `validate.py` | 보존 해시·별도 지표 계산·API 점수 재현 검증 |
| `report.py` | 비교표·일반 막대그래프·사례 그림·요약 |
| `result.ipynb` | 실행 결과와 해석 |

출력은 `reports/long_term_anomaly/intervals/`에 생성한다. 사용 시 필요한 모델 파일은 `models.joblib` 하나다. 임의의 joblib/pickle은 코드를 실행할 수 있으므로 신뢰된 파일만 로드한다. Python 코드 패키지와 기존 [의존성 목록](../requirements.txt)도 필요하다.

## 재현

```powershell
$env:PYTHONPATH='code'
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.intervals.run fit
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.intervals.run evaluate
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.intervals.validate
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.intervals.report
./.analysis-venv/Scripts/python.exe -m pytest code/long_term_anomaly/intervals/test_intervals.py -q
```

평가 후에는 `fit` 재실행을 거부해 평가 결과를 보고 기준을 몰래 바꾸는 것을 막는다. 기존 14일 모델은 [기존 설명](../README.md)과 [기존 노트북](../result.ipynb)에 그대로 남긴다.
