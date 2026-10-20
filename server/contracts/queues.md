# PostgreSQL 작업 큐 계약 (서비스 전환 v2)

| 논리 큐 | 실제 테이블 | 생산자 | 소비자 | 메시지 참조 |
|---|---|---|---|---|
| detection | powercode_queue.detection_logs | 검증된 관측 Collector / legacy 복구 | Detector | jobId·observationId·sourceId·configVersionId·inputManifestId/hash |
| training | powercode_queue.training_jobs | API | Trainer | jobId (train/evaluate/import_csv) |
| llm_recommendation | powercode_queue.llm_recommendation_jobs | API | LLM | jobId (recommend_feature/rule) |
| llm_explanation | powercode_queue.llm_explanation_jobs | API | LLM | jobId (explain) |
| 과거 feature 큐 | powercode_queue.feature_observations | 이전 버전 | Detector의 복구 경로 | observationId·sourceId |

검증된 P를 저장할 때 활성 source/config가 맞으면 불변 입력 manifest·v03 detect job·queue item을 관측과 함께 커밋한다. 활성 구성 없는 관측은 waiting item으로 보관한다. Detector가 해당 source의 승인 구성으로 작업을 고정한다. 실제 고객 API의 미확정 monitoring snapshot은 이 검증된 관측 프로토콜로 임의 변환하지 않는다.

직접 전달은 commit 후 jobId/hash만 전달하고 polling과 동일한 queue id를 claim한다. 전달 실패/중복으로 새 job을 만들지 않는다. 입력은 DB의 immutable manifest에 고정하며 실행 시 queue payload와 대조한다.

전달은 at-least-once다. 짧은 claim TX → DB 밖 계산 → current attempt/queue token/유효 임대를 잠금 확인한 최종 TX로 판정·event·ack를 확정한다. 긴 작업은 큐와 attempt 임대를 함께 갱신한다. 이전 attempt나 만료 token은 결과를 채택할 수 없다. 실패 재시도·최대 시도 제한을 적용한다.

이전 training 테이블의 추천 backlog는 LLM이 종류별 필터로 복구한다. Trainer는 train/evaluate/import_csv(외부 가공 CSV 반입, [prepared-csv-import](prepared-csv-import.md))만 가져간다. 새 LLM 큐는 독립 물리 테이블이고 Redis/Kafka를 필수로 추가하지 않았다. 이전 worker를 멈춘 뒤 006 migration과 새 worker를 적용한다. 과거 처리 이력을 삭제하거나 v03 입력을 다시 쓰지 않는다.
