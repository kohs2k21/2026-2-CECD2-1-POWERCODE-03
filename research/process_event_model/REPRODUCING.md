# 새 데이터 추론·평가·학습

모든 명령은 이 패키지 루트에서 `PYTHONPATH=code` 설정 후 실행합니다. 출력은 기존 파일을 덮어쓰지 않는 새 경로를 사용합니다.

## 1. 최신 모델 평가

### 원본 Process 실행별 평가

입력: 표준 Process CSV/JSON. 정답: `event_row_id,is_anomaly` 두 컬럼을 가진 별도 파일입니다. 입력과 정답의 ID가 일대일로 정확히 맞아야 합니다. 정답은 boolean 또는 0/1입니다.

```powershell
python -m event_workflow evaluate --model models/latest_process_rows/event_models.joblib --algorithm if --processes YOUR/processes.csv --labels YOUR/row_labels.csv --unit row --output outputs/my_if_row_eval.json --trust-model
```

기본은 `anomaly`를 평가합니다. 정책 적용 후 실행 후보를 평가하려면 `--decision confirmed_anomaly`를 지정합니다. 결과의 단위는 **원본 실행 행**이므로 공개된 주 성능표의 분별 F1과 직접 비교하면 안 됩니다.

### 최종 분 단위 경보 평가

정답 컬럼: `case_id,adapterType,processId,window_start,is_anomaly`. 각 행은 정확히 1분 구간이며 `window_start`는 분 경계입니다. 독립 실험/운영 세션은 서로 다른 `case_id`를 사용하고 Process 입력에도 같은 값을 넣습니다. 정상 대조군도 독립 세션으로 전달합니다.

```powershell
python -m event_workflow evaluate --model models/latest_process_rows/event_models.joblib --algorithm if --processes YOUR/processes.csv --labels YOUR/minute_labels.csv --unit minute --output outputs/my_if_minute_eval.json --trust-model
```

이 경로는 원본 Process 행으로 피처·점수를 계산한 뒤, 제공된 분 구간에 현재 경보 정책을 투영합니다. 미래 실행이 이전 피처나 판정을 바꾸지 않으며 point adjustment를 하지 않습니다. 빈 분 질의는 기존 경보 유지/만료만 확인하고 새 입력 중단 경보를 만들지 않습니다. 필요한 과거 Process 이력도 입력에 포함해야 합니다. 입력에 없는 실행 유형의 완전한 무관측 상태는 이 평가의 범위가 아닙니다.

## 2. 직전 모델 평가

```powershell
python -m research_workflow evaluate --model models/previous_minute/if_shared.joblib --native YOUR/minute_grid.csv --completions YOUR/completions.csv --labels YOUR/minute_labels.csv --output outputs/my_previous_if_eval.json --trust-model
```

- 격자 컬럼: `case_id,adapterType,processId,window_start,execution_arrivals`. 각 `case_id`는 한 Adapter+Process의 시간순 연속 1분 격자
- 완료 관측 컬럼: `case_id,adapterType,processId,totalCount,duration,end_seconds`. `duration`은 ms, `end_seconds`는 기존 모델 계약의 초 단위 시각이며 timezone 없는 격자 시각과 같은 기준으로 맞춤
- 격자는 KST naive 시각 권장. timezone 있는 입력은 KST naive로 변환됨. `end_seconds`는 변환 후 naive 격자의 `Timestamp.value / 1e9`와 같은 좌표계여야 하며 UTC epoch와 혼합 금지
- 직전 정답 컬럼: `case_id,window_start,is_anomaly`. 최신 평가 정답보다 키가 단순한 이유는 직전의 case 하나가 한 Adapter+Process로 제한되기 때문
- 수집 여부가 확인되지 않은 공백을 임의로 실행 0건으로 만들면 안 됨. 기존 모델 계약을 그대로 전달하는 경로이지 새 운영 수집기 구현이 아님

## 3. 새 정상 CSV로 원본 행 모델 재학습

```powershell
python -m process_event_model.train --train-normal-csv YOUR/normal_train.csv --normal-calibration-csv YOUR/later_normal.csv --predictive-traffic --output outputs/my_new_event_models.joblib
```

학습·보정 CSV는 정상으로 검토한 완료 S 후보이며 `successCount,errorCount,retryCount`도 필요합니다. 보정은 학습보다 뒤의 관측 날짜이고, Transaction 중복은 허용하지 않습니다. 단순 S라는 이유만으로 실제 정상이라고 단정하지 마십시오.

**이 명령은 정상-only 재학습 baseline입니다.** 현재 선택된 native/calendar 파이프라인의 개발 정답 기반 보정·경보 정책 전체를 그대로 재현하는 명령이 아닙니다. 새로운 정상 참조·피처·IF/K-means/LOF와 정상 분위수 경보선을 적합하며 공개 성능을 상속하지 않습니다. 이 모델은 frozen incident policy가 없으므로 예측에 `--normal-only-baseline`을 명시합니다.

```powershell
python -m event_workflow predict --model outputs/my_new_event_models.joblib --algorithm if --processes YOUR/new_processes.csv --normal-only-baseline --output outputs/my_refit_prediction.json --trust-model
```

## 4. 새 정상 자료로 직전 방식 재학습

```powershell
python -m research_workflow train --algorithm if --normal-native YOUR/normal_grid.csv --normal-completions YOUR/normal_completions.csv --calibration-native YOUR/later_grid.csv --calibration-completions YOUR/later_completions.csv --settings configs/if_previous_core.json --quantile 0.99 --reviewed-normal --output outputs/my_previous_refit.joblib
```

`kmeans`, `lof`도 algorithm과 설정 파일을 바꿔 학습합니다. 이 경로도 공개 v7의 정상 참조와 학습 코어를 새로 적합하는 **새 정상-only baseline**이며 v7 개발 정답 기반 통계 결합·세부 경보표 선택 전체를 재현하지 않습니다. 새 모델의 성능은 새 정답으로 다시 평가해야 합니다.

## 5. 공개 성능의 정확한 재현과 저장 결과 보기

- `notebooks/*.ipynb`: 포함된 집계 결과만 읽는 실행 가능한 보고서. 데이터 없이 결과 표·일반 막대 그래프를 확인 가능
- `code/process_event_model/minute_confirm.py` 등: 당시 연구 재현 소스. 원본으로 계산한 평가 캐시·기존 벤치마크·정답·중간 산출물 필요
- `code/shared_model_v7/evaluate_one.py` 등: 직전 동결 모델 평가·감사 소스. 당시 벤치마크와 입력 맥락 필요
- 원본·벤치마크·정답·개발 캐시는 이번 업로드 제외이므로, 위 과거 전수 실험을 데이터 없이 처음부터 재현할 수 있다고 표시하지 않음
- 당시 코드가 기대하는 `reports/`, `data/evaluation/` 배치는 외부 자료를 별도로 준비해야 함. 새 입력 평가에는 위 portable CLI를 사용
- 포함 소스 목록은 import 의존성 기준이며 모든 과거 탐색·실패 실험을 보존한 전체 연구 아카이브가 아님

원본 수집 기간과 학습·보정 데이터의 성격, 평가 단위, 남은 한계는 [MODEL_COMPARISON.md](MODEL_COMPARISON.md)에 정리했습니다.
