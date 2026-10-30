# 저장 모델로 새 데이터 예측·평가

Python 3.12에서 이 폴더를 작업 디렉터리로 사용한다.

```powershell
python -m pip install -r requirements.txt
$env:PYTHONPATH = "$PWD/code"
python inspect_latest.py --trusted --smoke
```

## 단기 입력

두 표를 CSV 또는 JSON 레코드 배열로 받는다. Transaction 표는 필요 없다.

| 표 | 필수 컬럼 | 의미 |
|---|---|---|
| native | `case_id, adapterType, processId, window_start, execution_arrivals` | 수집이 정상으로 확인된 연속 1분 격자와 실제 Process 시작 건수 |
| completions | `case_id, adapterType, processId, totalCount, duration, end_seconds` | 완료 Process 한 건씩. duration은 ms, end_seconds는 Unix 초 |

`case_id` 하나는 Adapter+Process 하나의 연속 감시 구간이다. 완료 행에도 같은 ID를 쓴다.
시각 문자열은 KST(시간대가 있으면 변환), Unix 초는 실제 epoch 값이다.
`window_end`는 생략 가능하며 시작+1분으로 만든다. 분별 분위수는 완료 행에서 다시 계산한다.
전체 과거 prefix를 함께 전달해야 최근 추세·공백을 재현한다.
수집 정상 확인이 있어야 실행 0건을 넣을 수 있다. 수집 불명 구간은 별도 분리한다.

```powershell
python -m research_workflow predict --model reports/shared_model_v7/release/handoff/if_shared.joblib --trust-model --native YOUR/native.csv --completions YOUR/completions.csv --output outputs/new_predictions.json
```

K-means·LOF는 모델 파일명만 바꾼다. `--expected-sha256`으로 해시도 검사할 수 있다.
하드룰은 `code/process_anomaly_pipeline/rules.py`에 별도로 있으며 이 명령이 자동 실행하지 않는다.
새 Adapter에서는 넓은 정상 기준으로 대체하지만 새 도메인 성능이 보장되는 것은 아니다.

## 새 데이터 평가

정답 표는 `case_id, window_start, is_anomaly`이며 마지막 값은 0/1 또는 JSON boolean이다.
예측과 정답은 같은 분 키로 일대일 대응해야 하며 중복·누락·추가 정답은 오류다.

```powershell
python -m research_workflow evaluate --model reports/shared_model_v7/release/handoff/if_shared.joblib --trust-model --native YOUR/native.csv --completions YOUR/completions.csv --labels YOUR/labels.csv --output outputs/new_evaluation.json
```

Accuracy·Precision·Recall·F1·FPR와 혼동행렬을 계산한다. 정답 구간 확장(point adjustment)은 하지 않는다.
정답이 없으면 predict만 사용한다. 정상만/이상만 있는 평가에서는 계산 불가능한 지표의 분모를 확인해야 한다.

## 장기 모델

Process 원래 컬럼의 CSV/JSON을 그대로 받고 내부에서 5분 집계한다.
별도 검토한 정상 참고 28일과 수집 메타데이터가 필요하다. coverage JSON 형식은 아래다.
실제 수집 확인 없이 예시 구간을 복사하면 안 된다.

```json
{"source":"YOUR_COLLECTOR_TELEMETRY","certified_intervals":[["2026-01-01T00:00:00","2026-02-01T00:00:00"]]}
```

```powershell
python -m long_term_anomaly.five_minute --model reports/long_term_anomaly/five_minute_localized/models_portable.joblib --processes YOUR/processes.csv --reviewed-normal-processes YOUR/reviewed_normal.csv --normal-end 2026-01-29T00:00:00 --window-end 2026-01-30T00:00:00 --analysis-hours 24 --coverage YOUR/coverage.json --output outputs/long_prediction.json
```

검증 범위상 정상 참고 종료 후 14일 안의 감시 구간을 지원한다. 새 집단은 정상 참고 등록이 필요하다.
출력은 `decisions`, `fine_bins`, `window_features`, `diagnostics`이며 수집 불명은 판단 불가다.
세부 계약·제약은 `code/long_term_anomaly/five_minute/README.md`에 있다.
기존 backend/worker에 새 모델을 자동 적용하는 변경은 포함하지 않았다.
