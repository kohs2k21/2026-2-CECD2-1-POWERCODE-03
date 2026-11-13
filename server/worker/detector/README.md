# Detector

`python -m detector.runner`는 승인 구성의 P 관측에 Rule과 IF를 적용하고 `run_process_decisions`에 입력 manifest 기준으로 멱등 저장한다. 일부 구성요소가 미실행이면 `partial`/`unavailable`과 사유를 남긴다. 적용·복원은 `applications.py`가 산출물 검증, 모델 준비, 활성 revision CAS 순으로 처리한다. 재시작 시 활성 포인터를 재확인한다.

실제 IF 로더·점수 계산·메모리 캐시는 `../model_adapters.py`에 연결되었다. 승인된 구성의 파일 해시를 확인하고 로드한 뒤 P 관측 한 건마다 점수를 계산한다. 현재는 P 숫자 필드만 변수 계산에 사용한다. 최신 v12 시간창 모델은 ../window_worker.py의 별도 경로로 연결한다. 수집 완료·시각·도착 이벤트 계약이 확인된 stream만 평가하며 margin과 미평가 상태를 그대로 기록한다. T/M as-of 관계와 GPU 상주를 주장하지 않는다. 원천 시간대가 미확정이면 발생→판정 지연을 산출하지 않는다. 판정 조회 API는 DB 기반이고 기존 데모 SSE와 별도다.

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
4. `build_evidence`와 `decision_columns`로 판정 행을 저장한다. 하드룰 입력 `HardRuleSelection`은 `read_hard_rule_snapshot`으로만 만들고 판정과 evidence가 같은 객체를 쓴다. v3 판정은 `relationInputs`·`relationNotes`(관계 입력을 읽은 판정), `protocolReview`(프로토콜 방법이 T), `relationRecheck`(재판정)를 더한다(`../../contracts/detection-decision.md` 11.4). 재판정 판정은 `ops_pipeline_measurements` 행을 넣지 않는다.

소프트룰 필드 정의는 `methods/registry.py`의 `SOFT_RULE_FIELDS`를 쓴다. 모델을 읽지 못하거나 다른 source의 모델이면 그 모델만 `not_configured`로 미판정이 되고 다른 방법 결과는 남는다. 산출물 파일이 없어도 `not_configured`이고, 파일이 변조됐으면 `failed`·`artifact_invalid`다.

방법 스레드는 공유 상태에 쓰지 않는다. 측정값·변수값은 결과와 함께 돌려주고, 기한 안에 채택된 결과의 값만 evidence·`data_feature_materializations`에 쓴다.

시도가 값 오류로 실패하면 원인 코드(`rule_set_hash_mismatch` 등, v3 추가 코드는 `../../contracts/detection-decision.md` 11.6)가 `run_jobs.error_code`, 시도, 큐 `last_error`에 남는다. v03은 기존대로 `detection_processing_failed`다.

## detect 작업 생성 전환

detect 작업은 `jobs.py`의 `enqueue_observation` 한 곳에서만 만든다. 호출자는 Collector(`collector/storage.py`, `collector/input_store.py`), 데모(`demo/runtime.py`), Detector의 옛 메시지 복구(`runner.py`)다. 새 작업의 계약 버전은 환경 변수 `DETECT_JOB_CONTRACT`(`v03`, `v04`)가 정한다. 값이 없으면 코드 기본은 `v03`이고, `compose.yaml`·`compose.demo.yaml`의 기본은 `v04`다(아래 전환 순서 1의 선행 조건 충족, 11/13).

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
