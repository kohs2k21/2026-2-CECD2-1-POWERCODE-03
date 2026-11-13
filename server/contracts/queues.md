# PostgreSQL 작업 큐 계약 (서비스 전환 v2)

| 논리 큐 | 실제 테이블 | 생산자 | 소비자 | 메시지 참조 |
|---|---|---|---|---|
| detection | powercode_queue.detection_logs | 검증된 관측 Collector / legacy 복구 / 재판정 탐색 Detector(`recheck_job`) | Detector | jobId·observationId·sourceId·inputManifestId/hash (v03 작업은 configVersionId 포함, v04 작업은 없음) |
| training | powercode_queue.training_jobs | API | Trainer | jobId (train/evaluate/import_csv/recommend_relations) |
| llm_recommendation | powercode_queue.llm_recommendation_jobs | API | LLM | jobId (recommend_feature/rule) |
| llm_explanation | powercode_queue.llm_explanation_jobs | API | LLM(주 소비자 교대 + 설명 전용 소비자, [llm-recommendation-job.md](llm-recommendation-job.md) GPU 차례 절) | jobId (explain / explain_protocol — [migration 027 확정 11/14 사용자] [protocol-explanation-job.md](protocol-explanation-job.md) 8절) |
| 과거 feature 큐 | powercode_queue.feature_observations | 이전 버전 | Detector의 복구 경로 | observationId·sourceId |

검증된 P를 저장할 때 활성 source/config가 맞으면 불변 입력 manifest·v03 detect job·queue item을 관측과 함께 커밋한다. 활성 구성 없는 관측은 waiting item으로 보관한다. Detector가 해당 source의 승인 구성으로 작업을 고정한다. 실제 고객 API의 미확정 monitoring snapshot은 이 검증된 관측 프로토콜로 임의 변환하지 않는다. DETECT_JOB_CONTRACT=v04(Compose 기본, 11/13 — 코드 기본은 v03)이면 승인 운영 입력은 구성 없이 v04 detect job으로 커밋하고 waiting item을 만들지 않는다. 승인 구성이 아닌 구성을 고정한 입력(input-validation·데모)은 v03이다. 같은 manifest의 detect job은 v03·v04를 합쳐 하나다(`enqueue_observation`이 보장, DB의 `run_jobs_v04_detect_idx`는 v04끼리만 강제). 단 v03 작업끼리는 지금처럼 구성마다 따로 있을 수 있다(`run_jobs_v03_detect_idx`).

재판정(relation-rules.md 6.3·6.5) [제안]: Detector 재판정 탐색이 같은 detection 큐에 v04 detect job을 넣는다. payload 형식은 그대로(jobId·observationId·sourceId·inputManifestId/hash, inputOrigin은 첫 판정과 같은 규칙)이고 manifest만 다르다(`process-input-v2-recheck`, `readiness.relation`). 관측마다 1회(`run_input_manifests UNIQUE(source_id, content_hash)`).

직접 전달은 commit 후 jobId/hash만 전달하고 polling과 동일한 queue id를 claim한다. 전달 실패/중복으로 새 job을 만들지 않는다. 입력은 DB의 immutable manifest에 고정하며 실행 시 queue payload와 대조한다.

전달은 at-least-once다. 짧은 claim TX → DB 밖 계산 → current attempt/queue token/유효 임대를 잠금 확인한 최종 TX로 판정·event·ack를 확정한다. 긴 작업은 큐와 attempt 임대를 함께 갱신한다. 이전 attempt나 만료 token은 결과를 채택할 수 없다. 실패 재시도·최대 시도 제한을 적용한다.

이전 training 테이블의 추천 backlog는 LLM이 종류별 필터로 복구한다. Trainer는 train/evaluate/import_csv(외부 가공 CSV 반입, [prepared-csv-import](prepared-csv-import.md))와 recommend_relations(규칙 관계 추천 후보, 실험 기능 — 관리자 전용·결과 JSON만, [trainer-service.md](trainer-service.md) 규칙 관계 추천 절)만 가져간다. 새 LLM 큐는 독립 물리 테이블이고 Redis/Kafka를 필수로 추가하지 않았다. 이전 worker를 멈춘 뒤 006 migration과 새 worker를 적용한다. 과거 처리 이력을 삭제하거나 v03 입력을 다시 쓰지 않는다.

## 변경 제안 — Issue #94 (#94 제안 범위에서는 위 현행 표·문단을 수정하지 않음)

Created: 2026-11-02T18:00:00+09:00 / Updated: 2026-11-05T16:26:00+09:00(detection 행, Issue #112 Detector 세션 builder) / Author: Claude Code (Trainer 세션 builder) / Status: draft(이 절만)

| 논리 큐 | 메시지 참조 변경 | 근거 |
|---|---|---|
| training | jobId (train/evaluate/import_csv/**evaluation_set/compare**) — #74 제안 승계(`job_kind`는 011에서 추가됨). train의 제외 구간·`timeBasis`는 큐 payload가 아니라 `run_jobs.input_spec`([trainer-service.md](trainer-service.md) 변경 제안 절). payload `{schemaVersion: 1, jobId, enqueuedAt}` 불변 | [config-comparison-evaluation.md](config-comparison-evaluation.md), [trainer-service.md](trainer-service.md) |
| llm_recommendation | jobId (`recommend_feature`/`recommend_rule` = **폐지 경로**, 기존 backlog 읽기 보존 / `recommend_methods` = 보류(011 enum 유지, 접수 미구현) / **`recommend_thresholds`** = 신규 소프트룰 임계값 일괄 추천, API 접수 구현(#121), DB는 migration 017(#124). payload `{schemaVersion: 1, jobId, enqueuedAt}` 불변) | [llm-recommendation-job.md](llm-recommendation-job.md) |
| detection | v04 detect payload에는 configVersionId가 없음(v03 작업은 유지). 판정 작업이 적용할 **룰 세트 리비전·활성 모델 집합 리비전·운영 설정(`detectionPolicy`) 리비전** 세 가지는 Detector가 claim 시점(시도 시작)에 운영 상태 한 건으로 함께 읽어 고정(payload에 넣지 않음 — Detector 계약 detection-decision.md(#92), 일관 읽기는 [model-version.md](model-version.md) 활성 집합 절). **고정 단위는 시도(attempt)** [확정 11/03 #92 U5]: 재시도는 그 시도 시작 시점에 세 리비전을 새로 읽으며, 첫 시도의 리비전을 큐 payload·불변 입력에 고정하는 작업 단위 안은 채택되지 않음 | [rule-set.md](rule-set.md), [model-version.md](model-version.md) |

- 자동 비교 평가(10/21 §4.8): API가 train 성공 결과의 트랜잭션 확정 **뒤** 별도 트랜잭션으로 `compare` 작업을 training 큐에 접수(학습 요청 관리자 승계, `Idempotency-Key = auto-compare:<configVersionId>:<evaluationSetId>`, 비교 상대 = 같은 `modelKey`의 활성 버전 — 11/02 9.4). 접수 실패는 학습 성공을 되돌리지 않고 구성 목록 `latestComparison`에 `auto: true, status: failed_to_enqueue`로 표시
- PostgreSQL 세션이 찾은 계약 누락 3건(10/21 인수인계 4절) — 큐 관점: (1) `run_jobs_v03_contract` CHECK는 kind별 queue·producer·role을 열거하므로 `recommend_thresholds`를 `llm_recommendation` 분기에 추가(`011:13-32` 재정의본 기준) (2) `erd_v03_validate_job` llm_spec 필수 kind·LLM 완료 결과 필수 kind·`run_llm_results.kind CHECK`에 `recommend_thresholds` 추가(`011:36-37, 39-41, 60, 76-77`) (3) `ml_config_versions` 불변 트리거는 `004_feature_pipeline.sql:84-96`에 이미 존재 — 추가 불필요. 상세는 [trainer-service.md](trainer-service.md) 변경 제안 절
