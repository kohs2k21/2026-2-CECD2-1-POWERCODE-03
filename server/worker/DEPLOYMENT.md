# Worker 배포·연결 설정

실행 절차의 기준 문서는 [Docker 배포](../infra/DOCKER_DEPLOYMENT.md)다. 기본은 **web/api/collector/detector/trainer/llm + 기존 PostgreSQL**이다. 파일 저장은 기존 호스트 디렉터리이고 monitor/window는 선택 프로필이다. 새 서비스 전환에는 006·API 축적/실시간성 측정에는 007 migration까지 필요하다.

| 실행 명령 | 역할 | 환경 |
|---|---|---|
| `python -m collector.input_runner` | API/CSV 수동 전환·공통 입고·측정 | CPU, Python 3.12 |
| `python -m detector.service` | 직접 전달 HTTP + 동일 PG queue 복구, 3모드 | CPU, Python 3.12 |
| `python -m trainer.runner` | train/evaluate만 처리 | CPU, Python 3.12 |
| `python -m llm_worker` | Base 한 번 적재 + 추천/설명 큐 | GPU 0 한 장, Python 3.12 |
| `python -m collector.report` | 상시 수집의 실시간성 측정 보고 | DB 읽기만 |
| `python -m monitor` / `window_worker` | 결측 변화 / 1분 시간창 | CPU, 선택 실행 |

Trainer는 LLM/GPU 준비 상태를 기다리지 않는다. LLM은 CPU 공통 라이브러리도 사용하므로 requirements.txt + requirements-generation.txt를 설치한다. 기본 이미지 CMD와 Compose를 사용하면 호스트 Python·Node·pnpm을 설치할 필요 없다.

## 필요한 환경

- 각 서비스 DATABASE_URL은 서비스별 login 역할로 지정. 별도 DBA 권한 SQL은 `infra/scripts/database-roles.sql`.
- ARTIFACT_ROOT: Trainer 쓰기, Detector·LLM 읽기. host bind 권한을 기존 rootless/rootful 운영과 맞춘다.
- RESEARCH_BUNDLE_ROOT: export한 해시 검증 번들. LLM/v12만 필요. Base는 RULE_MODEL_BASE_PATH, revision `6a9e13bd6fc8f0983b9b99948120bc37f49c13e9`.
- LLM: DESIGN_MODEL_TOKEN 32자 이상. CUDA_VISIBLE_DEVICES로 실제 노출 한 장, NF4/bf16. 피처 v5·Rule v2 adapter와 설명 Base를 한 모델에서 전환한다.
- RULE_V3_BUNDLE_ROOT: `python research_bundle.py --rule-v3 --output <새 폴더>`로 export한 Rule v3 묶음(feature_rule_spec 중 Rule 경로 파일만, `bundle.json` SHA 검증). 선택이며 compose override `infra/compose.rule-v3.yaml`로 켠다. 있으면 같은 Base에 Rule v3 LoRA(`Rule`)를 더 올려 `recommend_thresholds`의 `llm_spec.model_version=rule_v3` 작업을 처리한다(HTTP `/generate`에는 없음). export 검증·Base 대조(revision·메타데이터 SHA)·초기화가 실패하면 v3만 끄고 시작 로그에 `rule_v3_disabled: <코드>`를 남긴다. 이미지에 xgrammar·C 컴파일러(gcc·libc6-dev)가 필요하다. GPU 확인 절차는 `rule_v3_check.py` 머리말.
- LLM_REPLICA_GPUS(선택, Rule v3 병렬): 컨테이너 안 GPU 번호 목록(예 `1,2,3`). 장마다 자식 프로세스가 같은 모델을 따로 올리고 한 `recommend_thresholds` 작업의 결과 항목(필드 × 적용 범위 묶음, 최대 32개)을 나눠 처리한다(`generation_replicas.py`). 주 프로세스는 CUDA_VISIBLE_DEVICES 한 장 그대로이고 설명은 주 프로세스만 처리한다. 비어 있으면 기존 직렬 실행과 같다. compose override `infra/compose.llm-multi-gpu.yaml`(GPU 4장 노출, CPU·메모리 상한은 기본 compose의 LLM_CPUS·LLM_MEMORY를 물려받으므로 상한을 지원하는 호스트에서는 장수에 맞게 키운다)로 켠다. 복제본 적재 실패·어댑터 SHA 불일치는 그 장만 끄고 시작 로그에 `rule_v3_replica_disabled: <gpu> <코드>`를 남긴다. 시작 대기 상한 LLM_REPLICA_START_SECONDS(기본 1800).
  - 시작 명령의 `LLM_REPLICA_GPUS`는 그때 실제로 비어 있는 장과 맞춘다. 시작 전에 호스트 `nvidia-smi`로 장별 사용 메모리·프로세스를 보고 다른 계정·서비스의 프로세스가 있는 장은 넣지 않는다(다른 계정 프로세스는 건드리지 않는다). override 기본값 `1,2,3`을 확인 없이 쓰지 않는다.
  - LLM_REPLICA_MIN_FREE_MB(선택, MiB, 기본 20480): 복제본 자식은 GPU를 바꾼 뒤·모델 적재 전에 그 장 여유 메모리(`torch.cuda.mem_get_info`)를 보고 이 값보다 적으면 적재하지 않고 끝난다(`rule_v3_replica_disabled: <gpu> replica_insufficient_memory`, 잴 수 없으면 `replica_memory_check_failed`). 나머지 장과 주 프로세스는 그대로 돈다. 기본값 근거: 복제본 하나가 모델 적재에 약 18GiB, 생성 중 최고점에 1~2GiB를 더 쓰므로 그 합에 여유를 둔 값이다. 0이면 검사하지 않는다.
  - 시작 검사는 시작 뒤 다른 프로세스가 그 장으로 옮겨 오는 일을 막지 못한다. 그래서 실행 중에 복제본이 CUDA 메모리 부족(`OutOfMemoryError`)을 내면 그 복제본을 끄고(`rule_v3_replica_disabled: <gpu> replica_out_of_memory`) 프로세스를 끝내 그 장 메모리를 돌려준 뒤, 그 묶음을 남은 복제본이나 주 프로세스에서 다시 돌린다(주 프로세스는 여전히 맨 뒤 순서). 복제본 자식이 죽거나 파이프가 끊긴 경우(`replica_exited`)도 끄고 다시 돌리되, 그 묶음이 원인일 수 있어 다른 복제본에서만 돌린다(주 프로세스로 보내지 않음). 꺼진 실행기는 그 작업에서 다시 쓰지 않으므로 묶음마다 실행기당 최대 1회, 작업 전체 재실행은 복제본 수 이하다. 받을 실행기가 없으면 기존처럼 생성 장애(`generation_failed`)로 그 시도가 끝난다. 결과 항목 순서·내용·진행률(묶음당 한 번)은 그대로다. 그 밖의 실패(시간 초과·입력·모델·값)는 기존 규칙 그대로다.
  - 꺼진 복제본은 llm을 다시 시작할 때까지 돌아오지 않는다. 로그에 `rule_v3_replica_disabled`가 보이면 `nvidia-smi`를 다시 보고 비어 있는 장으로 `LLM_REPLICA_GPUS`를 정해 다시 시작한다.
- Detector: DETECTOR_DELIVERY_TOKEN 32자 이상. Collector 직접 전달은 jobId/hash만 보내고, persisted input과 정확한 PG queue item을 claim한다. 직접 전달 실패/중복은 polling 복구에 맡긴다.
- Collector: 제공 서버 Bearer 토큰, configuration/monitoring URL, 고정 source key, 허용 HTTP host. 상세 설정과 미확정 원천 로그 계약은 [Collector 문서](collector/README.md).

환경 변수 예시는 `infra/.env.example`이다. 비밀값은 Git/이미지에 넣지 않는다. server/worker/.env는 직접 실행 진단용이며 Docker에서는 infra/.env 값을 주입한다.

## 모델·기능 범위

피처 v5·Rule v2는 집계된 허용 Process 통계로 추천하고 응답을 DSL·출처·단위·버전 기준으로 검증한다. 실행 가능한 파생변수는 현재 Process 행 AST에 한정한다. 검증되지 않은 T/M 관계를 임의 join하지 않는다. Trainer와 Detector가 공통 변환을 사용한다. 동일 조건 비교는 명시적 snapshotId로 재사용하며, 작업 재시도는 최초 고정 snapshot을 사용한다.

IF는 기존 train 내부 fit/보정 P99를 사용한다. validation/test 평가와 실제 임계값 선택 방식은 분리해 기록한다. test를 반복 튜닝에 쓰지 않는다. 실제 v12 IF/K-means는 1분 연속 grid·KST·원천 clock/arrival coverage·prior 수락 조건이 별도로 필요하다.

새 탐지 구성은 rule_only / ml_only / parallel 모드, 100~30000ms 마감, 명시적 결합 정책을 갖는다. 모델 부족 때 Rule 결과를 유지하고 partial/unavailable을 저장한다. 미선택 경로만 skipped다. 뒤늦은 계산은 최종 저장 판정을 갱신하지 않는다. 과거 정책 없는 구성은 legacy 호환 설정을 근거에 표시한다.

설명은 사용자 요청 후 saved facts만 사용하고 모델 output을 fact ID·허용 점검 항목으로 검증한다. 근거 없는 원인·기여도·SHAP을 생성하지 않는다. 추천은 관리자, 설명은 인증 사용자 권한이다. PG recommendation/explanation 큐를 분리하고 한 GPU에서 bounded 교대 처리한다.

서버 접속·실제 고객 API 호출·GPU 적재는 이번 로컬 구현 검증에서 실행하지 않았다. [검증 기록](VALIDATION.md)을 참고한다.

## 공통 전체 입력

현재 기본 Collector는 최초 일시정지 상태로 시작한다. 009 migration 이후 [SWITCHABLE_INPUTS.md](../infra/SWITCHABLE_INPUTS.md)에 따라 CSV 묶음을 등록하고 입력 모드를 선택한다. 피처 생성·변환·Rule 추천·사용자 채택·학습/평가·탐지를 같은 source에서 실행한다.
