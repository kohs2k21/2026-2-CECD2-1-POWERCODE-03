# 최신 모델의 로드와 입력

## 먼저 확인

```powershell
cd research/anomaly_detection
python inspect_models.py
python inspect_models.py --trusted --smoke
```

이는 벤치마크 없이 모델 hash와 합성 입력 추론을 확인한다. `--trusted`는 직접 관리하는
모델 파일에만 사용한다. 내부 smoke 입력 예시는 `inspect_models.py`의 `demo_inputs()`에 있다.

## Python에서 모델 로드

```python
import json
import sys
from pathlib import Path

research = Path("research/anomaly_detection").resolve()
sys.path.insert(0, str(research / "code"))
from model_research_v8.inference import load

folder = research / "reports/model_research_v12/handoff"
manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
models = {entry["algorithm"]: load(folder / entry["file"], entry["sha256"])
          for entry in manifest["models"]}

# native와 completions는 아래 계약에 따라 caller가 구성한 DataFrame.
# 모델 로드 시 inputs.json이나 training/validation cache는 읽지 않는다.
if_result = models["if"].predict(native, completions)
kmeans_result = models["kmeans"].predict(native, completions)
```

## 입력 계약

모델이 바로 받는 것은 CSV 경로나 Transaction 테이블이 아니라 Process로 구성한 두 DataFrame이다.
원시 Process 한 행을 `predict()`에 그대로 넣으면 안 된다.

| 입력 | 필요한 컬럼과 의미 |
| --- | --- |
| `native` | `case_id`(감시 stream 식별자), `adapterType`, `processId`, `window_start`, `window_end`, `execution_arrivals`(해당 1분의 실행 도착 수) |
| `completions` | `case_id`, `adapterType`, `processId`, `totalCount`, `duration`(ms), `end_seconds`(완료 시각의 수치 표현) |

- stream 하나는 Adapter+Process 하나에 대응하고, 여러 stream은 구별된 `case_id`를 사용한다.
- `case_id`는 실행 Transaction ID가 아니라 상태를 유지할 업무 stream 키다. 평가에서는 사례 ID로 사용했다.
- 각 stream의 1분 grid는 시간순·연속이어야 한다. 요청이 0건인 분도 유지한다.
- 현재 판단까지의 prefix와 필요한 과거 컨텍스트를 제공한다. 새 요청마다 한 행만 전달하고 상태를 버리면 다른 피처가 된다.
- 완료 시각은 같은 KST-naive 달력 규약의 `model_evaluation.data.unix_seconds()`로 변환한다.
- 판단 시각보다 앞서 완료된 Process만 해당 시간창 계산에 사용한다. 실제 서비스는 지연 도착·watermark를 추가로 설계해야 한다.
- 입력에 정답·시나리오·정상 대조값을 포함하지 않는다. 원시 STATUS/건수/시각 정합성 하드룰은 모델 전에 별도로 적용한다.
- traffic prior는 모델에 내장돼 있어 별도의 benchmark history CSV 없이 로드된다. 다만 합성 기준이므로 실제 운영 적용 전에 검증/교체해야 한다.

결과는 `global_margin`, `adapter_margin`, `ensemble_margin`과 각 `*_anomaly`,
`expert_available`, `traffic_assessed`, `latency_assessed` 등을 포함한다.
`margin > 0`이 이상 후보이고, 점수는 0~1 확률이 아니다.
기존 backend가 0~1 `anomalyScore`를 기대한다면 별도 점수 규약이 필요하며 raw margin을 그대로 보내면 안 된다.

## 학습/평가 파일과의 구분

- `inputs.json`: 원본 정상 실행 참조 아카이브; 모델 생성·검증 및 벤치마크 생성 코드에서 사용
- `training_inputs.joblib`: 이전 시간창 선택의 정상 학습/보정/임계값 참조 데이터
- `validation_inputs.joblib`: 시간창 후보 선택용 검증 사례 캐시
- 위 세 파일은 공유에 포함하지만 최신 모델의 로드·추론에는 불필요
- 벤치마크 CSV·정답은 이번 업로드에서 보류; 따라서 기존 평가 스크립트를 실행해 전체 성능을 재계산할 수 없음
- 이전 모델 바이너리는 제외했으므로 과거 학습/재현 entrypoint와 최신 추론 API를 구분할 것

이 문서는 연결 준비를 위한 계약 설명이다. 서버 추론 runner/API/경보 저장·프론트 연결을 구현하거나 배포한 것은 아니다.
