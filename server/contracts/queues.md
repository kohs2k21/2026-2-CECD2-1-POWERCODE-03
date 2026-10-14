# PostgreSQL 큐 계약 v1

| 논리 큐 | 실제 테이블 | 생산자 | 소비자 | 메시지 참조 |
|---|---|---|---|---|
| `feature.observations.v1` | `powercode_queue.feature_observations` | Collector | Feature worker | `observationId`, `sourceId` |
| `detection.logs.v1` | `powercode_queue.detection_logs` | Feature worker | Detector | `observationId`, `sourceId`, `configVersionId`, `inputManifestHash` |
| `training.jobs.v1` | `powercode_queue.training_jobs` | Express API | Trainer | `jobId` |

Collector는 BODY/인증정보를 제외한 관측을 DB에 저장한 같은 트랜잭션에서 검증된 P의 변수 큐 메시지를 등록한다. Feature worker는 승인된 구성의 변수를 저장하고 탐지 메시지를 등록한 뒤 변수 메시지를 완료한다. Trainer 작업도 DB 작업 행과 큐 메시지를 한 트랜잭션으로 기록한다. 큐에는 원천 JSON이나 민감 필드를 넣지 않는다.

메시지 전달은 at-least-once다. 소비자는 짧은 임대로 claim하고, 결과와 ack를 같은 트랜잭션으로 확정한다. ack/fail은 현재 토큰과 유효한 임대를 확인한다. 긴 학습은 큐와 `run_job_attempts`의 임대를 함께 갱신한다. 만료/실패는 제한 횟수 후 dead로 전환한다. 각 큐의 대기 수·최고 지연·실패 수를 별도로 관찰한다.

현재 PostgreSQL 구현으로 먼저 전체 경로를 측정한다. Redis Streams/Kafka 도입 여부는 동일 입력 부하에서 지연·운영 부담을 비교한 뒤 결정한다. 과거 Collector가 탐지 큐에 직접 넣은 레거시 메시지는 Detector가 처리할 수 있지만 새 생산자는 변수 큐를 사용한다.
