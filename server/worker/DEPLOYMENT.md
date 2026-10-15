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
