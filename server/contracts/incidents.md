Created: 2026-11-12T20:00:00+09:00
Updated: 2026-11-14T02:24:00+09:00
Author: Claude Code(Detector builder)
Status: current — 실험 기능(층 C). DB migration 019·WebUI 사건 보기와 맞춤

# 층 C 거래 간 사건 묶음 계약(Detector 쪽 초안)

- 범위: Detector 최종 판정 중 이상·이상 징후를 원천 키와 시간 간격으로 묶어 "사건"을 추가만 하는 표에 남기는 주기 작업. 읽기 규칙(해제·구성원 수·병합·이어짐)은 화면·API가 같은 결과를 내도록 여기서 정한다
- 순수 로직: `worker/detector/incidents.py`(DB 입출력 없음), 시험 `worker/tests/test_incidents.py`
- DB 쪽: `worker/detector/incidents_store.py`(`run_once`, migration 019 형태), 진입점 `python -m detector.incidents_job`(Compose 서비스 `incidents`, `--interval 60`. 한 번만은 `--once`. Detector 서비스와 별도 프로세스), 실제 PostgreSQL 시험 `worker/tests/integration_incidents.py`
- 그래프 표현: 실행 그래프 계약(`process-graph.md`)의 층 C 예약 어휘 `incident` 노드·`member_of` 간선(거래 → 사건)을 쓴다
- 표기
  - **[확정 11/12 사용자]**: 사용자 결정
  - **[합의]**: 세션 간 합의(Detector·DB·WebUI·API)
  - **[합의 DB]**: DB migration 019에 반영한 형태
  - **[제안]**: Detector 제안. 이견이 없으면 이 기준으로 구현
  - 근거는 모두 **대리 지표·실측 아님**(원천 CSV, 판정 행 없이 잼). 이 문서에는 정성 서술만 두고 수치는 Git 밖 측정 기록에 둔다

## 1. 목적

- 층 C 진행 [확정 11/12 사용자]. 판정은 PROCESS 1건 단위라 같은 장애로 생긴 이상이 화면에 많은 건으로 흩어진다. 같은 원인 키·가까운 시각의 이상을 사건 1개로 묶어 화면이 사건 단위로 보여 줄 수 있게 한다
- 묶음은 Detector가 판정과 **분리된 주기 작업**으로 한다 [확정 11/12 사용자]. 판정 시도(3초 마감, fail-closed, `policy.py` `deadlineMs` 3000)를 늦추거나 실패시키지 않는다 [합의]
- 실험 기능이다. 묶음 기준은 기준 버전(`criteria_version`)으로 남기고 데이터를 보며 바꾼다

## 2. 입력(읽는 것)

### 2.1 구간 — 판정 이벤트 커서 [합의 DB]

- `run_decision_events.id`(identity PK) 커서로 읽는다: `id > 직전 cursor_to`, `event_kind = 'final'`만, `decision_id`로 `run_process_decisions`를 조인. 중간 기록 이벤트(`first_anomaly`·`evidence_added`·`superseded`·`no_final`)는 제외
- 근거: 판정 INSERT마다 트리거 `publish_decision_event`가 `pg_advisory_xact_lock(81736,0)` 아래에서 이벤트 행을 넣으므로(006·012) id 순서가 커밋 순서다. `decided_at` 시각 구간은 늦게 커밋된 판정을 놓치고, `run_process_decisions`에는 `decided_at`으로 시작하는 인덱스가 없다(018은 `(process_observation_id, decided_at DESC, id DESC)`)
- 시작점: 같은 `(source_id, criteria_version)`의 `run_incident_runs` 최대 `cursor_to`. 원본은 `run_process_decisions.execution_id → data_executions.source_id`로 거른다(이벤트 표에 원본 열이 없음)
- 경계: `cursor_from`은 배타(직전 `cursor_to`), `cursor_to`는 포함(이번에 읽은 이벤트 id 최댓값). 원본으로 거른 뒤 id 순서로 상한(`batch_limit`, 기본 1000)만큼 읽는다 [제안]
- 시작점이 없으면(첫 실행) 0 = 과거 final 이벤트 전체 [제안: 임시 기본값, 열린 항목 8]
- 새 이벤트가 없으면 실행 행을 넣지 않는다(`idle`). 읽은 이벤트가 있으면 구성원이 0건이어도 실행 행을 넣어 커서를 넘긴다(자격 없는 판정을 매 회차 다시 읽지 않게) [제안]
- 실행별 최신 판정만 후보 [제안]: 읽는 시점에 그 실행의 최신 판정(8절 정렬 `o.revision_no DESC NULLS LAST, d.decided_at DESC, d.event_sequence DESC`)이 아닌 판정은 넣지 않는다(`not_latest`로 셈). 이미 해제된 판정을 구성원으로 만들지 않기 위해서다. 최신 판정이 나중에 커밋되면 먼저 넣은 구성원은 8절 해제 규칙으로 읽는다

### 2.2 판정별 값

| 값 | 출처 | 비고 |
|---|---|---|
| `grade` | `evidence->'composite'->>'grade'`, `evidence->>'schemaVersion' = '4'`일 때만 | v3 판정은 등급이 없어 None(자격 없음). v04 전환 전까지 운영 판정 대부분이 v3라 구성원이 거의 없다 |
| 구성원 시각 원문 | 판정 PROCESS 관측의 `data_processes.end_time_raw`, `start_time_raw` | 3절 |
| 원본 시간대 | `ops_sources.clock_basis->>'timezone'`, `clock_basis->>'status' = 'verified'`일 때만 [제안]. 검증되지 않은 원본은 가정 시간대 `Asia/Seoul`(3절) [확정 11/12 사용자] | 시간대 없는 원문에 붙인다. Detector는 `clock_basis`를 읽기만 한다 |
| `transaction_id` | `data_processes.transaction_id` | 거래 응답코드 조인과 층 A 거래 노드 연결 |
| 거래 응답코드 | 2.3 | |
| `channel_id` | `data_processes.channel_id` | |
| `decided_at`, `execution_id` | `run_process_decisions` | 시각 대체, 실행별 최신 판정 |

### 2.3 거래 응답코드 조인 [제안]

- 응답코드는 PROCESS 값이 아니라 **거래(TRANSACTION) 값**을 쓴다. 근거(대리 지표): 실패는 거래 단위(TRANSACTION STATUS·RESPONSE_CODE)에 몰려 있고, 그 거래의 PROCESS 행은 대부분 END_TIME 없는 N이며 PROCESS response_code는 빈 값
- 수집기가 P→T FK(`data_processes.transaction_observation_id`)를 채우지 않으므로 문자열로 찾는다: 판정 PROCESS의 `transaction_id`가 없음이 아니면, 같은 원본(`data_observations.source_id`)의 `data_transactions.transaction_id`가 그 값과 같은 행 → 실행(`data_observations.execution_id`)마다 가장 큰 `revision_no` 1건으로 줄임 → **후보가 정확히 1개면 그 `response_code`**. 0개·2개 이상이면 None이고 PROCESS 응답코드로 대신하지 않는다(`resolve_transaction_response_code`)
- **실행 식별 불가 행** [제안]: `data_observations.execution_id`가 NULL인 TRANSACTION 행(002 CHECK상 `revision_no`도 NULL, 수집 경로의 `execution_identity_unverified`)은 실행으로 줄일 수 없어 따로 센다. 후보에 이런 행이 하나라도 있으면 모호하므로 고르지 않고 응답코드 키 없음 → channel
- 같은 실행 안에서 리비전이 같은 행은 DB `UNIQUE(execution_id, revision_no)`로 생기지 않는다는 전제다
- 재시도와 실행: 전환형 입력 경로(`collector/input_runner.py` → `collector/input_store.py` `admit`, api·csv 입력 모두)는 실행 식별(`collector/input_contract.py` `identity`)에 재시도 번호가 들어가 재시도가 다른 TRANSACTION 실행이 된다 → 재시도 거래는 후보 2개 이상 → 응답코드 키 없음. `retry_count`가 없으면 식별 값은 `'unknown'`이다(`identity`, 없음끼리는 같은 실행). CSV 재생 경로(`collector/replay_csv.py`, csv-replay)만 TRANSACTION 실행 식별이 `(transaction_id,)`라 재시도가 같은 실행의 리비전이 되고, 가장 큰 리비전 1건의 값을 쓴다. `collector/api_runner.py`는 T/P/M 행을 저장하지 않는다. 열린 항목 6
- 입력 이름공간과 실행: 전환형 입력 경로의 실행 키에는 입력 이름공간이 들어간다(`admit` 키 `json.dumps([namespace, layer, parts])`). api 입력은 `api` 하나지만 csv 입력은 `csv:{run}:{position//count}`(`input_runner.py`, 입력 실행 id·반복 회차)라, csv 입력을 반복(`repeat`)하거나 다시 돌리면 **같은 `transaction_id`가 회차·실행마다 다른 TRANSACTION 실행**이 된다 → 두 번째 회차부터 후보 2개 이상 → 응답코드 키 없음 → `channel_id`. 같은 csv 행을 반복 재생한 시험 데이터는 응답코드 키로 묶이지 않는다

## 3. 구성원 시각(`occurred_at`)과 시각 출처(`occurred_basis`)

- `source_log_occurred_at`은 지금 코드에서 채우는 곳이 없어 늘 NULL이므로 쓰지 않는다 [합의 DB]
- **원천 시각은 한국 시각(Asia/Seoul)으로 본다** [확정 11/12 사용자]. 고객 측 확인 뒤 바뀔 수 있음(바뀌면 가정 시간대·기준 문서를 바꾼다, 9절)
- **현재 사실**: 모든 수집 경로가 `ops_sources.clock_basis.status`를 여전히 `unknown`으로 넣는다(`collector/replay_csv.py`, `collector/input_store.py` `ensure_source`, `collector/api_runner.py`). 그래서 지금은 시간대 없는 원문 구성원이 모두 가정 시간대 `Asia/Seoul`로 읽힌다(`process_time_raw`). `decided_at` 대체는 해석 실패·범위 넘침·없음일 때만 남는다
- Detector는 수집기 코드·원본 설정(`ops_sources.clock_basis`)을 바꾸지 않는다. 가정은 층 C 사건 시각 계산에만 쓰며, Detector의 다른 부분(하드룰 `hard_rules._time`·판정·지연 계산 등)의 시각 해석은 바뀌지 않는다
- 순서 [제안]: PROCESS `END_TIME` → 없거나 해석 실패면 `START_TIME` → 둘 다 못 쓰면 `decided_at`
  - 없음 판정·해석은 하드룰과 같다: 없음은 `method_results.source_absent`, 해석은 `derived_expression.evaluate`의 열 해석(`hard_rules._time`이 쓰는 것, `datetime.fromisoformat`)
  - 오프셋이 있는 원문(`+09:00`·`+00:00`·`-05:00` 등)은 그 오프셋을 그대로 쓴다
  - 시간대 없는 원문에 붙이는 시간대 [확정 11/12 사용자]: 원본 `clock_basis.status = 'verified'`이면 그 `timezone`(기존), 아니면 가정 시간대 `Asia/Seoul`(`incidents.ASSUMED_TIMEZONE`, `Criteria.assumed_timezone`). 검증된 원본인데 `timezone`이 없거나 이름이 잘못됐으면(`zoneinfo`가 찾지 못함) 검증된 주장을 가정으로 덮지 않고 그 원본은 시간대 없음으로 보고 다음 순서로 간다(작업을 멈추지 않음) [제안]. 비교·저장은 UTC
  - Asia/Seoul은 IANA 시간대라 고정 +09:00이 아니다. 1908년 이전 지역 평균시(+08:27:52), +08:30을 쓴 기간(1908~1911, 1954~1961), 1988년까지의 일광절약시간이 반영된다(현재 ESB 로그 범위에는 영향 없음)
  - UTC로 바꿀 때 datetime 범위(1~9999년)를 넘는 값(예 `0001-01-01T00:00:00+09:00`, `9999-12-31T23:59:59-05:00`, 원본·가정 시간대를 붙인 `0001-01-01 00:00:00`)은 해석 실패로 보고 다음 순서로 간다. 범위 안의 끝값(`9999-12-31T23:58:00+00:00` 등)은 그대로 쓰고, 창·구간 계산(± gap·max_span)이 범위를 넘으면 범위 끝(UTC)으로 고정한다(`shift_clamped`, 비교 결과는 같다). 작업은 세션 시간대를 UTC로 고정해 DB 시각을 읽는다
- 사건의 `time_basis`는 기준이 정한 1개 값 `process_time_raw`, 대체 정책은 사건 `criteria.timeFallback = "decided_at"`, 가정 시간대는 `criteria.assumedTimezone = "Asia/Seoul"`. 019 CHECK `'process_time_raw'·'decided_at'·'source_log_occurred_at'`에 반영 [합의 DB]
- 구성원마다 실제로 쓴 출처를 `occurred_basis`(`process_time_raw` | `decided_at`)로 남긴다. 원본 시간대를 붙였든 가정 시간대를 붙였든 원문에서 읽었으면 `process_time_raw`다(가정 여부는 사건 `criteria.assumedTimezone`과 원본 `clock_basis.status`로 안다). 019 `run_incident_members.occurred_basis text NOT NULL` 열 추가로 확정 [합의 DB]. 대체 구성원 수는 이 열로 센다(순수 로직 `GroupingResult.fallback_members`)
- `decided_at`으로 대체된 구성원은 원천 시각과 다른 시계라 같은 사건 안에서 섞인다. 화면은 대체 구성원 수를 보일 수 있다

## 4. 묶음 규칙(기준 `incident-gap-v1`)

### 4.1 구성원 자격

- v4 `composite.grade`가 `anomaly` 또는 `anomaly_sign`인 최종 판정만 [합의] (`decision.py` `_grade`). `normal`·`undetermined`·v3(None)는 넣지 않는다
- **한계**: 층 C는 Detector 판정(PROCESS 단위)을 묶을 뿐 판정을 새로 하지 않는다. 거래는 실패인데 PROCESS가 END_TIME 없는 N이면 `hard:process-status`상 이상이 아니므로(`detection-decision.md` C26, `hard_rules.py` `evaluate_process_status`) 구성원이 되지 않고, 주 장애가 사건에 잡히지 않을 수 있다. 판정 규칙 쪽 문제라 층 C에서 고치지 않는다(열린 항목 1)

### 4.2 기준 키 [제안]

- 우선순위대로 보고 **처음 쓸 수 있는 키 1개**를 고른다: `response_code`(2.3 거래 값) → `channel_id`
- `response_code`는 실패 코드일 때만 키: 없음이 아니고, ASCII 공백류(0x20·0x09·0x0D·0x0A)를 앞뒤에서 뺀 값이 성공 코드 목록 `{"0000", "S"}`(상수 `SUCCESS_RESPONSE_CODES`, 기준에 포함)에 없을 때. 근거(대리 지표): 실패 상태 행의 응답코드에도 성공 코드·빈 값이 섞여 있다. 고객 코드 사전이 생기면 목록을 바꾸고 기준 버전을 올린다
- `process_hub_id`는 1차 기준에서 뺀다. 근거(대리 지표): 이 데이터에서 허브 값은 판별력이 없다(사실상 한 값이라 그 키로 묶으면 거의 전체가 한 묶음). 기준 문서 `excludedKeys`에 사유와 함께 남겨 다시 넣을 수 있게 한다
- 없음: NULL, 또는 ASCII 공백·탭·CR·LF만 앞뒤에서 뺀 값이 `""`·`\N`(그래프 계약 `graph_value_absent`, `DEPEND_PROCESS_ID`가 아닌 열 규칙). Detector `source_absent`와 별개
- 키 값은 원래 값(앞뒤 문자 포함) 그대로 쓴다. `group_key` = `<키 이름>:<이스케이프한 값>`(예 `response_code:E001`, `channel_id:CH%3A1`)
- **긴 값** [제안]: 이스케이프한 값의 UTF-8 길이가 512바이트(`GROUP_KEY_MAX_BYTES`)를 넘으면 `group_key` = `<키 이름>:sha256:<원래 값 UTF-8의 SHA-256 hex>`. 근거: 019 `UNIQUE(source_id, criteria_version, group_key, opened_at)` btree 항목 한도(약 2.7KB)를 넘는 압축 안 되는 값이면 INSERT가 54000(`ProgramLimitExceeded`)으로 실패하고 같은 배치가 매 회차 되돌려져 그 원본 커서가 멈춘다
  - 같은 긴 값끼리는 계속 같은 키로 묶인다. 이스케이프한 짧은 값에는 `:`가 남지 않으므로 해시 형태와 겹치지 않는다. 사건 id(5.1)도 이 `group_key`를 그대로 쓰므로 결정적이다
  - 원래 값은 사건 `criteria`나 다른 열에 넣지 않는다(크기). 화면이 원래 값을 보여야 하면 구성원 판정의 원천 행(`data_processes.channel_id`, 거래 응답코드는 2.3 조인)에서 읽는다. 기준 문서에는 `groupKeyMaxBytes`·`groupKeyLongValue`로 규칙만 남긴다
  - 원천 값에서 만들어져 인덱스·UNIQUE에 들어가는 열은 `group_key` 하나다(`criteria`는 기준 상수만 담고 인덱스 없음, 나머지 열은 uuid·시각·상수 코드)
- **키가 하나도 없는 판정은 어느 사건에도 넣지 않는다**(`no_group_key`)

### 4.3 간격·길이 상한 [제안]

- 같은 `group_key` 안에서만 묶는다. 판정은 `(occurred_at, 이벤트 id, decision_id)` 순서로 처리한다
- 매개변수: 간격 `gap` 5분, 사건 최대 길이 `max_span` 60분. 경계는 모두 포함(정확히 5분·정확히 60분은 같은 사건)
- 판정 시각 t, 같은 키의 병합되지 않은 사건 I(`opened_at` o, 마지막 구성원 시각 l)에 대해
  - **참여**: o ≤ t ≤ o + max_span 이고 t − l ≤ gap. 여럿이면 (o, id)가 가장 작은 사건에 넣는다. o ≤ t < l(늦게 온 중간 시각)도 참여
  - 병합된 사건도 **자기 구간(o, l)으로** 참여 판단을 한다. 병합 묶음의 어느 구성 사건 구간에 들어가면 그 구성 사건 행에 넣고(읽을 때 병합 뿌리로 묶임), 묶음 밖으로 이어짐 사건을 새로 열지 않는다
  - 참여할 사건이 없으면 t를 `opened_at`으로 하는 새 사건을 연다
  - **이어짐**: 새 사건을 열 때, 구간을 넘긴 사건(t > o + max_span 이고 t − l ≤ gap) 중 아직 다음 구간이 없는 것이 있으면 (o, id)가 가장 큰 것을 `continues_from`으로 넣는다(읽기 순서는 8절)
- 근거(대리 지표): 상한 없이 간격 사슬(5분)만 쓰면 한 장애가 사건 1개로 크게 이어질 수 있다
- `opened_at`은 생성 때 정해지고 바뀌지 않는다(append-only). 그래서 o보다 이른 판정은 그 사건에 넣지 않는다(4.4)

### 4.4 병합 [합의] [제안: 세부]

- 판정 t가 같은 키의 병합되지 않은 사건 둘 이상과 이어지면(참여 사건이 여럿이거나, t < o 이고 o − gap ≤ t인 사건이 있음) 판정이 들어간 사건이 대상, 나머지는 `merged_into` 대상으로 병합한다
  - 늦게 온 판정이 기존 사건 시작 직전(gap 안)이면 새 사건을 열고 기존 사건을 새 사건으로 병합한다
  - **묶음 단위 도달 검사**: 병합하기 전마다 읽은 상태(같은 실행 안의 앞선 병합·새 사건 포함)로 묶음 이어짐 그래프를 만든다. 노드 = 병합 뿌리(묶음), 간선 = X가 든 묶음 → `continues_from(X)`가 든 묶음. 두 묶음 사이에 어느 방향으로든 경로가 있으면 병합하지 않는다(병합하면 합친 묶음이 자기 자신에서 이어지는 고리가 됨, DB 제약으로는 잡히지 않음). 경로는 제3 묶음을 거쳐도 된다: 예 G의 g가 K의 k1에서 이어지고 K의 k2가 H의 h에서 이어지면 G → K → H라 G·H를 병합하지 않는다(시험 `test_no_merge_through_third_group`)
  - 같은 이어짐 사슬이 두 묶음에 걸친 경우(이전 초안의 "사슬 시작 집합이 겹침")는 사슬을 따라 경로가 있으므로 이 검사에 포함된다
  - 병합 뿌리가 상태 밖인 사건과 상태 밖 앞 구간은 그래프에서 "모르는 묶음" 노드 1개로 본다. 이 노드가 그래프에 하나라도 있으면(이어짐 간선의 어느 쪽이든, 병합할 두 묶음과의 경로 유무와 무관하게) 그 판정으로는 병합하지 않는다. 모르는 묶음의 실제 뿌리가 병합할 묶음 중 하나일 수 있어 경로를 확정할 수 없기 때문이다(반례: R·O 사이 경로는 없어 보이나 O에서 이어지는 Y의 뿌리가 상태 밖에서 실제로 R이면 O → R 병합은 고리, 시험 `test_unknown_group_anywhere_in_graph_refuses_merge`·`test_unknown_root_on_path_refuses_merge`). 이어짐 간선이 없는 모르는 뿌리 사건은 그래프에 들어가지 않으므로 막지 않는다. 묶음 구성 사건 자체가 상태에서 빠지면 그 간선이 보이지 않으므로 6.1 닫힘 읽기는 여전히 필요하다. 이 검사는 키와 무관하게 읽은 상태 전체 그래프를 보므로 다른 키의 모르는 묶음 때문에도 병합을 거부할 수 있다(보수적, 저장 경로에서는 닫힘 읽기 때문에 정상 데이터에 모르는 묶음이 생기지 않음)
- 사건당 `merged_into` 최대 1건, 대상은 병합되지 않은 사건, 자기 자신 불가 [합의 DB]. 대상이 아직 병합되지 않았는지는 Detector가 잠금 안에서 읽은 상태로 확인한다
- 병합 묶음은 한 사건으로 본다. 참여·이어짐 판단은 구성 사건마다 자기 `opened_at`·마지막 구성원 시각으로 하고(4.3), 병합 대상은 병합 뿌리다. max_span은 구성 사건마다 보며 병합 묶음 전체는 60분을 넘을 수 있다
- 병합 뿌리가 읽은 상태 밖이면(병합 대상이 아직 병합되지 않았는지 확인할 수 없음) 그 판정으로는 병합하지 않는다
- 새 사건 id 충돌(같은 키·같은 `opened_at`)은 생기지 않는다: 그런 사건은 병합됐어도 자기 구간 참여 조건을 늘 만족해 새 사건을 열지 않는다. 그 사건이 병합돼 있고 병합 뿌리가 상태 밖이면 구성원만 넣고 병합하지 않는다

### 4.5 결정성

- 같은 입력(판정·상태)이면 입력 순서와 무관하게 같은 행동을 낸다(시험)
- 저장 행은 처리 단위(실행마다 읽은 범위)에 따라 다를 수 있다. 예: 0·8분 판정을 먼저 묶고 4분 판정이 늦게 오면 사건 2개 + 병합 1건, 한 번에 오면 사건 1개. 병합을 푼 판정 묶음은 간격 규칙만으로는 같다(시험). max_span이 걸리면 구간 경계가 처리 순서에 따라 달라질 수 있다

## 5. 출력 행 — 019 형태를 따른다 [합의 DB]

- 모든 표는 INSERT만. UPDATE·DELETE는 트리거로 막는다. 끝 시각·구성원 수는 저장하지 않고 읽을 때 계산한다
- `run_incidents`: `id`, `source_id`, `criteria`(jsonb 객체, `Criteria.document()`), `criteria_version`, `group_key`, `time_basis`, `opened_at`, `continues_from`(앞 구간 사건 FK, NULL 가능, 생성 때만), `added_by_run`, `created_at`. UNIQUE `(source_id, criteria_version, group_key, opened_at)`, `UNIQUE(continues_from) WHERE NOT NULL`(사슬 선형), 자기 참조 CHECK
- `run_incident_members`: `decision_id` PK, `incident_id`, `occurred_at`, `occurred_basis`, `added_by_run`, `added_at`. 판정 1건당 1행 [합의 DB]
- `run_incident_events`: `id`(identity), `incident_id`, `kind`(지금 `merged_into`만), `target_incident_id` FK, `added_by_run`, `created_at`. `merged_into`는 사건당 1건(partial UNIQUE)
- `run_incident_runs`: 실행 트랜잭션 끝에 1행 — `id`, `source_id`, `criteria_version`, `cursor_from`·`cursor_to`(`run_decision_events.id`), `added_members`, `opened_incidents`, `merged_incidents`, `started_at`, `finished_at`. `added_by_run` FK는 DEFERRABLE INITIALLY DEFERRED라 사건 → 구성원 → 이벤트 → 실행 행 순서로 넣는다
- 순수 로직 행동 ↔ 행: `NewIncident` → `run_incidents`, `AddMember` → `run_incident_members`, `MergeInto` → `run_incident_events(kind='merged_into')`. 이어짐은 이벤트가 아니라 `NewIncident.continues_from`

### 5.1 id [제안]

- 사건 id = uuid5(이름공간 `uuid5(NAMESPACE_URL, "urn:powercode:detector:incident")`, 이름). 이름 = `source_id`, `criteria_version`, `group_key`, `opened_at`(UTC ISO, 마이크로초 6자리, `+00:00`) 각각을 이스케이프해 `:`로 이은 문자열. DB UNIQUE와 같은 네 값이라 두 기준이 맞고, 재실행이 같은 id를 만든다
- 이스케이프는 그래프 계약 규칙 하나: `%` → `%25` 다음 `:` → `%3A`(group_key도 한 번 더 이스케이프된다. 해시 입력일 뿐 읽어서 쪼개지 않는다)
- 그래프: 사건 노드 `incident:<사건 id>`, 구성원 거래 연결 `transaction:<이스케이프한 TRANSACTION_ID>`(층 A 거래 노드). TRANSACTION_ID가 없음이면 연결 없음(null). `member_of` 간선 id는 그래프 계약 간선 규칙(`member_of:<from>:<to>`)을 따른다 — 그래프 계약에 아직 정의 없음(열린 항목 9)

## 6. 주기 작업

- 1분마다, 원본·기준 버전마다 트랜잭션 1개 [합의]. 판정 시도 밖에서 돈다
- 잠금: 트랜잭션 처음에 `pg_try_advisory_xact_lock(81741, 0)`(`INCIDENT_LOCK_CLASS`, DB 배정) [합의]. 못 잡으면 이번 회차는 건너뛴다(다른 작업자가 도는 중). 같은 트랜잭션에서 81739(룰 세트 저장)·81740(`MANIFEST_LOCK_CLASS`)은 잡지 않는다 [합의]. 판정·판정 이벤트 표에 쓰지 않으므로 81736도 잡지 않는다
- 순서: 잠금 → 커서 시작점 → 새 final 이벤트 판정 읽기(2절) → 후보의 기존 구성원 여부(`run_incident_members`에서 후보 decision_id) → 열린 사건 상태 읽기(6.1) → `group_decisions` → INSERT(5절 순서) → 실행 행 → 커밋
- 실패: 예외·충돌이면 트랜잭션 전체를 되돌리고 커서는 그대로다. 다음 회차가 같은 범위를 다시 읽는다 [제안]
- 구현(`incidents_store.run_once(conn, source_id, criteria=…, batch_limit=1000, after=None)`): 열린 트랜잭션이 없는 연결을 받아 `BEGIN … COMMIT` 1개로 돈다. 결과 상태 `skipped`(잠금 못 잡음, 아무것도 읽지 않음) | `idle` | `completed`. `after`는 커서 시작점을 덮어쓰는 재처리·시험용(같은 범위를 다시 돌리면 `already_member`만 나오고 실행 행 1개만 더해짐)
- 사건 id는 Detector가 만든 uuid5(5.1), 실행 id는 uuid4. 실행 행 `started_at` = 잠금 직후 DB `clock_timestamp()`
- DB가 검사하지 않는 같은 원본·기준 버전 조건은 쓰기 전에 코드로 확인한다(`check_result_scope`): 읽은 상태 사건이 모두 이 원본·기준 버전이고, 구성원의 사건·병합 양쪽·`continues_from`이 읽은 상태 또는 이번에 만든 사건 안이어야 한다. 어긋나면 예외 → 되돌림
- 원본마다 트랜잭션을 따로 돈다(`incidents_job.run_all`이 `ops_sources` 전체를 차례로). 한 원본이 어떤 예외로 실패해도 그 원본만 되돌리고 사유(ValueError는 사유 코드, 그 밖은 예외 이름)를 한 줄 로그로 남긴 뒤 다음 원본으로 간다. 연결이 끊겼거나(UNKNOWN·BAD) 되돌리기도 실패하면 연결을 닫고 다시 연다. 실패한 원본 뒤 연결을 다시 열지 못하면 이미 끝난 원본의 보고 줄은 그대로 출력하고, 다시 열기 실패 사유 1줄과 남은 원본마다 `not_run` 줄을 남긴 뒤 그 회차를 끝낸다(다음 회차에 새 연결로 다시 시도). 원본 목록 읽기·연결 실패도 그 회차만 실패로 남긴다. 성공한 원본이 하나도 없는 회차가 이어지면 대기를 간격 × 2ⁿ(최대 5분, 간격보다 짧지 않음)로 늘린다. 한 원본만 계속 실패하면 다른 원본을 늦추지 않으려고 늘리지 않는다(`--once`는 실패가 있으면 종료 코드 1). 잠금 (81741, 0)은 원본과 무관한 하나라 원본끼리도 줄을 선다
- 권한: 019 권한(`powercode_detector` SELECT·INSERT, 시퀀스 USAGE)과 기존 SELECT 권한만으로 돈다(시험은 `powercode_detector`만 받은 로그인 역할로 실행)
- 019 머리말 잠금 문구는 DB 세션이 `pg_try_advisory_xact_lock(81741, 0)`(건너뜀)으로 정정했다 [합의 DB]

### 6.1 열린 사건 상태(순수 로직 입력) [합의 DB: 필요 항목] [제안: 범위]

- 후보 판정 키마다, `opened_at`이 [후보 최소 시각 − max_span − gap, 후보 최대 시각 + gap] 안인 사건에서 시작해, **병합 간선(양방향: `merged_into` 대상과 그 대상으로 병합된 사건)과 앞 구간 간선(`continues_from`)을 닫힘까지 따라간 모든 사건**(병합 뿌리 판단·묶음 단위 도달 검사용. 사슬 위 사건이 속한 묶음의 구성 사건과 그 사슬도 포함 — 4.4의 제3 묶음 경로). 다음 구간 방향은 따라가지 않는다(고리는 두 묶음 중 한쪽에서 앞 구간 방향으로 다른 쪽에 닿는 경로라 앞 구간 방향 닫힘으로 충분). 사건마다 `incident_id`, `group_key`, `opened_at`, 마지막 구성원 시각(구성원 `occurred_at` 최댓값, 해제 여부 무관), `continues_from`, 다음 구간 존재 여부(`has_continuation`), `merged_into` 대상
- 이 범위를 지켜야 한다: 같은 키·같은 `opened_at` 사건을 빠뜨리면 순수 로직의 충돌 검사(`incident_id_collision`)가 못 잡고 DB UNIQUE 위반 → 트랜잭션 되돌림 → 같은 범위가 매 회차 반복된다. 닫힘 일부를 빠뜨리면 도달 검사가 고리를 못 막는다(빠진 앞 구간·뿌리는 모르는 묶음이 되어 병합 거부 쪽이지만, 빠진 사건이 묶음 구성 사건이면 경로 자체가 안 보인다)
- 마지막 구성원 시각은 해제된 구성원도 포함한다 [제안]: 해제는 읽기 규칙이고, 작업 상태가 나중 리비전에 따라 바뀌지 않게 한다

## 7. 멱등성 [합의]

- 구성원 키 = `decision_id`, 판정은 사건 최대 1개(PK). 이미 구성원인 판정은 행동 없음(`already_member`). 같은 실행 범위를 다시 처리해도 새 행이 없다(시험: 결과를 반영한 상태로 재실행 → 행동 0)
- 쓰기는 `INSERT … ON CONFLICT (decision_id) DO NOTHING`. 잠금과 같은 트랜잭션에서 구성원 여부를 읽으므로 충돌은 정상 경로에 없다. 계획한 구성원이 하나라도 들어가지 않으면(`RETURNING` 행 수 < 계획 수) 빈 사건이 남지 않게 트랜잭션을 되돌린다(`incident_member_conflict`) [제안]
- 구성원 여부는 기준 버전과 무관하게 본다(PK가 `decision_id` 하나, 열린 항목 10)
- 같은 판정이 두 사건에 걸치는 경우는 병합(4.4)으로 표현한다

## 8. 읽기 규칙 [합의] [제안: 세부]

- **실행별 최신 판정**: `PARTITION BY d.execution_id ORDER BY o.revision_no DESC NULLS LAST, d.decided_at DESC, d.event_sequence DESC`(o = `data_observations`, 판정 PROCESS 관측) [합의]
- **해제**: 구성원 판정의 실행에서 최신 판정이 더는 `anomaly`·`anomaly_sign`이 아니면 그 구성원은 읽을 때 해제로 본다. 표는 바꾸지 않는다 [합의]
- **구성원 수** [합의 11/14 WebUI·Detector]: 병합 묶음(`merge_root` 단위)마다 센다. 키가 바뀐 리비전 때문에 한 실행의 판정들이 서로 다른 묶음의 구성원이어도 실행은 한 묶음에서만 센다
    - 해제는 위와 같다: 실행의 최신 판정 등급이 `anomaly`·`anomaly_sign`이 아니면 해제
    - **자리 판정**: 실행의 구성원 판정(읽는 사건과 같은 기준 버전) 중 위 최신 판정 정렬(`o.revision_no DESC NULLS LAST, d.decided_at DESC, d.event_sequence DESC`)에서 맨 앞인 판정. **자리 묶음** = 그 판정이 든 사건의 `merge_root` (`home_bundle_by_execution`)
    - 묶음 B의 구성원 수 = B에 구성원이 있고, 최신 판정 등급이 이상·이상 징후이며, 자리 묶음이 B인 실행 수(실행당 1) (`active_execution_count`)
    - 최신 등급은 이상·이상 징후지만 자리 묶음이 B가 아닌 실행은 B에서 해제처럼 다룬다: 세지 않고 최소 구성원 수 필터(`shown`)에도 넣지 않는다. 그래서 옛 사건이 목록에서 빠질 수 있다
    - 근거: (1) 최신 판정이 아니라 최신 '구성원' 판정을 기준으로 하므로, 묶음 작업이 새 리비전 판정을 아직 묶지 못한 동안에는 직전 자리에서 계속 센다. (2) 실행마다 자리 묶음이 하나뿐이라 `continues_from` 흐름 전체를 합쳐도 중복이 없다
    - 기준 버전은 읽는 사건의 기준 버전으로 고정한다(열린 항목 10)
    - API는 구성원별 자리 묶음 id(예: `homeIncidentId`)를 내려 줄 수 있다. 화면 표시 문구는 WebUI 몫이고, 새 문구는 사용자 승인이 필요하다
- **최소 구성원 수** `min_members` 기본 2: 사건은 구성원 1건으로 만들고 **읽을 때 거른다** [제안] (`shown`). 근거: (1) 해제 때문에 구성원 수는 어차피 읽을 때만 정해진다. (2) 2건이 모일 때까지 판정을 붙잡아 두면 커서 밖 대기 상태가 생겨 append-only·멱등성이 복잡해진다. (3) 값을 바꿔도 기준 버전·저장 행이 바뀌지 않는다. 그래서 `criteria`에 넣지 않는다
- **읽기 순서**: 병합(`merged_into`)을 먼저 풀고(`merge_root`), 그다음 `continues_from`을 따라간다. 이때 **병합 묶음 안 모든 사건의 `continues_from`을 본다**(뿌리가 아닌 구성 사건이 앞 구간을 가리킬 수 있음). 이어진 구간은 화면이 한 흐름으로 보일 수 있다
- 끝 시각 = 묶음 구성원 `occurred_at` 최댓값, 대체 구성원 수 = `occurred_basis = 'decided_at'` 수

## 9. 매개변수와 정하는 방법

| 매개변수 | 값 | 기준 버전 포함 | 정하는 방법 |
|---|---|---|---|
| `gap` | 5분 | 예 | 판정 행이 쌓인 뒤 키별 판정 간격 분포로 다시 본다 |
| `max_span` | 60분 | 예 | 같음. 지금은 크게 이어지는 단일 사건(대리 지표)을 끊는 값 |
| 기준 키·순서 | `response_code` → `channel_id` | 예 | 키별 판별력(값 수·최대 묶음 크기). `process_hub_id`는 판별력 없음으로 제외 |
| 성공 코드 목록 | `{"0000","S"}` | 예 | 고객 코드 사전 |
| 시각 순서·대체 | END_TIME → START_TIME → `decided_at` | 예 | 고정 |
| 가정 시간대 `assumedTimezone` | `Asia/Seoul` | 예 | 사용자 결정 11/12. 고객 측 확인 뒤 바뀔 수 있음 |
| `min_members` | 2 | 아니오(읽기 변수) | 화면에서 본 사건 수 |

- 기준 값을 하나라도 바꾸면 `criteria_version`을 올린다. 다른 버전의 사건은 섞지 않는다
- `incident-gap-v1`은 배포 전 초안 단계에서 내용이 바뀌었다(긴 키 해시 규칙 추가, 가정 시간대 `assumedTimezone` 추가 등). 아직 배포 전이라 버전을 올리지 않고 v1 안에서 고쳤다. **첫 배포 때 그 시점 내용으로 v1을 고정**하고, 그 뒤 변경부터 버전을 올린다. 이전 코드로 쌓인 로컬·시험 DB 행은 배포 전에 지운다 [제안]

### 9.1 판정 행 기반 측정 요청

- 위 근거는 모두 원천 CSV 대리 지표다. v04 판정 행이 쌓이면 키별 구성원 분포·사건 길이·대체 비율을 판정 기준으로 다시 잰다

## 10. 열린 항목

1. **판정 규칙 한계(사용자 결정으로 올림)**: 대리 지표상 거래 단위 장애의 PROCESS 행이 END_TIME 없는 N뿐인 경우가 있다. 이때 현재 규칙으로는 이상 판정이 없어 층 C가 그 장애를 묶지 못한다(수치는 Git 밖 측정 기록). 판정 근거에 거래 실패를 넣을지는 판정 계약(`detection-decision.md`) 쪽 사용자 결정
2. v3 판정에는 등급이 없어 구성원이 아니다. v04 전환 시점까지 층 C 결과는 거의 비어 있다
3. 성공 코드 목록은 데이터에서 본 값뿐이다. 고객 코드 사전 필요
4. 원본 시간대: 원천 시각을 한국 시각으로 보기로 했다 [확정 11/12 사용자]. 검증되지 않은 원본(지금 전부 `clock_basis.status = unknown`)의 시간대 없는 원문에 `Asia/Seoul`을 붙여 `process_time_raw`로 쓰고 `criteria.assumedTimezone`에 남긴다(3절). 배포 전 초안이라 `incident-gap-v1` 안에서 반영했다(9절). 남은 것: 고객 측이 원천 시각 기준을 확인하면 가정을 유지·변경한다(고객 측 확인 뒤 바뀔 수 있음, 배포 뒤 바뀌면 기준 버전을 올린다). 원본 `clock_basis`를 `verified`로 채우는 일은 수집기 쪽 결정이고 Detector가 하지 않는다
    - 배포 전 결정: 가정 시간대는 IANA 시간대 자료(`tzdata` 또는 OS zoneinfo)가 있어야 한다. `tzdata`는 `worker/requirements.txt` 직접 의존성이 아니고 다른 패키지(pandas·python-dateutil 등)의 의존성으로만 들어온다(Windows venv에서 확인, 컨테이너 이미지는 미확인). 자료가 없으면 기본 기준을 만드는 시점(모듈 import, 작업 시작)에 `invalid_incident_assumed_timezone`으로 멈춘다. 공용 워커 의존성 파일에 직접 넣을지는 배포 전에 정한다
5. (닫힘, 11/14 WebUI·Detector 합의) 키가 바뀐 리비전의 실행이 두 사건에서 세어지는 문제: 실행은 자리 묶음 한 곳에서만 센다(8절 구성원 수)
6. 전환형 입력 경로(api·csv 입력)의 재시도 거래(TRANSACTION 실행 2개 이상)와 실행 식별 불가 행이 섞인 거래는 응답코드 키가 없다(2.3). csv 입력은 실행 키에 이름공간 `csv:{run}:{position//count}`가 들어가 반복·재실행만으로도 같은 거래가 실행 2개 이상이 된다(재시도가 아니어도 응답코드 키 없음 → channel). CSV 재생 경로(csv-replay)만 재시도가 리비전이라 해당 없음. 최신 재시도 1건을 고르는 규칙, 또는 판정 PROCESS와 같은 이름공간의 TRANSACTION만 후보로 보는 규칙(`data_input_origins.namespace`)이 필요한지는 판정 행 측정 뒤
7. 구성원 저장 대안(사건에 요약만 두고 구성원은 조회 때 계산): DB 결정은 판정 1건당 1행 저장(DB 계산상 대리 지표 규모에서도 감당 가능, 실측 아님, 수치는 Git 밖 기록). 규모는 구성원 자격과 키 규칙(성공 코드·결측 제외)으로 줄인다. 대안은 DB 의견 기록용으로 남긴다
8. 한 번에 읽는 이벤트 상한과 첫 실행 시작점(과거 판정 전체를 묶을지, 배포 시점부터일지). 구현 임시값: 상한 1000, 시작점 0(과거 전체)
9. 그래프 계약의 층 C `member_of` 간선 id·`incident` 노드 attrs 정의(WebUI 그래프 계약과 조율)
10. 기준 버전을 올릴 때 과거 판정을 새 기준으로 다시 묶을지: `run_incident_members` PK가 `decision_id` 하나라 한 판정은 모든 기준 버전 통틀어 사건 1개에만 들어갈 수 있다. 버전별 재묶음이 필요하면 PK를 `(criteria_version, decision_id)`로 해야 한다(DB와 조율)
    - DB 세션 변경안(11/12, 요청 시 새 migration): members에 `criteria_version` 열 추가, PK `(criteria_version, decision_id)`, `run_incidents` UNIQUE(id, criteria_version) + members (incident_id, criteria_version) 복합 FK, `ON CONFLICT` 대상도 `(criteria_version, decision_id)`, 기존 행은 사건의 기준 버전으로 채움. 읽기(해제·구성원 수)는 기준 버전을 반드시 고정해야 이중 집계가 없다 [합의 DB, 지금은 미적용]
11. 열린 사건 상태 읽기(6.1): 구현은 재귀 CTE로 시간 창 사건에서 병합(양방향)·앞 구간 간선 닫힘을 읽는다(`incidents_store._STATE_SQL`, 시험 `closure_continuation`·`closure_merge`). 간선 CTE가 원본 조건 없이 `run_incident_events`·`run_incidents` 전체를 보므로 표가 커지면 계획 확인이 필요하다(`target_incident_id` 인덱스 없음). 순수 로직에서 `incident_id_collision`은 도달 불가(같은 키·같은 `opened_at` 사건이 있으면 `last_member_at ≥ opened_at`이라 참여 조건이 늘 성립)이지만, 읽기가 그 사건을 빠뜨리면 UNIQUE 위반으로 같은 범위가 매 회차 반복된다
12. 원본별 커서와 다른 원본 이벤트 훑기: 새 이벤트가 없는 원본은 실행 행이 없어 커서가 그대로다(2.1). 다른 원본이 바쁘면 그 원본의 매 회차 배치 읽기(`id > cursor` + 원본 거름 + LIMIT)가 다른 원본 이벤트를 훑는 범위가 점점 커진다. 정확성 문제는 아니고 계획(실행 비용) 확인이 필요하다. 대안 후보: 빈 회차에도 실행 행을 남겨 커서를 넘기기(이벤트 표 전역 최대 id까지), 또는 이벤트에 원본 열·인덱스 추가(DB와 조율)
