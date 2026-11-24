# Detector

`python -m detector.runner`는 승인 구성의 P 관측에 Rule과 IF를 적용하고 `run_process_decisions`에 입력 manifest 기준으로 멱등 저장한다. 일부 구성요소가 미실행이면 `partial`/`unavailable`과 사유를 남긴다. 적용·복원은 `applications.py`가 산출물 검증, 모델 준비, 활성 revision CAS 순으로 처리한다. 재시작 시 활성 포인터를 재확인한다.

실제 IF 로더·점수 계산·메모리 캐시는 `../model_adapters.py`에 연결되었다. 승인된 구성의 파일 해시를 확인하고 로드한 뒤 P 관측 한 건마다 점수를 계산한다. 현재는 P 숫자 필드만 변수 계산에 사용한다. 최신 v12 시간창 모델은 ../window_worker.py의 별도 경로로 연결한다. 수집 완료·시각·도착 이벤트 계약이 확인된 stream만 평가하며 margin과 미평가 상태를 그대로 기록한다. T/M as-of 관계와 GPU 상주를 주장하지 않는다. 발생→판정 지연은 Collector가 정한 관측 `clock_status`(offset·verified_tz·assumed_tz = KST로 해석한 시각)·실시간 조회(`live`)·첫 revision·재판정 아님일 때만 판정 행에 `measured`로 저장하고(`../../contracts/detection-decision.md` 18절), 판정 저장 소요는 COMMIT 뒤 `ops_pipeline_measurements` stage `judgment_store` 행으로 남긴다(19절, migration 031). 판정 조회 API는 DB 기반이고 기존 데모 SSE와 별도다.

판정 구조 v2(`../../contracts/detection-decision.md`)의 순수 판정 로직은 `policy.py`의 v2 파서, `hard_rules.py`·`soft_rules.py`(방법 실행), `decision.py`(조합·등급·위험도·판정 행 열), `evidence_v4.py`(evidence v4·중간 판정 기록 payload)에 있다.

## v03·v04 실행 경로

작업의 `contract_version`이 실행 경로를 정한다. 설정이 아니라 작업 행을 보므로 전환 전후에 남은 작업은 만들어진 경로대로 끝난다.

| 작업 | 구성 | 판정 | 기록 |
|---|---|---|---|
| v03 | 작업에 고정(`input_config_version_id`) | `computation.py` `compute`, `parse_policy`(v1·legacy) | evidence v3 |
| v04 | 없음(`NULL`) | `attempt_v04.py` | evidence v4, 판정 `config_version_id` NULL, 중간 판정 기록 |

v04 시도 1회의 순서.

1. `operational_state.py`가 뷰 `ops_operational_state` 한 문장으로 세 리비전을 읽고, 그 리비전의 룰 세트 스냅샷을 다시 읽어 `content_hash`를 재계산해 대조한다. hash 버전은 `rule_set_content_hash_version`을 따른다. 정책 NULL은 리비전 0일 때만 내장 기본 정책이다. 뷰 0행, 그 밖의 NULL, hash 불일치, 미지 규칙, 방법 0개, k 범위 위반은 판정 행 없이 시도 실패다. 재시도는 그 시도 시작 시점에 다시 읽는다. 재판정 작업(`readiness.relation`)이면 먼저 그 참조를 확인한다(`recheck_reference`).
   - 룰 세트 v3(hash 버전 3): 관계 규칙·`hard:transaction-failed`가 선택됐거나 프로토콜 기능이 켜진 판정만 같은 원본·같은 거래 ID의 TRANSACTION·PROCESS·MESSAGE를 같은 트랜잭션에서 한 번 읽는다(`relation_inputs.py`). `relation_plan.py`가 그 입력으로 관계·프로토콜 규칙을 미리 판정하고, 거래 행이 와야 판정할 수 있는 것은 방법이 아니라 evidence `relationNotes`로 남긴다(`../../contracts/relation-rules.md` 3.3·5절, `protocol-rules.md` 5절).
2. `method_runner.py`가 방법 단위로 실행한다. 룰은 슬롯 1개에서 차례로, 모델은 모델마다 슬롯 1개로 돈다. 기한(`deadlineMs`)을 넘긴 결과는 버리고 `cause=timeout`에 판정 시점 측정값 `elapsedMs`를 남긴다. 슬롯이 없으면 `capacity`다.
3. 결과가 도착할 때마다 `attempt_v04.py`의 `InterimRecorder`가 첫 이상·근거 추가를 `run_decision_interims`에 쓴다. 미도착 방법이 없으면 쓰지 않는다(판정 행이 최종이다). 대체됨·최종 없음은 DB 트리거가 쓴다.
4. 기한 밖에서 `attribution.py`가 ML 결과마다 기여도 `methodResults[ml].attribution`을 더한다(이상 T만 계산, 나머지는 `skipped`, 예외는 `failed`로 흡수 — 판정·종합·중간 판정 기록은 그대로, `../../contracts/detection-decision.md` 6.1). 이어서 `build_evidence`와 `decision_columns`로 판정 행을 저장한다. 하드룰 입력 `HardRuleSelection`은 `read_hard_rule_snapshot`으로만 만들고 판정과 evidence가 같은 객체를 쓴다. v3 판정은 `relationInputs`·`relationNotes`(관계 입력을 읽은 판정), `protocolReview`(프로토콜 방법이 T), `relationRecheck`(재판정)를 더한다(`../../contracts/detection-decision.md` 11.4). 재판정 판정은 `ops_pipeline_measurements` 행을 넣지 않는다.
5. 성공 저장 트랜잭션 하나의 순서: 소유권 잠금 확인(`lock_training_ownership`, 작업·시도·큐 행 `FOR UPDATE`) → 변수 캐시 INSERT → `finish.py` `finish_detect_success`가 pipeline 한 묶음으로 detector 측정 행 INSERT(첫 판정만) → 판정 INSERT → 시도 `succeeded` → 작업 `succeeded`(`result_summary`) → 큐 ack를 보내고 Sync 한 번으로 결과를 받는다 → ack가 1행이 아니면 `training_queue_lease_lost`로 ROLLBACK → COMMIT → COMMIT 뒤 `judgment_store` 측정 행(첫 판정만, 별도 트랜잭션). 판정 INSERT가 잡는 전역 잠금 81736 구간의 클라이언트 왕복은 묶음 1번 + COMMIT 1번이다. trainer `_finish`와 같은 값을 쓰지만 두 번째 소유권 확인과 중첩 savepoint는 하지 않는다. 첫 확인의 행 잠금이 COMMIT까지 유지되고, 남는 변화인 임대 만료는 판정 INSERT 트리거·작업 완료 트리거(시도 임대)와 ack 조건(큐 임대)이 다시 보기 때문이다. 묶음 안 문장이 트리거 예외·제약 위반으로 실패하면 뒤 문장은 실행되지 않고 그 오류가 예외로 올라와 ROLLBACK되며, 호출자(`runner.py`)가 지금처럼 trainer `_finish`로 시도 실패를 기록한다. detector 측정 행은 판정 INSERT 앞으로 옮겼다. 그 소요는 시작부터 저장 문장을 만드는 때(`attempt_v04._success_statements`)까지이고, 한 건 저장에서는 소유권 확인·변수 캐시 INSERT 뒤, 측정·판정 INSERT를 보내기 직전이다(묶음 저장은 아래 "묶음 저장"의 측정). 실패 경로와 v03 경로는 trainer `_finish` 그대로다. 시도 1회는 `prepare_attempt`(1~4, 저장 직전까지) → `save_attempt`(5, 한 건 저장)이고 `run_attempt`는 둘을 잇는다. 다중 작업자의 묶음 저장(아래 "묶음 저장")은 4까지 건마다 같고 5만 여러 건을 한 트랜잭션으로 한다.

소프트룰 필드 정의는 `methods/registry.py`의 `SOFT_RULE_FIELDS`를 쓴다. 모델을 읽지 못하거나 다른 source의 모델이면 그 모델만 `not_configured`로 미판정이 되고 다른 방법 결과는 남는다. 모델 버전 구성은 `schemaVersion` 정수 1·3만 읽고 그 밖(키 없음 포함)은 `unsupported_config_schema` 사유로 같은 미판정이다(`../../contracts/model-version.md` schemaVersion 3 판독 규칙 절). v03 경로(`load_context`)는 `schemaVersion`을 보지 않는다. 산출물 파일이 없어도 `not_configured`이고, 파일이 변조됐으면 `failed`·`artifact_invalid`다.

방법 스레드는 공유 상태에 쓰지 않는다. 측정값·변수값은 결과와 함께 돌려주고, 기한 안에 채택된 결과의 값만 evidence·`data_feature_materializations`에 쓴다.

시도가 값 오류로 실패하면 원인 코드(`rule_set_hash_mismatch` 등, v3 추가 코드는 `../../contracts/detection-decision.md` 11.6)가 `run_jobs.error_code`, 시도, 큐 `last_error`에 남는다. v03은 기존대로 `detection_processing_failed`다.

큐 재시도를 다 쓴(dead) detection 행의 작업을 `failed`·`queue_exhausted`로, 그 작업의 만료된 시도를 `expired`로 바꾸는 정리(`pg_queue.reconcile_dead_jobs`)는 판정 루프 반복마다가 아니라 첫 반복에서 한 번, 그 뒤로는 직전 정리에서 30초(`runner.RECONCILE_SECONDS`)가 지난 반복에서 한 번 돈다. 그래서 dead가 된 뒤 작업 상태가 바뀌기까지 그 간격과 반복 한 번만큼 늦을 수 있다. detection 큐의 대조 문장은 큐 id = jobId(넣는 모든 경로: `jobs.enqueue_observation`·`enqueue_observations`·`enqueue_recheck`)라 EXISTS에 `q.id=j.id`를 더해 큐 pkey로 찾는다(`pg_queue._reconcile_detection`). 그래서 비용이 dead 큐 행 수에 비례하지 않는다. training·LLM 큐 문장은 그대로다.

## 판정 작업자 수(서비스)

`python -m detector.service`(Compose `detector`)는 직접 전달 HTTP 서버 1개와 판정 루프 `DETECTOR_WORKERS`개(`runner.run_workers`, 스레드, 작업자마다 DB 연결 1개)를 돌린다. 큐가 비었을 때 작업자는 0.1초 기다린다(`service.main`의 `poll_seconds`, `run()` 기본 1초는 그대로). 직접 전달(`POST /detect`)은 한 번에 1건(`capacity`)이고 연결 1개를 요청마다 다시 쓴다(`service.DeliveryConnection` — 처음 쓸 때 열고, 연결 오류·끊김이면 닫고 다음 요청에서 다시 연다, 요청마다 COMMIT/ROLLBACK으로 열린 트랜잭션을 남기지 않음). `/health`는 요청마다 짧은 연결을 연다. `python -m detector.runner`(`run()`, 통합 시험·`compose.demo.yaml`)는 지금처럼 단일 루프다.

- `DETECTOR_WORKERS`: 1~8 정수, 없으면 4. 범위 밖이거나 정수가 아니면 시작하자마자 실패한다. Compose `detector` 환경에 `DETECTOR_WORKERS: ${DETECTOR_WORKERS:-4}`로 넘기므로 `.env`에서 바꾼다
- `DETECTOR_BATCH_SIZE`: 작업자 1개가 detect 메시지를 한 번에 임대·저장하는 최대 수. 1~64 정수, 없으면 8. 범위 밖이거나 정수가 아니면 `DETECTOR_WORKERS`처럼 HTTP 서버를 열기 전에 실패한다. 1이면 지금까지의 한 건 경로 그대로다. Compose `detector` 환경에 `DETECTOR_BATCH_SIZE: ${DETECTOR_BATCH_SIZE:-8}`로 넘긴다. 아래 "묶음 저장"
- 작업자들은 같은 큐를 `FOR UPDATE SKIP LOCKED`로 나눠 가진다. 결과 저장은 작업 행·시도·큐 임대 토큰을 잠금 확인하므로 작업자 수와 무관하게 작업마다 판정은 하나다(`../../contracts/queues.md`)
- 정리 담당은 0번 작업자 하나다. 첫 반복과 그 뒤 30초마다 임대가 끝난 채 시도를 다 쓴 큐 행을 dead로 바꾸고(`pg_queue.expire_exhausted_leases`) 이어서 `reconcile_dead_jobs`를 부른다. 작업자들의 claim은 이 dead 처리 UPDATE를 건너뛴다(`claim(expire_exhausted=False)`). 그런 행은 claim 조건(`attempts < max_attempts`)에서 이미 빠지므로 다시 임대되지 않고, dead 표시만 최대 30초 늦다
- 건너뛰기: 직전 반복에서 메시지를 처리한 작업자는 신청 claim·활성 구성·원천 조회와 과거 feature 복구를 마지막 실행 뒤 1초(`runner.FIXED_REFRESH_SECONDS`)가 지난 반복에서만 한다. 큐가 빈 반복 다음에는 매번 한다. 그래서 적체 중에는 과거 feature 복구가 작업자마다 약 1초에 1건으로 줄고, 원천 범위 claim은 1초 안에 읽은 원천을 쓴다
- 방법 실행 슬롯: 룰 레인·모델 슬롯과 실행 풀(`policy.py` `_SLOTS`·`_POOLS`)은 프로세스 전체가 함께 쓰고 비차단으로 잡으므로, 슬롯 수가 동시 판정 수보다 적으면 겹친 판정의 룰·모델이 `capacity`(미판정)로 끝난다. 그래서 서비스는 시작 때(HTTP 서버를 열기 전) `policy.configure_capacity(작업자 수 + 1)`로 룰·ML 슬롯을 동시 판정 수(기본 5, +1은 직접 전달 몫)에 맞춘다. 그 밖의 프로세스(Trainer 비교 평가, 단일 `run()`)는 기본 2 그대로다(`../../contracts/detection-decision.md` D20)
- 모델이 켜져 있으면 ML 채점이 동시에 최대 슬롯 수(기본 5)만큼 돌 수 있어 채점 CPU가 단일 루프 때보다 최대 그 배수까지 늘 수 있다(컨테이너 `cpus` 기본 2)
- 작업자 하나가 예외로 끝나면 traceback을 stderr 로그에 남기고 나머지를 멈추고(전체 최대 10초 대기) HTTP 서버를 닫은 뒤 0이 아닌 코드로 끝난다(컨테이너 재시작). 시작 단계 `prepare_active`는 작업자를 띄우기 전에 한 번만 한다
- 처리량 상한 [추정, 미측정]: 판정 INSERT 트리거(`publish_decision_event`, migration 012)가 전역 advisory lock `(81736, 0)`을 COMMIT까지 잡아 판정 이벤트 순서를 커밋 순서로 맞춘다(`../../contracts/pipeline.md` SSE). 그래서 판정 INSERT부터 COMMIT까지는 작업자 수와 무관하게 한 번에 하나씩 지나간다. v04 성공 저장은 이 구간의 문장을 pipeline으로 묶어 클라이언트 왕복을 2번(묶음·COMMIT)으로 줄였다(위 v04 순서 5). 중간 판정 기록 INSERT(`publish_decision_interim_event`, migration 012:200)도 같은 잠금을 잡아 이 줄에 함께 선다. 이 구간이 3~4.5ms라면 판정마다 한 번 잡을 때 상한은 초당 약 220~330건이다. 묶음 저장은 잠금 획득·COMMIT을 묶음마다 한 번으로 줄인다(아래). 그 효과는 배포 뒤 다시 잰다

## 묶음 저장(다중 작업자, `DETECTOR_BATCH_SIZE`)

전역 잠금 81736 획득과 COMMIT(WAL flush)을 판정마다가 아니라 여러 판정에 한 번 하려고, 다중 작업자(`run_workers` → `runner._Loop`)는 detect 메시지를 여러 건 한 번에 임대하고 v04 첫 판정을 트랜잭션 하나로 저장한다.

- 임대: 작업자 반복의 첫 claim(`job_kinds=("detect",)`)만 `pg_queue.claim_many(limit=DETECTOR_BATCH_SIZE)`로 지금 있는 것을 최대 그만큼 가져온다. 채우려고 기다리지 않는다. 행마다 `claim`과 같은 조건(`FOR UPDATE SKIP LOCKED`, `attempts < max_attempts`, 행마다 새 임대 토큰, 60초 임대)이고 돌려주는 순서(= 임대 순서)는 `available_at, created_at`(같으면 id)다. 원천 범위 claim·대기 입력 claim·직접 전달(`service.py`)·단일 루프 `run()`은 늘 한 건(`claim`)이다
- 판정: 가져온 것이 2건 이상이면 `runner.process_detection_batch`가 건마다 차례로 한 건 경로와 같은 단계(작업 claim·시도 행, 건마다 heartbeat, 입력 manifest 확인, 재판정 참조, 운영 상태 고정, 방법 실행, 중간 판정 기록, evidence — `attempt_v04.prepare_attempt`)를 거친다. v04 첫 판정만 저장 직전에서 모은다. v03 작업·재판정 시도·옛 메시지 복구는 그 자리에서 한 건 경로로 끝까지 처리한다. 저장 전 실패는 그 건만 한 건 경로와 같은 처리(trainer `_finish` 실패 분기 → 큐 fail)를 한다. 1건이면 한 건 경로 그대로다
- 저장: 모은 건이 2건 이상이면 `attempt_v04.save_batch` → `finish.finish_detect_batch`가 트랜잭션 하나에서 pipeline 두 묶음으로 보낸다. 첫 묶음은 모든 건의 소유권 확인 → 모든 건의 변수 캐시 INSERT → 모든 건의 detector 측정 행 INSERT이고, Sync 뒤 소유권 확인이 하나라도 0행이면 판정 INSERT 전에(잠금 81736 전) 묶음 실패다. 소유권을 잃은 건의 판정 INSERT가 81736을 쥔 채 다른 저장자가 잠근 작업 행을 기다려 교착(deadlock_timeout 동안 전체 판정 저장 정지)이 되지 않게 하려는 것이다. 둘째 묶음은 건마다 판정 INSERT·시도·작업 완료·큐 ack(임대 순서)이고, 그 뒤 COMMIT을 한 번 한다. 잠금 81736을 잡은 뒤에는 판정·완료 문장만 돈다. 판정 이벤트 순번은 묶음 안에서 임대 순서다
- 묶음 실패: Sync 뒤 소유권 확인 행이 없거나 ack가 1행이 아니거나 어느 문장이든 DB 오류면 묶음 전체를 ROLLBACK하고 각 건을 임대 순서대로 `save_attempt`(한 건 저장)로 다시 저장한다. 문제가 있는 건만 지금과 같은 원인 코드로 실패하거나(예 트리거 거부 → 작업 `queued`·시도 `failed`·큐 `pending`, 원인 모두 `detection_processing_failed` — 통합 시험이 묶음 크기 1과 같은지 본다) 임대 상실로 버려지고 나머지는 저장된다
- 임대: 모은 건은 자기 heartbeat가 20초마다 시도·큐 임대를 연장하고, 저장(묶음·한 건)이 끝난 뒤 어느 경로든(예외 포함) 모두 멈춘다. 저장 직전에 heartbeat가 소유권 상실을 알린 건은 한 건 경로의 같은 확인처럼 `detection_lease_lost`로 실패 처리한다. 아직 시작하지 않은 건의 큐 임대는 claim 때 받은 60초이므로 앞 건 판정이 길면(기한 `deadlineMs`는 최대 30초) 끝날 수 있다. 그래서 20초(`runner.LEASE_RENEW_SECONDS`)마다 남은 건의 큐 임대를 연장하고, 연장에 실패한 건(이미 끝남)은 시도를 만들기 전이라 묶음에서 뺀다
- 측정: 묶음 저장한 판정의 `judgment_store` 값은 묶음 저장 소요(진입 → COMMIT)이고 판정마다 같다. 묶음 크기는 evidence `telemetry.details.judgmentStoreBatchSize`에 남는다(한 건 저장이면 키 없음, `../../contracts/detection-decision.md` 19절). detector 측정 행의 소요는 묶음 트랜잭션에 들어간 직후 `save_batch`가 저장 문장을 만드는 때(`_success_statements`, 첫 묶음의 소유권 확인 전)까지라 앞 건 판정을 기다린 시간을 포함하고, 소유권 확인·변수 캐시 INSERT 시간은 포함하지 않는다(한 건 저장은 둘 다 포함). 작업 `queueWaitMs`도 앞 건 판정을 기다린 시간을 포함한다
- 동시성: 한 작업자 안의 판정은 차례로 하므로 동시 판정 수와 방법 실행 슬롯(`configure_capacity(작업자 수 + 1)`)은 그대로다

## detect 작업 생성 전환

첫 판정 detect 작업은 `jobs.py`의 `enqueue_observations`(일괄, `pg_queue.enqueue_many` 한 문장)에서만 만든다. `enqueue_observation`은 관측 1개짜리 호출이다. 호출자는 Collector(`collector/storage.py`, `collector/input_store.py` `admit_batch` — 묶음 입고는 `enqueue_observations`를 직접 부른다), 데모(`demo/runtime.py`), Detector의 옛 메시지 복구(`runner.py`)다. 두 형태 모두 큐 id = payload `jobId`(작업 id)다. 재판정 작업은 `enqueue_recheck`가 같은 규칙으로 만든다. 새 작업의 계약 버전은 환경 변수 `DETECT_JOB_CONTRACT`(`v03`, `v04`)가 정한다. 값이 없으면 코드 기본은 `v03`이고, `compose.yaml`·`compose.demo.yaml`의 기본은 `v04`다(아래 전환 순서 1의 선행 조건 충족, 11/13).

- `v03`: 지금과 같다. 구성이 없는 입력은 대기한다.
- `v04`: 승인 운영 경로의 입력은 구성 없이 v04 작업이 된다. 입력 제어나 호출자가 승인 구성이 아닌 구성을 고정한 입력(input-validation, 데모)은 v03으로 남는다. 구성을 기다리던 입력도 v04 작업으로 바뀐다.
- 큐 payload v04에는 `configVersionId`가 없다.
- 같은 입력(manifest)의 v03·v04 작업은 하나만 만든다(v03 작업끼리는 구성마다 따로 있을 수 있다). 다른 버전 작업이 이미 있으면 failed여도 그 작업을 재사용한다. 그래서 프로세스마다 설정이 어긋나도 판정이 둘이 되지는 않지만, 입력마다 경로가 갈리므로 전환은 아래 순서로 한다.
- 옛 메시지 복구는 그 관측에 판정이 이미 있으면 v04 작업을 만들지 않고, 메시지의 입력 hash가 맞지 않으면 거부한다.

전환 순서.

1. 선행 조건(사용자 결정 11/05)을 확인한다: migration 016 적용, API·WebUI의 v04 판독(구성 NULL 판정) 배포, `queues.md` 조율 완료. 끝난 뒤 한 번에 전환한다. `queues.md` 조율은 #112에서 끝났다.
2. `enqueue_observation`을 부르는 프로세스를 멈춘다. `compose.yaml`은 `collector`·`input`·`detector`, `compose.demo.yaml`은 `detector`와 실행 중인 `sender`다.
3. 그 프로세스 전부에 `DETECT_JOB_CONTRACT=v04`를 넣고 다시 시작한다. `compose.yaml`의 작업자는 `server/infra/.env`의 값 하나를 함께 받는다. 데모 스택은 `.env.demo`의 값을 받으므로 같은 DB를 쓰면 같은 값을 넣는다. 데모 `sender`의 입력은 승인 구성이 아닌 구성을 고정하므로 설정과 무관하게 v03이다. 남은 v03 작업은 그대로 처리된다.
4. 되돌릴 때는 같은 순서로 `v03`을 넣는다. 이미 만든 v04 작업은 v04 경로로 끝난다.

## migration 016 적용 절차

`server/infra/migrations/016_detect_v04_contract.sql`은 `run_jobs`·`run_process_decisions`·`run_llm_results`·`ops_pipeline_measurements`·`ops_rule_set_revisions`와 뷰 `ops_operational_state`를 COMMIT까지 잠근다. 작업자 트랜잭션은 표를 다른 순서로 잠그므로 켠 채 적용하면 교착으로 어느 한쪽이 취소될 수 있다. 반드시 작업자를 멈추고 적용한다.

1. DB를 쓰는 작업자를 모두 멈춘다. `compose.yaml`은 `api`·`llm`·`trainer`·`collector`·`input`·`detector`·`incidents`·`recheck`(profile `relation-recheck`로 켠 경우)·`monitor`·`window`, 같은 DB를 쓰는 데모 스택(`compose.demo.yaml`)은 `api`·`detector`와 실행 중인 `sender`다. 처리 중인 시도는 끝나거나 임대가 만료되기를 기다리지 않아도 된다. 재시작 뒤 큐가 다시 가져간다.
2. 001~015가 적용돼 있고 룰 세트에 r0 말고 저장된 리비전이 없는지 확인한다. 저장된 리비전이 있으면 016이 전체를 되돌린다. 저장된 행을 보존하고, 새 규칙 2개를 어떻게 켤지 룰 세트 소유 세션과 정한 뒤 016의 r1 단계를 고친다.
3. 표·함수 소유자로 `psql -v ON_ERROR_STOP=1 -f server/infra/migrations/016_detect_v04_contract.sql`을 1회 실행한다. 단일 트랜잭션이라 중간 오류는 전체 ROLLBACK이다. `database-roles.sql` 재적용은 필요 없다.
4. `SELECT rule_set_revision,rule_set_content_hash_version FROM ops_operational_state`가 `1, 2`인지 확인한다.
5. 작업자를 다시 시작한다. Compose 기본은 `v04`이므로 v03으로 두려면 `.env`에 `DETECT_JOB_CONTRACT=v03`을 넣는다.

자동 down migration은 없다. v04 행이 생긴 뒤에는 NOT NULL을 복원할 수 없고 r1은 불변 행이라 001~015 상태로 내릴 수 없다.

v03 코드 경로는 016 없이도 돈다. v04 작업 실행은 016이 만든 `rule_set_content_hash_version`과 구성 NULL 조건이 필요하다.

## 사건 묶음 작업(층 C)

`python -m detector.incidents_job`은 판정 행을 원본마다 사건(`run_incidents`)으로 묶는다(`../../contracts/incidents.md`). Compose 서비스 `incidents`가 `--interval 60`(초)으로 계속 돈다. Detector와 같은 이미지·DB 연결(`DETECTOR_DATABASE_URL`, 없으면 `DATABASE_URL`)·사용자를 쓰고, 상태 확인 HTTP가 없어 healthcheck는 끈다.

- 시작: `docker compose -f server/infra/compose.yaml up -d incidents`. 로그는 회차마다 원본별 JSON 한 줄(`docker compose logs incidents`)
- 한 번만: `docker compose -f server/infra/compose.yaml run --rm incidents python -m detector.incidents_job --once [--source <uuid>]`
- 이전에 `docker compose run -d --name pwc-incidents … detector …`로 띄운 컨테이너가 있으면 먼저 멈추고 지운다(같은 잠금 81741로 줄을 서므로 둘이 돌아도 결과는 같지만 detector healthcheck를 물려받아 unhealthy로 보인다)

## 관계 규칙 재판정 탐색 작업(룰 세트 v3)

`python -m detector.recheck_job`은 거래 행 도착을 기다리던 v04 첫 판정(relationNotes `held`·`deferred_to_transaction`)을 거래 행 도착 확정(최소 지연 30초) 뒤 찾아, 현재 룰 세트로 사전 검사해 종합이 바뀌는 판정만 v04 재판정 작업(manifest `process-input-v2-recheck`)을 1회 넣는다(`../../contracts/relation-rules.md` 6절). 잠금 81742, 커서는 `run_recheck_runs`(migration 024 [제안]). 1회 보장은 `run_input_manifests UNIQUE(source_id, content_hash)`. 재판정 시도는 Detector 서비스가 일반 detect 작업처럼 처리하고 새 판정 행을 더한다(옛 행은 그대로, 측정 행 없음).

- 시작: migration 024 적용 뒤 `docker compose -f server/infra/compose.yaml --profile relation-recheck up -d recheck`(`--interval 30`). 연결·사용자·로그 형식은 `incidents`와 같다
- 한 번만: `docker compose -f server/infra/compose.yaml --profile relation-recheck run --rm recheck python -m detector.recheck_job --once [--source <uuid>]`
- 보류 기록이 없거나(v2 룰 세트 포함) 새 거래 행·판정이 없으면 아무것도 넣지 않는다. API v3 저장을 열기 전에 켠다(relation-rules.md 8절 배포 순서)
