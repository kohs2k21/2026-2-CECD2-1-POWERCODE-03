Created: 2026-11-15T03:55:00+09:00
Updated: 2026-11-15T18:00:00+09:00
Author: Collector Claude Code(builder)
Status: current — 결정 반영(WebUI 11/15 Q1~Q8, Detector C1~C5). Collector 코드·migration 030 구현. API·화면 연결은 WebUI

# Collector 운영 계측 계약

- 목적: 운영 현황 화면의 `측정 불가` 중 Collector가 채우는 값(원천 발생 시각, 수집 지연·처리량, 시스템 자원)의 정의·저장·집계 SQL·API 필드. Collector는 `server/backend`를 고치지 않는다 — WebUI가 이 문서대로 `GET /api/operations/*`에 넣는다. 판정 쪽 시각은 Detector([detection-decision.md](detection-decision.md))
- 근거 계약: [operations-ui.md](operations-ui.md) overview·collection·latency·storage 절, [trainer-service.md](trainer-service.md) 거래량 집계, [rules-ui.md](rules-ui.md) `timeBasis`, [incidents.md](incidents.md) 원본 시간대
- 원칙: 계측하지 않은 값은 `null`(개요는 키 생략) + 사유 코드. 추정값·고정값·0 대체·성공 대체 금지([operations-ui.md](operations-ui.md) null 의미와 같다). 음수 지연을 0으로 자르지 않는다
- 공통 창: 지연 집계는 기존 latency API 창 `1h`(`OPERATIONS_WINDOW`, `persisted_at >= clock_timestamp() - interval '1 hour'`)를 같이 쓴다. 분위수는 p50·p95(`percentile_cont`, 선형 보간)

## 1. 원천 발생 시각(ESB 시각)

### 1.1 저장 열 — 관측 INSERT 때 채운다 [확정 C1]

- 시점: Collector가 관측을 INSERT하는 같은 트랜잭션(`collector/storage.py` `ingest_observation` — 공유 입력 `input_store.admit`, 이전 CSV 재생 `replay_csv.py`, 시연·측정 경로가 모두 이 함수). B 참조 행(`input_store.admit` B 분기)은 늘 `absent`
- 해석 규칙: `trainer/source_time.py` `process_source_time`을 그대로 부른다(`collector/source_clock.py`, 시간대 읽기도 Trainer와 같은 `rule_v3_capture.source_timezone`). T·P·M 모두 `end_time_raw` → `start_time_raw`. 오프셋이 있으면 그대로, 없으면 원천 `ops_sources.clock_basis.status='verified'`의 `timezone`, 그 밖은 `Asia/Seoul` 가정. Detector 사건 묶음 `occurred_time`과 같은 시각이 나온다(시험 `test_collector_metrics.py`)
- M의 `process_datetime_raw`(`PROCESS_DT`)는 뜻이 확인되지 않아 쓰지 않는다 [확정 Q1, 열린 항목 7절]
- `clock_basis`는 INSERT 트랜잭션 안에서 매번 읽는다. 운영자가 시간대를 지정하면(1.3) 그 뒤 INSERT부터 반영되고 이미 저장된 관측은 바뀌지 않는다

| 열 | 값 |
|---|---|
| `source_log_occurred_at` | 해석한 UTC 시각. 못 읽으면 NULL. `persisted_at`·`api_received_at`으로 대신하지 않는다 |
| `source_log_time_raw` | 해석에 쓴 원문 문자열 그대로. `unparsed`는 해석하지 못한 원문(END 먼저, 문자열만), `absent`는 NULL |
| `clock_status` | `offset`·`verified_tz`·`assumed_tz`(해석 근거) / `unparsed`(값은 있으나 해석 실패 — 형식 불일치, 검증됐지만 시간대 이름이 없거나 잘못됨) / `absent`(두 열 모두 값 없음 — NULL·빈 문자열·공백·`\N`, B 포함). 030 이전 행은 `unknown`(시각 없음)으로 남는다 |
| `parsed_times` | 시각 있음 `{"rule":"source-time-v1","field":"end"\|"start","basis":"offset"\|"verified_tz"\|"assumed_tz"}`, 시각 없음 `{"rule":"source-time-v1"}`. 시각 값 자체는 두지 않는다(정본은 위 열). 030 이전 행은 `{}` |
| `api_received_at` | API 조회 응답 수신 시각 = `api_client` receipt `finished_at` [확정 C5]. CSV 재생·이전 재생·시연 경로는 NULL(030에서 NOT NULL 해제). 이전 `api_runner.py`는 관측을 저장하지 않아(스냅샷만) 해당 없음 |
| `source_updated_*` | 원천 수정 시각이 없어 NULL |

- 값 목록은 이 계약이 정본. `clock_status`에 CHECK를 두지 않는다(030 이전 `unknown` 행과 표 전체 검사 잠금 회피)
- 기존 행 보정(backfill) 없음 [확정 Q8]: 고정된 관측은 UPDATE가 막히고(004 `erd_v03_protect_observation`), 일부만 보정하면 기준이 섞인다. `esb_occurred_at` 기본값 전환은 이 범위 밖(각 화면 세션 판단)

### 1.2 인덱스 — migration 030 [확정 Q7]

- `data_observations_source_occurred_idx ON data_observations(source_id, source_log_occurred_at) WHERE source_log_occurred_at IS NOT NULL`
- 용도: 거래량 집계·수식 추천 `esb_occurred_at`([trainer-service.md](trainer-service.md) DB 요구), 2.3 `hourlyBasis=source_event`. `source_log_occurred_at >= $1` 같은 조건이 `IS NOT NULL`을 함의해 이 부분 인덱스를 쓴다
- 일반 `CREATE INDEX`(018과 같은 근거). 적용 중 관측 INSERT가 기다리므로 WebUI가 Collector 입력을 일시정지하고 백업한 뒤 적용한다. 기존 행은 모두 NULL이라 항목 0개지만 표 전체를 한 번 읽는다(시간 ∝ 표 크기, 측정은 `infra/database/README.md` 030 절)

### 1.3 원천 시간대 검증 흐름 [확정 C2]

1. 원천에 시간대가 없으면(문자열에 오프셋 없음) 관측은 `assumed_tz`로 저장된다. 지연 지표(3절)에서 빠지고 `excludedCounts.assumedTz`로만 센다
2. 운영자가 latency API `clockCheck`(3.4)를 본다 — `Asia/Seoul` 가정 아래 저장 지연이 작은 양수로 모이면 가정이 맞다는 근거, 몇 시간 단위로 치우치거나 음수가 많으면 다른 시간대
3. 운영자가 Collector 컨테이너에서 지정한다: `docker compose run --rm input clock-basis verify --timezone <IANA> --actor <운영자 id>`. 시간대는 zoneinfo가 아는 IANA 이름만(경로·빈 값 거절, DB 접속 전 검사). 원천(`--source-key`)이 없으면 만들지 않고 `input_source_not_initialized`로 거절
4. 그 뒤 INSERT되는 관측부터 `verified_tz`가 되어 지연 값이 나온다. 이전 관측은 `assumed_tz` 그대로(보정 없음)
5. 되돌리기: `clock-basis reset --actor <id>` → `{"status":"unknown","resetAt":…,"resetBy":…}`. 그 뒤 INSERT부터 다시 `assumed_tz`

- 저장 형태: `ops_sources.clock_basis = {"status":"verified","timezone":"<IANA>","method":"persist_lag_check","verifiedAt":"<UTC ISO8601, DB 시계>","verifiedBy":"<actor>"}`. 읽는 쪽(Collector·Detector·Trainer)은 `status`·`timezone`만 본다
- Collector 실행 경로(`input_runner`·`input_store`·`storage`·`resource_sampler`)는 `clock_basis`를 바꾸지 않는다(시험으로 확인). CLI는 `powercode_collector` 역할로 실행되고 `ops_sources`에는 trigger가 없어 UPDATE 권한(002 GRANT)으로 충분하다 — migration 변경 없음
- 원천에 오프셋이 있으면 `offset`이라 지정 없이도 값이 나온다

## 2. 수집 처리량 — `GET /api/operations/collection?sourceId=` 추가 필드

| 필드 | 정의·SQL |
|---|---|
| `input.api.lastSuccessAt`·`lastAttemptAt` | 현재 실행 `ops_input_runs.last_success_at`·`last_attempt_at`(기존 쿼리의 `r` 조인). 없으면 null |
| `input.api.sinceLastSuccessMs` | `extract(epoch FROM clock_timestamp()-r.last_success_at)*1000`(DB 시계). 성공 없음 null. 오류 원문·`error_code`는 싣지 않는다(기존 `lastError` 제외 규칙) |
| `input.csv.actualRate` | 실행 `status='running'`일 때 최근 60초 저장 관측 수/60(행/초): `count(*) FROM data_observations WHERE source_id=$1 AND persisted_at>=clock_timestamp()-interval '60 seconds'`. 한 원천에 Collector는 하나라 그 창의 저장은 현재 실행 것이다. 실행이 running이 아니면 null, 저장 0이면 0. 목표 `rate`와 비교용(중복 행은 저장되지 않아 세지 않는다) |
| `layers[].sourceTimeKnownCount` | 기존 계층 집계 쿼리에 `count(source_log_occurred_at)::float8` 추가(같은 스캔) |
| `layers[].lastSourceEventAt` | 같은 쿼리에 `max(source_log_occurred_at)`. 없으면 null |
| `hourly`(쿼리 `hourlyBasis=source_event`일 때만) | 기본 `persisted_at`은 지금과 같다. `source_event`이면 `{layer:'T', basis:'source_log_occurred_at', items[24], timeUnknownCount}`: 칸 조건 `o.source_id=$1 AND o.layer='T' AND o.source_log_occurred_at>=h.hour_start AND o.source_log_occurred_at<h.hour_start+interval '1 hour'`(1.2 인덱스). `timeUnknownCount` = 같은 24시간 창에 `persisted_at`이 들고 `source_log_occurred_at IS NULL`인 T 관측 수. 다른 값은 400 `unknown_operations_filter` |

- CSV 재생은 원천 시각이 과거라 최근 24시간 ESB 시각 칸이 비는 것이 정상이다(0 = 실제로 센 0)
- CSV 재생 collector·persist 단계 소요는 범위 밖, 새 저장 없음 [확정 Q6] — latency `stages` collector·persist는 지금처럼 CSV에서 null

## 3. 수집 지연 — `GET /api/operations/latency?sourceId=` 추가 필드

### 3.1 입력 구간 `data_input_origins.ingest_phase` [확정 C4, migration 030]

| 값 | 언제 |
|---|---|
| `replay` | `mode='csv'` 모든 행 |
| `initial` | `mode='api'` 실행의 첫 성공 조회 — 그 실행에 커밋된 성공 조회가 없음(`ops_input_runs.last_success_at IS NULL`), 또는 마지막 성공이 마지막 제어 행 변경보다 이른 경우(`last_success_at < ops_input_controls.updated_at` — 일시정지 → 재개는 같은 실행을 다시 쓰고, 같은 모드로 다시 `select`하거나 구성만 바꿔도 제어 행이 바뀌어 해당한다). 그 응답의 거래는 수집이 (다시) 시작되기 전부터 진행 중이었을 수 있다 |
| `live` | 그 밖의 `mode='api'` 조회 |
| NULL | 030 이전 행 |

- 결정 위치: `collector/input_runner.py` `ingest_phase`. 입고 트랜잭션의 fence가 제어 행 `revision`과 실행 `next_position`을 잠근 채 다시 확인하므로(성공 조회마다 `next_position`이 1 늘어난다) 읽은 값이 그 트랜잭션의 값이다
- CHECK `data_input_origins_ingest_phase_check`(NULL 허용, `replay` ⇔ `csv`), `NOT VALID`로 추가해 기존 행 검사 잠금이 없다
- Collector 프로세스 재시작·API 오류 뒤 첫 성공은 `live`다(실행·제어 행 변경이 없으면 그 동안의 지연은 실제 수집 지연이다)

### 3.2 `sourceToStored` — 원천 → 저장 지연

- 표본 = 창 안 후보 관측 중 `clock_status IN ('offset','verified_tz')` 행의 부호 있는 원값 `persisted_at − source_log_occurred_at`(ms)
- 후보 관측: `o.source_id=$1 AND o.layer='P' AND o.revision_no=1 AND o.persisted_at>=clock_timestamp()-interval '1 hour' AND i.mode='api' AND i.ingest_phase='live'`(`data_input_origins i`). 실행 식별 없는 행(`revision_no` NULL)은 빠진다. 계층을 P로 둔 것은 Detector 원천 → 판정 지연(판정 = P)과 같은 모집단으로 비교하려는 것
- 음수 표본(원천 시계가 DB 시계보다 앞섬)은 분위수에서 빼고 `skewCount`로 센다(Detector와 같은 규칙). 0으로 자르지 않는다
- `assumed_tz` 포함 여부 [확정 Q2: 뺀다, 사용자 결정 남음 — Detector는 포함 의견]: 아래 SQL의 `verified` 조건 한 줄(`clock_status IN ('offset','verified_tz')`)에 `'assumed_tz'`를 더하면 포함으로 바뀐다. 그래서 `excludedCounts.assumedTz`를 늘 낸다

```sql
WITH c AS (
  SELECT o.clock_status, i.ingest_phase,
         extract(epoch FROM o.persisted_at - o.source_log_occurred_at) * 1000 AS ms
    FROM data_observations o JOIN data_input_origins i ON i.observation_id = o.id
   WHERE o.source_id = $1 AND o.layer = 'P' AND o.revision_no = 1
     AND o.persisted_at >= clock_timestamp() - interval '1 hour' AND i.mode = 'api'
     AND (i.ingest_phase = 'live' OR i.ingest_phase IS NULL)),
v AS (SELECT * FROM c WHERE ingest_phase = 'live'
        AND clock_status IN ('offset','verified_tz') AND ms IS NOT NULL)   -- 포함 조건 한 줄
SELECT (SELECT count(*) FROM v WHERE ms >= 0)::integer AS samples,
       (SELECT percentile_cont(.5)  WITHIN GROUP (ORDER BY ms) FROM v WHERE ms >= 0) AS p50,
       (SELECT percentile_cont(.95) WITHIN GROUP (ORDER BY ms) FROM v WHERE ms >= 0) AS p95,
       (SELECT count(*) FROM v WHERE ms < 0)::integer AS skew,
       (SELECT count(*) FROM c WHERE ingest_phase='live' AND clock_status='assumed_tz')::integer AS assumed_tz,
       (SELECT count(*) FROM c WHERE ingest_phase='live' AND clock_status='unparsed')::integer AS unparsed,
       (SELECT count(*) FROM c WHERE ingest_phase='live' AND clock_status='absent')::integer AS absent,
       (SELECT count(*) FROM c WHERE ingest_phase IS NULL)::integer AS legacy_unknown;
```

- 응답(최상위 키, 기존 `stages`·`endToEnd`는 그대로):

```json
{"sourceToStored": {"window": "1h", "samples": 0, "p50Ms": null, "p95Ms": null, "skewCount": 0,
  "excludedCounts": {"assumedTz": 0, "unparsed": 0, "absent": 0, "legacyUnknown": 0},
  "reason": "no_live_api_samples"}}
```

- `reason`(분위수가 null일 때만, 그 밖 null) 순서: `samples > 0` → null / `skew > 0` → `source_clock_skew`(0 이상 표본 없이 음수만) / live 후보가 있음(`assumedTz + unparsed + absent > 0`) → `source_clock_unverified`(1.3 지정 전) / 그 밖 → `no_live_api_samples`(CSV 재생 중·일시정지·첫 조회만 있음·030 이전 행만 있음)
- `excludedCounts`는 숫자만 낸다. 새 화면 문구는 두지 않는다 [확정 Q2]
- 성능: 표본은 `(source_id, persisted_at)` 인덱스로 1시간 창만 읽고 출처 표는 PK로 찾는다. 새 인덱스 불필요

### 3.3 Detector 원천 → 판정 지연과의 관계

- overview `endToEndLatencyMs`(p50 하나)와 latency `endToEnd`는 Detector 몫이다. Detector 측정 조건: 관측 `clock_status IN ('offset','verified_tz')` ∧ 출처 `ingest_phase='live'` ∧ 첫 revision ∧ 재판정 아님 ∧ `source_log_occurred_at` 있음. Detector는 이 조건이 참(`latency_status='measured'`)일 때만 판정 행에 관측의 `source_log_occurred_at`을 그대로 복사한다(다시 해석하지 않음). 조건이 거짓이면 판정 행의 `source_log_occurred_at`·`source_latency_ms`는 NULL이고 `latency_status='unavailable'`이다(판정 행에 원천 시각 있음 ⇔ `measured`, [detection-decision.md](detection-decision.md) 18절). 원천 시각은 관측 행에서 읽는다. 음수 규칙(`skewCount`·`source_clock_skew`)도 3.2와 같다
- 3.2와 모집단이 같아(P·첫 revision·live·같은 근거) 두 지연의 차이가 판정 대기·처리 시간이다

### 3.4 `clockCheck` — 시간대 가정 확인용 [확정 C3]

- 지연 지표가 아니다. 1.3 2단계에서 운영자가 본다
- 표본 = 3.2 후보 중 `ingest_phase='live' AND clock_status='assumed_tz'`의 부호 있는 `ms`(음수 포함 그대로 분위수)

```json
{"clockCheck": {"window": "1h", "assumedTimezone": "Asia/Seoul", "sourceClockBasisStatus": "unknown",
  "sampleCount": 0, "p50Ms": null, "p95Ms": null, "negativeRatio": null}}
```

- `sourceClockBasisStatus` = 원천 설정 `ops_sources.clock_basis->>'status'`(`unknown`·`verified`, 없으면 null). 관측마다의 근거 `data_observations.clock_status`(Detector telemetry `sourceClockStatus`)와 다른 값이다. `negativeRatio` = 음수 표본 수/`sampleCount`. `sampleCount` 0이면 세 값 null

## 4. 시스템 자원

### 4.1 표본기 [확정 Q4]

- 별도 compose 서비스 `resources`(`python -m collector.resource_sampler`, profile `resources`, CPU 0.1·메모리 128m, `database` 네트워크만, 루트 파일시스템 읽기 전용·`cap_drop: [ALL]`·`no-new-privileges`·`/tmp` tmpfs). WebUI storage API 4.3 반영 뒤 켠다(5절 배포 순서). DB 역할은 `powercode_collector` 재사용(같은 세션 소유, 새 로그인 불필요). Docker 소켓·외부 명령 없음
- 주기 60초(`RESOURCE_SAMPLE_SECONDS`, 10~3600). 매 주기 한 트랜잭션: 자원 1행 + 디스크 키별 1행 + 보존 정리(자원 24시간, 저장공간 7일 — `storage_key IN ('database_data','artifacts') AND target_kind='filesystem'`만 지운다). `measured_at`은 DB 시계
- DB 접속 실패는 다음 주기에 다시 연결(오류 이름만 출력). 표본을 만들지 못한 값은 측정 불가 행으로 남고 이전 값을 반복하지 않는다

| 지표 | 계산 | 측정 불가 사유 |
|---|---|---|
| CPU % | `/proc/stat` 첫 `cpu` 줄의 두 표본 차이: total = user…steal 8칸, idle = idle + iowait, (1 − Δidle/Δtotal) × 100 | `cpu_first_sample`(시작 첫 표본), `cpu_counter_reset`(역행·0 증가), `proc_stat_unavailable`(파일 없음·형식 다름) |
| 메모리 | `/proc/meminfo` used = `MemTotal − MemAvailable`, total = `MemTotal`(kB만) | `meminfo_unavailable` |
| 디스크 | 읽기 전용 바인드 경로 `statvfs`: capacity = `f_blocks×f_frsize`, used = `(f_blocks−f_bfree)×f_frsize`, available = `f_bavail×f_frsize`. 사용률 = used/(used+available)(df Use%와 같은 분모) | `mount_not_visible`(경로 미설정·없음·마운트 지점 아님 — 컨테이너 루트 값을 대신 적지 않음), `statvfs_failed` |

- 범위: 일반 Docker에서 컨테이너 안 `/proc/stat`·`/proc/meminfo`는 호스트 전체 값이지만 lxcfs 등이 있으면 컨테이너 값이다. 대상 서버에서 확인하기 전에는 `scope='host_proc_unverified'`, 운영자가 확인한 뒤 `RESOURCE_PROC_SCOPE=host`
- 디스크 키: `artifacts`(산출물 루트, 기본 바인드) / `database_data`(DB 데이터 디렉터리가 있는 파일시스템 — `compose.resources-db.yaml`을 함께 줄 때만 바인드, `RESOURCE_DATABASE_DATA_HOST_PATH`). 없으면 `unavailable`·`mount_not_visible`
- `ops_storage_measurements` 값(002 표 재사용): `target_kind='filesystem'`, `filesystem_key='dev:<st_dev>'`(같은 파일시스템 중복 합산 금지용), `database_bytes` NULL(API가 `pg_database_size`로 실측), `writer_access_verified=false`, `warning_spec_version='disk-warning-v1'`, `warning_state` = `normal` / `warning`(80% 이상) / `critical`(90% 이상) / `unknown`(측정 불가) [확정 Q5 — 기존 계약·와이어프레임에 값이 없어 80/90]

### 4.2 `ops_resource_measurements` — migration 030

| 열 | 뜻 |
|---|---|
| `id` uuid PK, `measured_at` timestamptz(DB 시계) | |
| `scope` | `host_proc_unverified` / `host` |
| `cpu_status`(`measurement_status`), `cpu_percent`(0~100), `cpu_reason_code`, `sample_interval_ms` | measured ⇔ 값·간격 있음·사유 없음 |
| `memory_status`, `memory_used_bytes`, `memory_total_bytes`(> 0), `memory_reason_code` | measured ⇔ 두 값 있음·사유 없음, used ≤ total |

- 지표별 상태라 첫 표본에서 CPU만 측정 불가이고 메모리는 측정될 수 있다
- 권한(`infra/scripts/database-roles.sql`): `powercode_collector` SELECT·INSERT·DELETE(UPDATE 없음) on `ops_resource_measurements`·`ops_storage_measurements`, `powercode_api` SELECT. 불변 trigger 없음(보존 정리 때문)

### 4.3 API 필드

- 신선도: 최신 행 `measured_at`이 `clock_timestamp() − interval '180 seconds'`보다 오래됐거나 `clock_timestamp() + interval '5 seconds'`보다 미래면 그 값은 없음으로 본다(주기 × 3). 이전 값을 최신처럼 보이지 않는다
- overview(키 생략 규칙 그대로):
  - `cpuPercent` = 신선한 최신 `ops_resource_measurements` 행이 `cpu_status='measured'`일 때 `cpu_percent`
  - `memoryPercent` = 같은 행이 `memory_status='measured'`일 때 `memory_used_bytes / memory_total_bytes × 100`
  - `diskPercent` = `storage_key='database_data'` 신선한 최신 행이 `measured`일 때 `used_bytes / (used_bytes + available_bytes) × 100` [확정 Q3]. `artifacts` 값으로 대신하지 않는다

```sql
SELECT r.cpu_status, r.cpu_percent, r.memory_status, r.memory_used_bytes, r.memory_total_bytes, r.measured_at
  FROM ops_resource_measurements r
 WHERE r.measured_at BETWEEN clock_timestamp() - interval '180 seconds' AND clock_timestamp() + interval '5 seconds'
 ORDER BY r.measured_at DESC LIMIT 1;
```

- storage [확정 Q3]:
  - `disk` = `database_data` 신선한 최신 measured 행(기존 필드 모양 `capacityBytes`·`usedBytes`·`availableBytes`·`warningState`·`measuredAt`), 아니면 null. 지금 구현의 "모든 키 중 최신 measured 행"을 이 조건으로 좁힌다
  - `disks[]` = 키 `database_data`, `artifacts` 순서로 2개 고정. 키마다 최신 행(`DISTINCT ON (storage_key) … ORDER BY storage_key, measured_at DESC`, 002 `ops_storage_measurements_time_idx`): `{storageKey, status, filesystemKey, capacityBytes, usedBytes, availableBytes, warningState, reasonCode, measuredAt}`. 행이 없으면 `status:'unavailable', reasonCode:'not_measured'`, 오래되면 `status:'unavailable', reasonCode:'measurement_stale'`(값 null, `measuredAt`은 그 행 시각) — 두 사유는 API가 붙이는 값

## 5. 다른 계약 영향

- 큐 payload([queues.md](queues.md)): 변경 없음
- migration `030_collector_metrics.sql`: 1.2 인덱스, `api_received_at` NOT NULL 해제, `ingest_phase` 열·CHECK, 4.2 표. 역할 스크립트 재적용 필요. 배포 순서 migration 030 → 역할 스크립트 → Collector → WebUI storage API 4.3 반영(`disk` = `database_data`, `disks[]`) → `resources` 서비스(`infra/database/README.md` 030 절). 지금 `readStorage`는 키를 가리지 않고 가장 최근 measured 행을 골라, 반영 전에 켜면 `artifacts` 값이 `disk`로 보일 수 있다
- [operations-ui.md](operations-ui.md): collection·latency·storage·overview 필드는 이 문서 2~4절 — WebUI가 연결할 때 그 문서에 옮긴다
- [rules-ui.md](rules-ui.md)·[models-ui.md](models-ui.md)·[trainer-service.md](trainer-service.md): `esb_occurred_at`은 030 이후 새 관측부터 값을 가진다. 기존 행은 `timeUnknownCount`로 남는다
- [incidents.md](incidents.md): Detector가 원문을 직접 해석해 동작은 그대로. "원천 시각 열이 늘 NULL" 같은 현재 사실 문장은 030 이후 새 관측부터 달라진다
- `parsed_times`를 원천으로 쓰지 않는 계약([relation-rules.md](relation-rules.md), [detection-decision.md](detection-decision.md) D1, [llm-recommendation-job.md](llm-recommendation-job.md), [rules-ui.md](rules-ui.md))은 그대로 원문 열을 읽는다 — `parsed_times`에는 시각 값이 없다

## 6. 검증

- 단위: `worker/tests/test_collector_metrics.py`(원천 시각 5종·END 우선·Trainer/Detector 같은 결과, INSERT 열 값, 입력 구간, API 수신 시각, CLI 검사, `/proc` 고정 문자열·측정 불가 경로)
- DB: `infra/tests/erd-v03-collector-metrics.mjs`(`erd-v03.mjs`가 호출) — 030 적용·기존 행 유지·CHECK·권한·재실행 실패·DBML parity

## 7. 열린 항목

- `PROCESS_DT` 뜻 — 원천 담당자 확인. 확인 전에는 M도 END → START만 쓴다
- `assumed_tz`를 지연 지표에 넣을지 — 사용자 결정(3.2 한 줄로 바뀜)
- 첫 revision이 START_TIME만 있는 행(진행 중)은 원천 처리 시간이 지연에 섞인다. Detector와 같은 조건을 유지하고, 필요하면 두 쪽 모두 `parsed_times->>'field'='end'` 조건을 더한다
- 대상 서버 `/proc` 범위(`host` 확인), DB 데이터 디렉터리 호스트 경로 — 배포 때 운영자 확인
