# Python worker 운영 경계

기본 구조는 **Collector / Detector / Trainer / LLM**이다. web/api 및 기존 PostgreSQL을 포함하면 기획서의 7개 서비스다. [Docker 배포](../infra/DOCKER_DEPLOYMENT.md)를 기준으로 실행한다. 006·007까지의 마이그레이션이 필요하다.

- Collector: 제공된 Bearer 조회 API를 계속 호출하고 설정·안전한 응답 snapshot·요청 실시간성 측정값을 DB에 축적한다. 조회 API의 실행/revision/원천 시각 계약이 미확정이면 탐지용 T/P/M 관측으로 추정하지 않는다.
- 검증된 관측 입력(CSV 등): 관측 + 불변 manifest + detect job + detection 큐를 같은 트랜잭션에 저장한다. 승인 구성이 없는 입력은 대기하고 나중에 해당 원천의 활성 구성으로 job을 고정한다.
- Detector: 공통 파생변수 계산, Rule 전용/ML 전용/병렬, deadline, 현재 임대 token 검증, 결과·event·ack의 원자적 확정. 직접 전달과 polling이 같은 PG item을 claim한다.
- Trainer: train/evaluate만 소비, 동일 조건 snapshot 재사용·재시도 고정, 실제 CPU IF 산출물. LLM 준비 상태에 의존하지 않는다.
- LLM: 추천과 요청 설명을 별도 큐에서 bounded 교대 처리. Base 하나와 feature_v5/rule_v2 adapter를 공유한다. 출처·DSL·saved fact output 검증 후만 결과를 저장한다.

실제 모델 연결은 `model_adapters.py`, 공통 계산은 `feature_runtime.py`다. v12 IF/K-means는 `window_worker.py`에서 별도 연속 1분 이력으로 실행한다. GPU 모델 생성이 없으면 추천 성공을 만들지 않는다. 설명의 원인·기여도·SHAP을 만들어 내지 않는다.

```text
python -m collector.input_runner
python -m detector.service
python -m trainer.runner
python -m llm_worker
# Collector 측정값만 읽으며 추가 고객 API 호출을 하지 않음
python -m collector.report --watch-seconds 300
```

`feature_worker`는 이전 queue backlog의 복구·공통 라이브러리용으로 남겨 두었고 새 기본 컨테이너는 아니다. 기존 worker를 멈춘 후 전환한다. monitor/window는 선택 프로필이다. 보관 데이터·산출물·계정 경로를 초기화하지 않는다.

큐는 PostgreSQL at-least-once이며 최종 저장은 current attempt + 유효 lease token을 검사한다. 오래된 실행이 결과를 채택할 수 없다. 큐에는 BODY/인증정보 대신 immutable ID·버전을 넣는다. `benchmark_queue.py`의 기존 feature/detection/training 경로는 과거 비교 도구이며 실제 고객 API/전체 판정 지연을 포함한 측정을 대신하지 않는다.

실제 운영 탐지 품질·원천 발생 지연·GPU 적재는 서버에서 별도 확인한다. [검증 기록](VALIDATION.md)에는 로컬 모형 생성/API와 실제 CPU 모델을 구분해 기록한다.

### 기존 CPU / PostgreSQL 간소 데모

원본을 보존하며 별도 데모 source에 초당 N개 Process를 보내 실제 CPU Isolation Forest 판정을 저장한다. 서버 입력은 `EC_TRANSACTION_MONITORING.csv`, `EC_PROCESS_MONITORING.csv`, `EC_MESSAGE_BODY.csv`다. 권한 문제·사전 검사·마이그레이션·재개·결과 확인은 [데모 실행 안내](../infra/DEMO_REPLAY.md)를 따른다.

## API / CSV 공통 전체 프로세스

현재 권장 입력은 [입력 전환 안내](../infra/SWITCHABLE_INPUTS.md)다. `009_switchable_inputs.sql` 적용 후 `/detection/pipeline`에서 API·네 CSV·일시정지를 선택한다. 같은 sourceId·피처·Rule·모델을 유지하며 Rule 추천/사용자 채택 과정도 포함한다. Message 모니터링 82,351행과 BODY 참조 579행을 구분한다. 실제 BODY 원문은 수집하지 않는다.
