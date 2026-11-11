# 수치 SR · 논리 SR · ML 연구 실행 번들 1.1

`cli.py` 하나로 같은 로그에 수치 SR, C3 논리 SR, IF·K-means·LOF를 적용하고 최종 OR 판정과 근거를 저장한다. 기존 생성 명세와 학습 가중치를 조합한 실행 번들이다. 신경망 가중치를 병합하거나 이번 작업에서 다시 학습한 모델은 아니다.

ESB 기본 탐지에는 **분모 0인 비율의 실제 대체 평가**가 포함된다. 비율 규칙을 비해당으로 빼지 않는다. 기존 건수 차이 피처와 그 TRAIN 기준선 또는 제공된 처리 건수 정의의 합계 일관성 SR로 평가하고, 어떤 검사로 판정했는지 보존한다. 원천 결측·관계 불명·대체 검사 불가는 계속 평가 불가다.

## 포함된 구성

| 프로필 | 동결 피처 | 수치 SR | C3 논리 SR | 동결 ML |
|---|---|---|---|---|
| ESB | 기존 65개 AST, ML에는 T25/P13/M4=42개 | 42개 | 3개 | T/P/M × IF·K-means·LOF, 9개 |
| FlowBench 1000genome | 8개 AST | 기존 공동 p99 조건 7개 | 작업 목록 참조 1개, 실행 ID 검사는 별도 무결성 채널 | 3개 |
| FlowBench Montage | 8개 AST | 기존 공동 p99 조건 8개 | 작업 목록 참조 1개 | 3개 |

두 데이터 계열의 모델을 서로 바꿔 넣지 않는다. ESB의 기존 관측 SR58개와 HR10개는 원래 실행 결과에 보존하되 새 주 판정에는 포함하지 않는다. 새 주 판정은 **수치 SR + C3 논리 SR + ML**이다. 기존42개 ML 입력에 새 Boolean 피처를 추가하지 않았다.

## 환경과 CPU 확인

현재 승인된 Docker 컨테이너와 프로젝트 Python 환경을 사용한다. 자동 설치·다운로드·학습은 없다. CPU 의존성은 `requirements.cpu.txt`, 선택적 LLM 생성 환경은 `vendor/logical/model/requirements.lock.txt`에 기록되어 있다. 새로운 설치는 별도 승인 후 진행한다.

```bash
PY='/home/msko20702/#종합설계/data/.venv/bin/python'
MODEL='/path/to/this/model'

"$PY" -B "$MODEL/cli.py" inspect
"$PY" -B "$MODEL/verify.py" --output /path/to/NEW_cpu_verification
"$PY" -B -m unittest discover -s "$MODEL/tests" -p test_ratio_fallback.py -v
```

모든 출력은 새 폴더를 사용한다. 포함된 파일·모델·환경·입력 계약이 달라지면 자동 대체·재학습하지 않는다.

## ESB: 새 T/P/M CSV 탐지

기존과 같은 스키마의 원천을 메타데이터 캡처로 만든다. 기간은 T.START_TIME을 기준으로 선언하며 해당 거래의 P/M 전체를 함께 제공한다. Body·payload·RESPONSE_MESSAGE는 입력 캡처에서 제외한다. 임의의 필드명·단위 변경이나 부분 거래를 조용히 추정하지 않는다.

```bash
"$PY" -B "$MODEL/cli.py" capture-esb \
  --transactions /path/to/T.csv \
  --processes /path/to/P.csv \
  --messages /path/to/M.csv \
  --start 2026-08-22 --end 2026-09-09 --split test \
  --data-class approved_real \
  --complete-family-capture --held-lineage-excluded --accept-frozen-reference \
  --output /path/to/NEW_capture

"$PY" -B "$MODEL/cli.py" detect-esb \
  --capture /path/to/NEW_capture/capture.json \
  --output /path/to/NEW_detection
```

승인된 기존 `shared42-capture1.0` 캡처도 직접 사용할 수 있다. `--accept-frozen-reference`는 과거 수치 기준선을 연구 비교에 적용한다는 선언이며 새 운영 환경에서의 유효성을 증명하지 않는다. 기존 HR 근거가 그대로 유효한 경우에만 `capture-esb --confirm-same-hr-contract`를 사용한다. HR은 새 주 OR에 포함하지 않는다. 9/30~10/2 보류 계보와 미확인 원천을 우회하지 않는다.

ESB의 C3 규칙은 공급된 CSV에서 P→T, M→P 문자열 참조와 T 성공·P 실패 공존을 확인한다. 재시도 식별·복구·허용 예외·실제 수집 완전성을 증명하지 않는다. `profiles/esb/LOGICAL_SCOPE.txt`에 채택 범위가 있다. 이번 호출은 **연구용 사실 검토 후보 실행**이며 서비스 경보 자동 채택은 아니다.

## 분모 0인 비율의 대체 평가

정확한 정의 SHA·모집단·원천·단위·기준선으로 바인딩한 수치 SR14개만 대상이다. 분모가 양수이면 기존 비교를 유지한다. 확인된 분모가0이면 다음을 실행한다.

- `전체 P 수 / N 상태 P 수` → `전체 P 수 - N 상태 P 수`를 기존 건수 기준선과 비교한다.
- `전체 건수 / 성공 건수` → `전체 건수 - 성공 건수`를 기존 건수 기준선과 비교한다. 성공·실패·전체 건수의 원천 일관성도 검사한다.
- `실패 건수 / 전체 건수` 및 `성공 건수 / 전체 건수`에서 전체 건수0 → 제공된 컬럼 설명의 `전체=성공+실패`를 실제로 검사한다. 문서상 관계와 어긋나면 SR 검토 경고, 일치하면 해당 대체 검사는 비경고다. DB 강제 제약이나 실제 운영 장애로 확정하지 않는다.

전체14개 분기와 재사용 기준선은 `profiles/esb/ratio_fallback.json`에 있다. 분자·분모가 결측/음수/비유한이거나 관계/대체 검사 확인이 불가하면 보류한다. 0, 무한대, epsilon으로 값을 채우지 않으며 ML 피처와 기존 가중치는 바꾸지 않는다.

기본값은 `--ratio-policy fallback`이다. 엄격한 기존 비교를 실행하려면 아래처럼 지정한다.

```bash
"$PY" -B "$MODEL/cli.py" detect-esb \
  --capture /path/to/NEW_capture/capture.json --ratio-policy strict \
  --output /path/to/NEW_strict_detection
```

FlowBench에는 이 ESB 전용 정책을 적용하지 않는다. 원천 시간값 결측을 분모0으로 간주하지 않는다.

## FlowBench: 새 실행 CSV 탐지

한 CSV가 한 workflow 실행인 기존 FlowBench 형식이다. 해당 workflow의 입력만 별도 디렉터리에 둔다. 폴더명·ID·정답 라벨·주입 종류를 ML 피처로 사용하지 않는다.

```bash
"$PY" -B "$MODEL/cli.py" detect-flowbench \
  --workflow 1000genome --input-dir /path/to/1000genome_csvs \
  --output /path/to/NEW_flowbench_detection

# 고정 분할 평가 시에는 CSV 절대 경로의 JSON 배열을 명시할 수도 있다.
"$PY" -B "$MODEL/cli.py" detect-flowbench \
  --workflow montage --run-list /path/to/test_runs.json \
  --output /path/to/NEW_montage_detection
```

공식 DAG는 고정 버전의 작업 목록으로만 사용한다. 검증되지 않은 실행 의존 순서·시계 동기화·필수 노드 계약을 만들지 않는다. 1000genome에서 원래 모델이 생성한 실행 ID 참조 규칙은 어댑터가 동일한 ID를 양쪽에 넣어 항상 만족하므로 무결성 보조 채널로 명시적으로 분리했다. 원래 제안 전체는 `serving_pack.json`에 보존되어 있다.

## 결과 해석과 형식

`decisions.csv.gz`에 다음이 저장된다. 각 판정 값은 `warning`, `nonwarning`, `unavailable`이다.

- `numeric_SR`, `logical_SR`, `if`, `kmeans`, `lof`, `ML`, `combined`
- 원천 entity/거래 ID, 발동한 수치·논리 rule ID, 불가 rule ID와 불가 component
- component 실패 사유와 연구 후보라는 해석 범위
- `strict_numeric_SR`, `strict_combined`: 동일 입력에서 대체 평가를 사용하지 않은 원래 판정
- `fallback_for_rule_ids`, `fallback_actual_rule_ids`, `fallback_evaluation_count`: 어느 비율을 어떤 실제 검사로 평가했는지
- `original_unavailable_numeric_rule_ids`, `unavailable_numeric_rule_ids`: 원래 불가와 대체 평가 후 남은 불가를 각각 보존

OR는 하나라도 경고이면 경고, 세 component가 모두 비경고이면 비경고, 경고가 없고 일부 불가이면 불가다. 결과가 없거나 결측·계산 실패일 때 0이나 정상 판정으로 바꾸지 않는다. component 오류가 있어도 이미 계산된 다른 방법의 경고와 증거는 보존하며 실행 종료코드는 오류를 알린다.

`numerical/`에는 현재 원천에서 계산한 전체 피처와 원천행별 ML 점수·기존 규칙 근거가 있다. `logical/`에는 nullable Boolean 피처·연결 명세·발동 원천 행과 참조 행이 있다. ESB의 `numeric_and_ML_warning_evidence.jsonl.gz`는 원천행을 최종 거래와 연결한다. `summary.json`, `execution.json`, `manifest.json`은 방법별/단독/공통 집계, 버전·SHA·처리 시간과 파일 무결성을 기록한다.

ESB IF/K-means margin은 0 초과가 경고이고 LOF는 저장 임계값 초과가 경고다. FlowBench의 정규화 점수는 클수록 이상이며 각 저장 임계값을 초과할 때 경고다. 이 점수는 장애 확률이 아니다.

`ratio_fallback/alternative_rule_evidence.csv.gz`는 분자·분모, 대체 계산값·조건, 원천행과 실제 실행 규칙을 기록한다. 원래 비율 값은 여전히 미정의이며, 최종 판정은 기록된 대체 검사 결과를 사용한다. 대체 실행이 실패하면 기존에 확인된 경고와 근거를 유지하고 CLI가 부분 실패를 알린다.

## 선택적 ML 재학습과 논리 명세 생성

탐지 명령은 학습하지 않는다. 새 ML 학습이 명시적으로 필요한 경우에만 다음을 호출한다. 세 참조는 기간과 거래 가족이 겹치지 않아야 하며 TEST는 거부된다. 수치 SR·논리 SR·LLM 가중치는 이 Trainer에서 변경하지 않는다.

```bash
"$PY" -B "$MODEL/cli.py" train-esb \
  --train /path/to/train/capture.json \
  --calibration /path/to/calibration/capture.json \
  --threshold /path/to/threshold/capture.json \
  --output /path/to/NEW_training

"$PY" -B "$MODEL/cli.py" detect-esb \
  --capture /path/to/test/capture.json \
  --models /path/to/NEW_training/models \
  --output /path/to/NEW_trained_model_detection
```

선택 C3와 실제 생성 코드를 포함했다. 새 논리 명세가 필요하면 노출된 GPU 가용성을 확인한 뒤 로컬 Base를 명시한다. 이 명령은 명세 후보를 만들며 동결 탐지기에 자동 반영하지 않는다.

```bash
"$PY" -B "$MODEL/cli.py" recommend-logical \
  --input /path/to/source_metadata.json --root YourRootTable \
  --base-path /path/to/pinned_local_Qwen_snapshot \
  --output /path/to/NEW_recommendation
```

기존 `CUDA_VISIBLE_DEVICES` 설정을 유지하며 컨테이너에 할당된 GPU만 사용한다. Base는 Qwen/Qwen3.6-27B revision `6a9e13bd6fc8f0983b9b99948120bc37f49c13e9`이며 대용량 Base 가중치와 가상환경은 번들에 없다. ESB의 기존 명세 생성 원본 어댑터는 `models/esb_design_origin/`, FlowBench 수치 명세 계열은 `vendor/numeric/research/models/feature_rule_spec/`, C3는 `vendor/logical/model/`에 구분했다. 다른 어댑터가 기존 명세를 새로 생성한 것으로 설명하지 않는다.

## 실제 확인 범위

`RESULTS.md`에 실제 ESB와 FlowBench 결과 및 한계를 기록했다. CSV → 피처 → 수치 SR/논리 SR/ML → 최종 판정의 연구 실행 경로를 제공한다. 실제 수집 시각을 사용한 스트리밍·탐지 지연, 서비스 API·DB·알림, 기업이 확인한 장애 정답 기반 성능은 이번 번들에서 검증한 범위에 포함되지 않는다. 모델·규칙·기준선·스키마 변경은 새 버전과 별도 재평가로 관리한다.
