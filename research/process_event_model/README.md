# Process 이상 탐지 모델 — 실행 행 방식과 직전 1분 집계 방식

이번 #95 전달물은 **최신 원본 Process 행 모델 3개 + 직전 1분 집계 모델 3개**입니다. 모델·피처·전처리·예측/평가·새 정상 데이터 학습 코드와 집계 결과를 포함합니다. 서버 코드를 교체하거나 운영 모델을 자동 전환하지 않습니다.

## 먼저 볼 파일

- [최신 결과](notebooks/result.ipynb): 최신 성능·시나리오별 해석·동일 기준 비교
- [직전 결과](notebooks/previous_minute_result.ipynb): 기존 1분 집계 모델 결과
- [두 방식 비교](MODEL_COMPARISON.md): 입력 차이·성능·학습 근거·한계
- [새 데이터 학습·평가](REPRODUCING.md): 추론과 재학습, 공개 성능 재현의 차이
- [연동 주의사항](INTEGRATION.md): 상태·수신 순서·다른 연구 패키지와 충돌 방지
- [모델 목록](model_manifest.json): 선정된 모델 4파일의 해시·위치
- [포함 코드 목록](source_inventory.json): 필요한 import 의존성과 검증용 소스

## 1. 무엇을 사용해야 하나요?

| 구분 | 모델 경로 | 사용 목적 |
|---|---|---|
| 최신·기본 | `models/latest_process_rows/event_models.joblib` | 원본 Process 실행 한 행을 유지한 판정. 파일 하나에 IF·K-means·LOF 포함 |
| 직전·비교용 | `models/previous_minute/{if,kmeans,lof}_shared.joblib` | 그룹별 1분 집계 판정. 각 모델이 별도 파일 |

총 6개 알고리즘 모델, joblib 4파일, 모델 파일 합계 약 19.38MB입니다. 두 방식 모두 알고리즘별 공유 추정기 하나이며 Adapter 전문가·알고리즘 앙상블은 아닙니다. 직전보다 더 오래된 모델·장기 시계열 모델·원본 데이터·벤치마크 CSV/정답은 추가하지 않았습니다.

## 2. 환경 설정

Python 3.12에서 검증합니다. **이 README가 있는 폴더**를 작업 디렉터리로 사용합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:PYTHONPATH = "$PWD/code"
$env:PYTHONUTF8 = '1'
```

Linux/macOS에서는 가상환경을 활성화한 뒤 `export PYTHONPATH="$PWD/code"`로 설정합니다. 노트북·테스트에는 `pip install -r requirements-research.txt`도 필요합니다. 다른 research 폴더의 `code` 경로를 동시에 추가하지 마십시오.

## 3. 최신 모델로 새 입력 판정

```powershell
python -m event_workflow predict --model models/latest_process_rows/event_models.joblib --algorithm if --processes examples/completed_process.json --output outputs/if_prediction.json --trust-model
```

`--algorithm`을 `kmeans`, `lof`로 바꾸고 출력 경로도 바꾸면 됩니다. 예제는 직접 만든 가상 Process이며 고객 로그나 평가 벤치마크가 아닙니다. 실제 입력은 표준 컬럼을 가진 CSV·JSON·JSONL을 사용할 수 있습니다.

```python
from process_event_model.inference import load

monitor = load('models/latest_process_rows/event_models.joblib', 'if', with_incidents=True)
result = monitor.predict(completed_transaction_process_rows)
```

필수 컬럼: `transactionId`, `processId`, `adapterType`, `startTime`, `endTime`, `totalCount`. 가능하면 실제 수신 시각 `availableTime`, 안정적인 `event_row_id`도 전달합니다. 같은 Transaction의 완료 Process 행과 동일 수신 시각 배치는 함께 전달합니다.

| 출력 | 의미 |
|---|---|
| 원본 ID·컬럼 | 입력 실행에 다시 연결할 수 있도록 유지 |
| 파생변수 | 정상 소요시간 참조 및 해당 그룹의 과거 지연·빈도 맥락 |
| `score`, `threshold`, `anomaly` | 정규화 점수·경보선·실행별 판정. 점수는 장애 확률이 아님 |
| `confirmed_anomaly` | 선택한 경보 정책을 적용한 실행 후보 |
| `incident_active`, `incident_action` | 이미 열린 지연·폭주 경보의 상태 |

한 요청마다 모델을 로드하면 이력이 사라집니다. 서비스에서는 알고리즘별 monitor를 유지하고, 시간 역행·중복 전달·재시작 경계를 별도 처리해야 합니다. 입력 중단 중 새 경보를 만드는 기능은 이번 최신 모델의 범위가 아닙니다.

## 4. 직전 1분 집계 모델 실행

```powershell
python -m research_workflow predict --model models/previous_minute/if_shared.joblib --native examples/minute_grid.json --completions examples/minute_completions.json --output outputs/previous_if_prediction.json --trust-model
```

직전 모델은 **연속 1분 감시 격자 + 완료 Process 관측**을 받습니다. 최신 모델의 Process CSV를 그대로 `--native`에 넣는 방식이 아닙니다. 입력 스키마는 [재현 안내](REPRODUCING.md)에 있습니다.

## 5. 검증과 결과 확인

```powershell
python verify_package.py
python -m pytest code/process_event_model code/event_workflow code/research_workflow -q
python -m jupyter execute notebooks/result.ipynb --inplace
python -m jupyter execute notebooks/previous_minute_result.ipynb --inplace
```

검증 기록은 [publication_verification.json](publication_verification.json)에 둡니다. 노트북은 포함된 집계 지표·그림만으로 재실행할 수 있습니다. 이는 모델을 벤치마크 전체로 재평가하는 작업은 아닙니다.

새 데이터에서 후보·점수만 뽑을 때는 정답이 필요 없습니다. Accuracy·Precision·Recall·F1을 계산하려면 정답을 별도로 제공해야 합니다. [재학습·평가 명령](REPRODUCING.md)을 확인하십시오.

보안 주의: joblib은 Python 객체를 역직렬화하므로 신뢰하는 파일만 로드하십시오. 해시 확인은 변조 확인용이며, 신뢰하지 않는 pickle을 안전하게 만들지는 않습니다.
