Created: 2026-11-13T15:40:00+09:00
Updated: 2026-11-14T04:00:00+09:00
Author: Claude Code — Detector 세션(builder)
Status: draft — 실험 기능 설계(구현 계획·구현 상태는 10절)

# 프로토콜 규칙(정상 경로) Detector 설계 초안 (실험 기능)

- 범위: 룰 설정 › 프로토콜 설정에 저장하는 **인터페이스별 허용 경로**(`protocolRules[]`)의 판정 의미, 경로 정의, 미등록 인터페이스 처리, 알림 근거 필드, 룰 세트 hash v3 포함 방식. 그리고 룰 세트 v3 판정 전체(관계 규칙·`hard:transaction-failed`·프로토콜 규칙·재판정)의 Detector 구현 계획(10절)
- 별도 문서로 둔 이유: [relation-rules.md](relation-rules.md)는 이미 단계 단위 규칙(kind 4종)·재판정·hash v3를 한 문서에 담아 길다. 프로토콜 규칙은 저장 단위(인터페이스 1개 = 항목 1개)·화면·알림이 따로라 새 문서로 두고, **판정 자리·입력 읽기·재판정·보류 기록 형식은 relation-rules.md 절을 그대로 참조**한다. relation-rules.md에는 교차 참조만 더한다
- 소유: Detector(판정·보류 사유·evidence). 룰 세트 저장·검증은 API([rule-set.md](rule-set.md)), 화면·알림 버튼은 WebUI, 관측 경로 초안 집계는 Trainer, CHECK·hash 저장 형식은 DB. 그래프 어휘·없음 판정은 [process-graph.md](process-graph.md)
- 근거 코드: `5aaa813`(origin/dev). 이 문서가 인용한 Detector 파일은 relation-rules.md 기준 커밋 `e038b5e`와 같다. 짧은 파일명은 `server/worker/detector/` 아래
- 표기: **[확정 11/13 사용자]** 사용자 결정(WebUI 세션 전달) / **[제안]** 이 초안이 고른 값 / **[확인 필요]** 데이터로 확인할 사실

## 1. 사용자 결정 [확정 11/13 사용자]

- 화면: 룰 설정 하위 "프로토콜 설정"(목록 → 상세). [관측 경로로 초안 만들기]는 코드가 학습 기간 경로를 집계하고 사용자가 확인·저장
- 단위: **인터페이스별 허용 경로**(여러 개 가능). 경로 = 거치는 채널 순서 전체(시작 → … → 끝). 깊이를 3으로 묶지 않고 **안전 상한만** 두며 넉넉하게
- 판정: **거래 완료 때 그 인터페이스 허용 경로에 없으면 이상**. 판정 자리는 관계 규칙과 같다(거래 행 도착 재판정)
- 처음 보는 경로·인터페이스: **이상 + 알림** [정상 프로토콜에 추가] [이상 유지]. 추가는 **그 뒤 판정부터** 적용, 이미 이상인 판정은 재판정하지 않음
- 저장: 룰 세트 안(버전·되돌리기 함께) → Detector 룰 세트 hash v3 판정 구현이 선행
- LLM은 설명만. 경로 집계는 코드가 한다

## 2. 열 확인 — 경로를 만드는 입력 (코드 근거)

| 열 | 표 | 형식 | 근거 |
|---|---|---|---|
| `interface_id` | `data_transactions` | text, NULL 허용 | `server/infra/migrations/002_core_erd.sql:54` |
| `start_channel_id`·`end_channel_id` | `data_transactions` | text, NULL 허용 | `002_core_erd.sql:54` |
| `retry_count` | `data_transactions` | numeric, NULL 허용 | `002_core_erd.sql:57` |
| `channel_id` | `data_processes` | text, NULL 허용 | `002_core_erd.sql:64` |
| `process_id`·`depend_process_id`·`transaction_id`·`retry_count` | `data_processes` | text/numeric, NULL 허용 | `002_core_erd.sql:63-66` |
| `process_channel_id` | `data_messages` | text, NULL 허용 | `002_core_erd.sql:72` |
| `execution_id`·`revision_no`·`parsed_times`·`persisted_at` | `data_observations` | 실행·리비전은 함께 NULL 가능 | `002_core_erd.sql:36-49` |

- **`data_processes`에는 `interface_id`가 없다**(`002_core_erd.sql:60-67`, 수집 P 투영 `server/worker/collector/storage.py:21-23`). 인터페이스 키는 거래 행 `data_transactions.interface_id`뿐이다 → 프로토콜 판정은 거래 행이 있어야 한다
- **PROCESS 순서를 나타내는 열이 로그 행에는 없다**. `processOrder`는 인터페이스 설정 목록 투영(`server/worker/collector/api_runner.py:32`)에만 있고 그 결과는 `ops_api_catalog.configuration` jsonb(인터페이스·설정 리비전 단위, `server/infra/migrations/006_service_transition.sql:7-13`)에 저장된다. 거래·실행 단위가 아니고 `data_processes`에도 없다(`storage.py:21-23`) → 거래별 순서 근거로 쓰지 않는다
- 수집 경로: CSV 헤더는 `CHANNEL_ID`·`START_CHANNEL_ID` 같은 원천 이름을 snake_case로 바꿔 허용 목록에 맞춘다(`server/worker/collector/replay_csv.py:72-90`, `input_contract.py:10,20`). 값은 대체로 원래 문자열 그대로 저장되지만(`storage.py` `_typed_values`) 운영 입력 경로는 정확히 `\N`인 값을 NULL로 바꾼다(`input_contract.py:20`). 다른 경로(`replay_csv.py`)는 `\N` 문자열이 남을 수 있어, 판정은 두 경우를 모두 `graph_value_absent`로 "없음"으로 본다
- 순서 후보 비교
  - `DEPEND_PROCESS_ID` 사슬(그래프 `precedes`, `process-graph.md` precedes 절): 같은 거래 안 문자열 일치라 **결정적**이고, 화면 그래프·관계 규칙(relation-rules.md 3.1)과 같은 기준이다 → **채택** [제안]
  - 시작·종료 시각: 원문 문자열 + 관측 `parsed_times`(`002_core_erd.sql:43`). 동률·시계 상태(`clock_status`)·결측이 있어 순서가 흔들릴 수 있다 → 쓰지 않음(동률 가르기 용도로도 쓰지 않음 — 사슬에는 동률이 없다)
  - `process_id` 끝 번호(`<인터페이스>_PRnnn`): 형식 가정이 확인 전(relation-rules.md 10절 "processId만으로 단계 특정") → 쓰지 않음
  - MESSAGE_ID 인계(`hands_off`): 메시지가 없는 단계가 있을 수 있고 수집 미완료 보류가 생긴다 → 경로 순서에 쓰지 않음
- 화면과의 차이: 그래프 process 노드 attrs에는 `channelId`가 없다(`server/backend/src/services/processGraph.ts:212-217`). 거래 노드 `channelId`는 `start_channel_id`다(`processGraph.ts:148-150`, `server/backend/src/services/analysisReadModel.ts:384`). 화면이 같은 경로를 그리려면 WebUI가 process 노드에 `channelId`를 더해야 한다(9절)
- 그래프와 시작 단계 차이: 그래프는 `DEPEND_PROCESS_ID`의 NULL·`""`·`\N`·`NONE`을 모두 시작 단계로 그린다(`processGraph.ts:172-173`). 판정은 relation-rules.md 3.1 "키 값이 없을 때"와 같이 **정확히 `NONE`만 시작 단계**, NULL·`""`·`\N`은 데이터 문제로 본다

## 3. 경로 정의 [제안 — Trainer 초안 집계와 같은 정의]

판정과 Trainer 초안(`protocolPathDrafts`)은 아래 정의를 **글자 그대로** 공유한다.

### 3.1 입력

- 거래 = 같은 원본·같은 `transaction_id` 원래 문자열. 행 선택은 relation-rules.md 3.2와 **같다**(실행마다 최신 리비전 1건, 판정 PROCESS 자신의 실행은 판정에 쓴 관측 `focus`, 상한 PROCESS 200). 관계 규칙·그래프 층 A와 입력이 같아진다
- Trainer 집계 기준: 거래 단위로 **실행마다 최신 리비전**(판정이 없으므로 `focus` 없음). PROCESS 실행마다 리비전이 1건이면 판정 입력과 같고, 리비전이 여러 건인 거래에서만 판정(`focus`)과 다를 수 있다 — 알려진 차이로 둔다
- 상한도 Trainer와 같다: 거래당 PROCESS(실행마다 최신 리비전으로 줄인 뒤) 200건 초과 → `graph_truncated`. Trainer도 같은 200건 기준으로 제외한다
- 거래 행 = relation-rules.md 7.2의 대표 행 후보(실행마다 최신 리비전). 인터페이스는 3.1a로 정한다. Trainer는 학습 스냅샷 안에서 같은 규칙
- 단계 = process-graph.md 층 A의 PROCESS 묶음(같은 거래·같은 `process_id`, 재시도는 한 묶음)

### 3.1a 인터페이스 정하기 (경로를 만들기 전)

- 판정 관측 `transaction_id` 없음(`graph_value_absent`): 거래 행을 찾을 수 없으므로 **프로토콜을 평가하지 않는다**. 방법 결과 없음, 기능이 켜져 있으면 relationNotes `{kind: "protocol", result: "not_applicable", reason: "transaction_id_missing"}`만 남긴다(`deferred_to_transaction`도 남기지 않음 — 풀릴 일이 없다). Trainer는 제외
- 대표 행 후보 1건, 또는 relation-rules.md 7.2로 대표가 정해짐 → 그 행 `interface_id`. 없음 값이면 `interface_absent`(4.1 표)
- 대표 행이 **모호**(7.2 `transaction_row_ambiguous`): 후보 행들의 `interface_id`가 **모두 같고 값이 있으면 그 값**으로 방법을 고르고 경로 판정을 계속한다(경로는 PROCESS 행으로 만들므로 거래 행 모호와 무관). 이때 `endpoints`는 시작·끝을 null, `ambiguous: true`로 둔다(5.3). 값이 서로 다르거나 하나라도 없음이면 인터페이스를 정할 수 없다 → 4.1 표 "인터페이스 모름" 행. Trainer도 같은 규칙(값이 모두 같으면 집계, 아니면 `transaction_row_ambiguous`로 제외)

### 3.2 만드는 순서

1. 3.1a에서 인터페이스가 정해진 거래만 아래 순서로 PROCESS 묶음에서 경로를 만든다(미등록 인터페이스도 알림용으로 만든다)
2. 묶음마다: `process_id` 없음 → `process_id_missing`. 묶음이 모호(process-graph.md "모호") → `precedes_ambiguous`. 묶음 하나가 모호해도 **그 거래의 경로 전체를 버린다**(의도 — 순서·채널을 하나로 정할 수 없는 단계가 있으면 어느 경로든 틀릴 수 있다)
3. 묶음마다 선행 값: 시도마다 먼저 **분류**한다 — ASCII 공백·탭·CR·LF만 앞뒤에서 뺀 값이 정확히 `NONE` → `start`, NULL이거나 같은 방식으로 뺀 값이 `""`·`\N` → `absent`, 그 밖 → 원래 값. 분류 결과가 시도마다 다르면(`start`·`absent`·값이 섞이거나 값이 서로 다름) → `precedes_ambiguous`. 그래프도 시도별 값을 먼저 정규화한 뒤 서로 다른지 본다(`processGraph.ts:173`) — 같은 방식이고, 차이는 그래프가 `start`·`absent`를 한 값(null)으로 합친다는 것뿐이다. 하나로 정해진 분류가 `start` → 시작 단계, `absent` → `depend_process_id_missing`, 값 → 원래 값 그대로 같은 거래의 `process_id`와 비교
4. 시작 단계 묶음이 **정확히 1개**가 아니면(0개·2개 이상) → `start_ambiguous`
5. 선행 값이 가리키는 묶음이 없음(그래프 `missing`), 자기 자신(`selfDependency`), 또는 시작 단계에서 사슬로 닿지 않는 묶음이 있음(순환 포함) → `chain_broken`. 각 묶음의 선행은 하나뿐이라 시작 단계에서 닿는 부분은 항상 나무(tree)이고, 순환은 닿지 않는 부분에만 생긴다 → 순환은 따로 사유를 두지 않고 `chain_broken`에 포함
6. 묶음마다 채널: 시도들의 `channel_id` 원래 값이 모두 없음(`graph_value_absent`) → `channel_absent`. 값 있는 시도와 없는 시도가 섞이거나 값이 서로 다름 → `channel_ambiguous`. 아니면 그 값(원래 값, trim 없음)
7. **경로(route)** = 시작 단계에서 각 끝 단계(후행 없는 묶음)까지 사슬을 따라간 채널 목록. 갈라지지 않으면 경로 1개, 갈라지면(그래프 `branch`) 끝 단계 수만큼
8. 상한: 경로 하나의 단계 수 > **64** → `path_too_long`. 경로 수 > **64** → `routes_too_many`. PROCESS 입력 상한 초과(relation-rules.md 3.2 `relationInputs.truncated`) → `graph_truncated`

- 2~8 중 하나라도 걸리면 그 거래는 **경로 없음**(일부 경로만 쓰지 않는다). 사유가 여럿이면 하나만 쓴다. 고르는 규칙(결정적, Trainer 사유 건수도 같은 규칙): ① `graph_truncated`가 있으면 그것(입력이 잘렸으면 다른 판단을 믿을 수 없다) ② 아니면 단계 번호가 가장 작은 단계의 사유 ③ 같은 단계에서 사유가 여럿이면(다른 묶음끼리 포함) 3.4 표에 적은 순서가 앞선 것. Trainer 사전 제외·3.1a 사유까지 포함한 전체 순서는 3.4 표 아래 "사유 선택 전체 순서". 예: 2단계 `process_id_missing` > `precedes_ambiguous`, 3단계 `precedes_ambiguous` > `depend_process_id_missing`, 6단계 `channel_absent` > `channel_ambiguous`, 8단계 `path_too_long` > `routes_too_many`
- 단일 PROCESS 거래는 채널 1개 경로

### 3.3 Trainer 질문 (a)~(d) 결정 [제안]

| 질문 | 결정 | 근거 |
|---|---|---|
| (a) 연달아 나온 같은 채널을 합칠지 | **합치지 않는다**(Trainer 안 그대로) | 경로 위치가 단계와 1:1로 맞아 근거·알림에서 어느 단계가 다른지 가리킬 수 있다. 같은 채널에 단계가 하나 더 끼는 변화도 잡는다. 합친 표시는 화면이 할 수 있지만 합친 값에서 원래 단계를 되살릴 수는 없다 |
| (b) 거래 행 `start_channel_id`·`end_channel_id`와 경로가 맞지 않을 때 | **판정에 쓰지 않는다.** 근거 기록만(`protocolReview.endpoints`, 5.3) | 두 열은 값 2개뿐이라 갈라진 경로를 나타내지 못하고, 단계 채널과 같은 체계의 값인지 확인 전이다 [확인 필요]. 이상으로 보면 확인 안 된 의미로 대량 오탐이 날 수 있다. Trainer는 `endpoint_mismatch` 건수만 정보로 센다(제외 사유 아님). 데이터로 항상 맞는다고 확인되면 다음 버전에서 검사로 올릴지 정한다 |
| (c) 갈라진 경로 | **경로마다 판정**: 각 경로가 허용 경로 중 하나와 같아야 정상, 하나라도 없으면 이상 | 사용자 예시 "A → {B, C}에서 A→C가 의도하지 않은 경로"를 그대로 잡는다. 보류로 두면 이 예시를 못 잡는다. **Trainer는 `branching`을 제외 사유에서 빼고** 갈라진 거래의 각 경로를 경로별 관측 1건으로 센다(갈라진 거래에서 나온 건수는 따로 `fromBranching`으로 표시 권고). 빼면 첫날부터 갈라진 거래가 모두 이상이 된다 |
| (d) 저장 형식 | **채널 목록**(`channels: [..]`). 단계(`process_id`) 목록은 저장하지 않음 | 사용자 단위가 채널(시스템 → 시스템)이다. 단계 목록은 관계 규칙(`precedes`)의 몫이고 같은 것을 두 번 규정하게 된다. 단계 id는 근거(`steps`, 5.2)에만 남긴다 |

- 상한: Trainer의 `path_too_long` 16단계 → **64단계**로 맞춘다(사용자 "더 깊게"). 64는 PROCESS 입력 상한 200 안에서 근거·알림 크기를 막는 값이다 [제안]
- 시작 단계: Trainer 안의 "DEPEND 없음 또는 `NONE`"은 판정과 다르다 → **정확히 `NONE`만** 시작 단계, NULL·`""`·`\N`은 `depend_process_id_missing`(위 3단계)

### 3.4 사유 어휘 — 판정 보류와 Trainer 초안 제외가 같은 이름

| 사유 | 판정 cause(5.4) | Trainer 초안 |
|---|---|---|
| `transaction_id_missing` | 보류 아님 — 평가하지 않음(3.1a, relationNotes만). **Trainer 제외 전용** | 제외(건수만) |
| `transaction_row_ambiguous` | 인터페이스를 정할 수 없을 때만 `hard:unregistered-interface`의 `input_invalid`(4.1) | 인터페이스를 정할 수 없을 때만 제외 |
| `interface_absent` | `hard:unregistered-interface`의 `input_missing`(4.1) | 제외 |
| `process_id_missing` | `input_missing` | 제외 |
| `precedes_ambiguous` | `input_invalid` | 제외 |
| `depend_process_id_missing` | `input_missing` | 제외 |
| `start_ambiguous` | `input_invalid` | 제외(Trainer 안 이름 유지) |
| `chain_broken` | `input_invalid` | 제외(Trainer 안 이름 유지, 순환 포함) |
| `channel_absent` | `input_missing` | 제외(Trainer 안 이름 유지) |
| `channel_ambiguous` | `input_invalid` | 제외 |
| `path_too_long` | `capacity` | 제외(상한 64) |
| `routes_too_many` | `capacity` | 제외(상한 64) |
| `graph_truncated` | `capacity` | 제외 |
| (정보) `endpoint_mismatch` | 판정에 쓰지 않음 | 건수만, 제외 아님 |
| (삭제) `branching` | — | Trainer 안에서 뺀다((c)) |
| `cross_split` | **Trainer 집계 전용**(판정 영향 없음) | 제외 — 학습 분할 경계에 걸린 거래(스냅샷에 train 밖 행이 있음) |
| `anomaly_label` | **Trainer 집계 전용**(판정 영향 없음) | 제외 — train 행에 명시 이상 라벨이 있는 거래 |
| `no_transaction_row` | **Trainer 집계 전용**(판정 영향 없음) | 제외 — TRANSACTION 행이 없는 거래(집계 대상 조건). 판정 쪽 `deferred_to_transaction`(거래 행이 아직 없어 평가를 미룬다는 보류 기록, 5.1)과는 다른 것 — 판정은 거래 행이 오면 다시 평가하지만 초안 집계는 그 거래를 세지 않을 뿐이다 |

- **사유 선택 전체 순서** [제안 — Trainer PR #149 구현 `server/worker/trainer/protocol_paths.py` `REASON_ORDER = PRE_INTERFACE_REASONS + PATH_REASONS`와 같음]. 한 거래에는 사유 하나만 센다
  1. 거래 단위 사전 제외(**Trainer 집계 전용**, 3.1a·3.2보다 먼저): `cross_split` → `anomaly_label` → `no_transaction_row`. 3.2의 ①~③ 규칙에는 들어가지 않는다
  2. 3.1a 사유: `transaction_row_ambiguous` → `interface_absent`. 인터페이스를 모르는 상태라 Trainer는 **전체 건수에만** 센다(인터페이스별 건수 없음)
  3. 3.2 사유(3.2 고르는 규칙 ①~③): `graph_truncated` → `process_id_missing` → `precedes_ambiguous` → `depend_process_id_missing` → `start_ambiguous` → `chain_broken` → `channel_absent` → `channel_ambiguous` → `path_too_long` → `routes_too_many`. 단계 번호 순과 3.4 표 순서가 이 목록에서 어긋나지 않는다(`precedes_ambiguous`는 2·3단계 모두에서 나오지만 2단계 `process_id_missing`과 3단계 `depend_process_id_missing` 사이에 있다)
  - `transaction_id_missing`은 거래로 묶이지 않는 **행 단위**라 이 순서에 들지 않는다(Trainer `transactionIdMissingRows`, 9절). 판정 쪽 `transaction_id_missing`은 3.1a의 첫 항목이라 **모든 사유보다 먼저 "평가 안 함"**이다(보류 사유가 아님)
  - 판정(Detector)은 1을 쓰지 않고 2·3만 같은 순서로 쓴다
- `transaction_row_ambiguous`·`precedes_ambiguous`·`depend_process_id_missing`·`graph_truncated`는 relation-rules.md 5절과 같은 이름·같은 cause. `transaction_id_missing`은 이름만 같고 프로토콜에서는 보류가 아니다(3.1a). 새 cause는 만들지 않는다
- Trainer 집계 대상: TRANSACTION 행이 있는 거래만, 실행마다 최신 리비전(Trainer 안 그대로) — 판정이 거래 행 도착 뒤에만 평가하는 것과 같다

## 4. 룰 세트 스냅샷 `protocolRules[]` [제안]

```json
{"id": "hard:proto-5c1e9a07", "interfaceId": "IF_A",
 "paths": [{"channels": ["CH_A", "CH_B"], "source": {"kind": "draft", "jobId": "…"}},
           {"channels": ["CH_A", "CH_C", "CH_B"], "source": {"kind": "alert", "decisionId": "…"}}],
 "severity": "critical", "enabled": true,
 "source": {"jobId": "…", "snapshotId": "…", "period": {"from": "…", "to": "…"}}}
```

| 필드 | 규칙 |
|---|---|
| `id` | `hard:proto-<소문자 hex 8자리>`(`^hard:proto-[0-9a-f]{8}$`). API가 항목을 처음 만들 때 생성(소프트룰과 같은 `crypto.randomBytes(4)`, `server/backend/src/ruleset/ruleSetStore.ts:108`), 이후 불변, 되돌리기는 스냅샷 id 그대로 재생성. `hard:rel-*`과 같은 방식. 현 `METHOD_ID` 정규식(`method_results.py:16`)을 만족. 내장 하드룰 key는 `rel-`·`proto-`로 시작하지 않는다(접두 예약) |
| `interfaceId` | 판정 키(필수). 거래 대표 행 `interface_id`와 **원래 값 정확 일치**. 없음 값 저장 422. 같은 `interfaceId` 항목 2개 저장 422(인터페이스당 항목 1개) |
| `paths[].channels` | 채널 원래 값 문자열 배열, 길이 1~64. 원소가 없음 값(`graph_value_absent`)이면 422. 같은 경로 중복 422. 항목당 경로 1~64개 |
| `paths[].source` | 출처(초안 작업·알림 판정 id). **hash 입력 아님** |
| `severity` | `critical`·`warning`·`info`, 기본 `critical`(하드룰 기본 위험도 = 위험, detection-decision.md 4.1). T일 때 `riskLevel` |
| `enabled` | `false` = 그 인터페이스는 검사하지 않음(적용 안 함, 미등록 이상도 아님) — 사용자가 인터페이스별로 끌 수 있는 길 |
| `source` | 항목 출처. hash 입력 아님 |

- 저장 상한 [제안, API와 확정]: 항목 500개, 스냅샷 JSON 전체 크기 상한은 API·DB가 정함
- 같은 규칙의 기준: `interfaceId`가 같으면 같은 항목. 경로 추가는 그 항목의 `paths`에 더하는 저장(새 리비전). 알림에서 추가할 때 항목이 없으면 새 항목 생성

### 4.1 미등록 인터페이스 — 내장 하드룰 `hard:unregistered-interface` [제안]

- 사용자 결정 "처음 보는 인터페이스 = 이상 + 알림"을 방법 결과로 남길 id가 필요해 **내장 하드룰**로 둔다. `hardRules[]`의 `{id, enabled, severity}`(단일 문자열, 기본 `critical`). 스냅샷에 없으면 꺼짐(rule-set.md 원칙) → 프로토콜 설정 첫 저장 때 WebUI가 켠 상태로 보내는 것을 권고
- **API 변경 필요**: 지금 위험도를 편집할 수 있는 내장 하드룰은 `hard:process-status`뿐이다(rule-set.md 4차 정정). `hard:unregistered-interface`의 `severity`(단일 문자열) 편집을 API가 허용해야 한다(프로토콜 항목 `severity`와 같은 화면에서 고침)
- **프로토콜 기능 켜짐** = `protocolRules`에 `enabled: true` 항목이 1개 이상. 꺼져 있으면(`protocolRules` 없음·빈 배열·모두 꺼짐) 프로토콜 판정 전체를 하지 않는다 — `hard:unregistered-interface`가 켜져 있어도 **모든 거래를 이상으로 만들지 않는다**
- 구분

| 인터페이스(3.1a) | 결과 |
|---|---|
| (먼저 봄) 기능 꺼짐 | 아래 모든 행보다 먼저: 프로토콜 판정을 하지 않음, 기록 없음 |
| 거래 행 아직 없음 ∧ 기능 켜짐 ∧ 판정 관측 `transaction_id` 있음 | 5.1 relationNotes `deferred_to_transaction`(인터페이스를 아직 모름) |
| 켜진 항목 있음 | 그 항목으로 경로 판정(5절) — 방법 `hard:proto-<id>` |
| 꺼진 항목 있음 ∧ 기능 켜짐 | 적용 안 함(relationNotes `not_applicable` / `protocol_disabled`) |
| 항목 없음 ∧ 기능 켜짐 ∧ `hard:unregistered-interface` 켜짐 | **T** `PROTO_INTERFACE_UNREGISTERED` + 알림 근거(경로를 만들 수 없어도 T — 인터페이스가 처음이라는 사실만으로 이상). **예외: PROCESS 입력 상한 초과(`graph_truncated`)** → T가 아니라 `hard:unregistered-interface` 보류 `capacity` + `graph_truncated`(붙이는 판정을 정할 수 없어 그 거래의 모든 판정에서 평가하므로 판정마다 이상이 반복되지 않게 — 켜진 항목·`required_step`과 같은 규칙) |
| 항목 없음 ∧ (기능 꺼짐 또는 미등록 규칙 꺼짐) | 적용 안 함, 기록 없음 |
| 없음 값(대표 행 `interface_id` 없음) ∧ 기능 켜짐 | 미등록 규칙 켜짐 → `hard:unregistered-interface` 보류 `input_missing` + `interface_absent`. 미등록 규칙 꺼짐 → relationNotes `not_applicable` / `interface_absent` |
| 인터페이스 모름(대표 행 모호 ∧ 후보 `interface_id`가 서로 다르거나 없음 포함) ∧ 기능 켜짐 | 미등록 규칙 켜짐 → `hard:unregistered-interface` 보류 `input_invalid` + `transaction_row_ambiguous`. 미등록 규칙 꺼짐 → relationNotes `not_applicable` / `transaction_row_ambiguous` |
| 평가 안 함(판정 관측 `transaction_id` 없음) ∧ 기능 켜짐 | 방법 결과 없음, relationNotes `not_applicable` / `transaction_id_missing`(3.1a) |

- 미등록 규칙의 기본값(스냅샷에 없을 때·프로토콜 설정 첫 저장 때)은 **켜짐을 추천**하며, 확정은 rule-set.md v3 절(10절 0단계)에서 한다. 초안: rule-set.md v3.5 — "스냅샷에 없음 = 꺼짐" 원칙은 그대로 두고, WebUI가 첫 프로토콜 항목 저장 때 켬으로 미리 선택

- 저장 경고 [제안]: 기능이 꺼진 채 `hard:unregistered-interface`만 켜면 저장은 허용하고 `warnings[]`에 `protocol_unregistered_without_entries`(relation-rules.md 4절 `warnings` 위치와 같음)
- `detection_methods_empty` 검사(relation-rules.md 4절)에서 `hard:proto-*`·`hard:unregistered-interface`는 **세지 않는다**(거래 행 도착 때만 평가되어 첫 판정에서는 거의 항상 빠진다 — `hard:transaction-failed`와 같은 이유)

## 5. 판정

### 5.1 자리·시점 — 관계 규칙 `required_step`과 같은 자리 [확정 11/13 사용자: 같은 판정 자리 / 세부 제안]

- 평가는 **거래 행이 있을 때만**: 첫 판정 시도 시작 때 이미 있거나, 거래 행 도착 재판정 때(relation-rules.md 3.1 `required_step`·6절과 같음)
- "거래 행이 있음"은 relation-rules.md 3.4의 **도착 확정**(실행 1건 이상, 모든 실행의 `persisted_at` ≤ 판정 읽기 시각 − 30초, DB 시계, 상수 `TRANSACTION_SETTLE_SECONDS`)이다. 확정 전에는 경로를 만들지 않고 아래 `deferred_to_transaction`으로 남긴다 — CSV 재생은 거래 행을 PROCESS보다 먼저 커밋하므로 거래 행만 보고 경로를 만들면 뒤 단계가 빠진 경로로 오탐한다(10절 한계). 그 결과 CSV 재생의 첫 판정은 거의 항상 `deferred_to_transaction`이고 프로토콜은 사실상 7단계 재판정에서 판정된다(relation-rules.md 3.4)
- 붙이는 판정 = 그 거래에서 가장 늦게 끝난 PROCESS의 판정(relation-rules.md 3.1 "붙이는 판정" 규칙 그대로). 다른 판정에서는 적용 안 함(기록 없음)
- 첫 판정 때 거래 행이 아직 없고 기능이 켜져 있으며 판정 관측 `transaction_id`가 있으면(없으면 3.1a — 평가하지 않음): relationNotes `{ruleId: null, kind: "protocol", result: "deferred_to_transaction", reason: null, waitingFor: {layer: "T"}}`(인터페이스를 아직 모르므로 `ruleId` null). n에 넣지 않는다
- 입력: relation-rules.md 3.2의 관계 입력 읽기를 그대로 쓴다. 읽는 조건에 "**프로토콜 기능 켜짐**"을 더하고, PROCESS 행 읽기에 `channel_id`를 더한다. 판정 PROCESS 실행은 관계 규칙과 같이 `focus` 관측(3.1). 새 조회는 없다
- 재판정: relation-rules.md 6.2 사전 검사에 **⑤ 프로토콜**을 더한다 — 다시 평가한 결과가 N이 아니면(T 또는 방법 결과 보류) 대상. ⑤는 원 판정에 고정된 룰 세트가 아니라 **사전 검사 시점의 현재 룰 세트**로 계산한다(재판정 시도도 현재 룰 세트를 고정하므로 같은 기준으로 거른다). 6.1 탐색이 찾는 `deferred_to_transaction` 판정에 `kind: "protocol"`도 포함. 그 밖의 재판정 규칙(1회, manifest `process-input-v2-recheck`, 잠금 81742, 30초·24시간)은 그대로
- 룰 세트 고정: 재판정 시도도 **시작 때 룰 세트를 다시 고정**한다(relation-rules.md 6.3). 그래서 사용자가 경로를 추가한 뒤 도착한 거래 행은 새 규칙으로 판정되고, 이미 이상으로 기록된 판정은 바뀌지 않는다 — 사용자 결정 "그 뒤 판정부터, 재판정 없음"과 맞다. 추가 저장이 재판정을 일으키지 않는다
- 다른 계기로 재판정될 때도 같다: 관계 규칙·`hard:transaction-failed` 때문에 재판정되는 시도도 그 시도 시작 때의 현재 룰 세트를 따른다. 그 사이 사용자가 경로를 추가했다면 그 관측의 최신 판정은 프로토콜 N이 될 수 있다(옛 판정 행은 이상 그대로 남음, append-only). 사용자 결정 "그 뒤 판정부터"의 결과로 받아들인다
- 24시간 안에 거래 행이 오지 않으면 평가하지 않는다(보류로 끝). 한계: 그 거래의 경로 이상은 기록되지 않는다

### 5.2 판정 규칙 [제안]

- 켜진 항목: 3.2로 경로를 만든다
  - 경로 없음(사유) → 방법 결과 보류 `not_ready`, cause는 3.4 표, `evidence: [{"reason": "<사유>"}]`
  - 모든 경로가 `paths[].channels` 중 하나와 **배열이 정확히 같음**(길이·순서·원래 문자열) → N
  - 하나라도 없음 → **T**
- 방법 결과: `methodVersion` = `protocol-path-v1`, T 코드 `PROTO_PATH_UNMATCHED`, `severity`·`riskLevel` = 항목 `severity`. 미등록: `methodVersion` = `unregistered-interface-v1`, 코드 `PROTO_INTERFACE_UNREGISTERED`, 위험도 = `hardRules` 항목 `severity`
- `methodResults[].evidence` = `[{"interfaceMatched": true|false, "routes": [{"steps": ["<process 노드 id>", …], "matched": true|false}]}]` — 단계는 process-graph.md 노드 id(원천 값 복사 없음, detection-decision.md 11.5). 채널 값은 알림에 필요하므로 5.3에만 둔다
- 한 판정에 프로토콜 방법은 **많아야 1개**(그 인터페이스 항목 또는 미등록 규칙) → n 부풀림 없음
- 순서: `methodResults`에서 내장 하드룰(내장 목록 순, `hard:unregistered-interface` 포함) → `relationRules[]` 저장 순 → `protocolRules[]` 저장 순. `_rank`(`decision.py:46-53`) 변경은 relation-rules.md 9절과 함께
- 하드룰 대조 집합(`_check_hard_rules`, `evidence_v4.py:50-57`): `hard:unregistered-interface`는 켜져 있어도 **그 판정에서 과제로 평가한 때만** 넣는다. 붙이는 판정이 아님·거래 행 없음(`deferred_to_transaction`)·기능 꺼짐·항목 있음(`hard:proto-*`가 평가됨)·평가 안 함이면 뺀다. `hard:proto-*`도 평가한 판정에서만 넣는다. relation-rules.md 9절 `evidence_v4.py` 행(`hard:transaction-failed` 보류 제외)과 같은 방식
- v3 투영 `rules[]`·`ruleSeverities`(`evidence_v4.py` `_rule_entry`·`build_evidence`): `hard:proto-*`·`hard:unregistered-interface`는 **넣지 않는다**(relation-rules.md 9절 `hard:rel-*`과 같음). 영향도 같다 — LLM 설명 입력 `savedEvidence.rules`에 빠지고 `decisionCode`만 `PROTO_*`가 된다. 근거는 `methodResults`·`protocolReview`에 있다
- 관계 규칙과 함께: 같은 판정에서 둘 다 평가되고 둘 다 hard다. 단계 누락은 `precedes` 규칙이 T, 프로토콜은 `chain_broken` 보류로 겹쳐 나올 수 있다(의도 — 프로토콜은 경로를 만들 수 없을 때 이상을 짐작하지 않는다). 관계 규칙이 없으면 보류가 hard 대분류를 판단 불가로 만들어 드러난다(`decision.py:73-74`)

### 5.3 알림 근거 `evidence.protocolReview` [제안 — WebUI·API가 읽는 필드]

- 프로토콜 방법이 **T일 때만** 판정 evidence 최상위에 둔다(N·보류·적용 안 함이면 키 없음). 기준은 **최종 `methodResults`**의 그 방법(항목 `hard:proto-<id>`, 미등록이면 `hard:unregistered-interface`)이 `anomalous: true`인지다 — 입력 평가가 T였어도 시간 초과·규칙 슬롯 부족으로 결과가 `not_ready`이면 키를 두지 않는다. 알림 목록은 최신 판정(018 순서) 중 이 키가 있는 판정에서 만든다

```json
{"version": 1, "kind": "path_unmatched",
 "interfaceId": "IF_A", "protocolRuleId": "hard:proto-5c1e9a07",
 "ruleSetRevision": 15, "transactionObservationId": "…",
 "routes": [{"channels": ["CH_A", "CH_C"], "pathKey": "CH_A:CH_C", "matched": false},
            {"channels": ["CH_A", "CH_B"], "pathKey": "CH_A:CH_B", "matched": true}],
 "routesReason": null,
 "endpoints": {"start": "CH_A", "end": "CH_B", "matchesRoutes": false, "ambiguous": false}}
```

| 키 | 뜻 |
|---|---|
| `kind` | `path_unmatched`(항목 있음, 경로 불일치) / `interface_unregistered`(항목 없음) |
| `protocolRuleId` | `interface_unregistered`이면 null |
| `routes` | 3.2 경로 전부. `path_unmatched`는 경로를 모두 만들었을 때만 나므로 항상 채워진다. `interface_unregistered`는 경로를 만들 수 없어도 T이므로 그때 `[]` + `routesReason` = 3.2 2~8단계 사유(입력 상한 `graph_truncated`는 4.1 표 예외로 보류라 이 키가 생기지 않는다) |
| `pathKey` | 채널마다 process-graph.md id 이스케이프(`%` → `%25` 다음 `:` → `%3A`) 뒤 `:`로 이음. 알림 묶음 키 = (`interfaceId`, `pathKey`). 쪼개 읽지 않는다(원값은 `channels`) |
| `endpoints` | `{start, end, matchesRoutes, ambiguous}`. 거래 대표 행 `start_channel_id`·`end_channel_id` 원래 값(없음이면 null)과 "모든 경로의 첫 채널 = start ∧ 마지막 채널 = end" 여부. `start`·`end` 중 하나라도 null이거나 `routes`가 `[]`이면 `matchesRoutes: null`(비교하지 않음). 대표 행이 모호하면(3.1a) `start`·`end`·`matchesRoutes` 모두 null, `ambiguous: true`(아니면 false). **판정에 쓰지 않음**(3.3 (b)) |

- 버튼 의미(WebUI·API 소유)
  - [정상 프로토콜에 추가]: `matched: false`인 경로(사용자가 고름)를 그 인터페이스 항목 `paths`에 더하는 룰 세트 저장(항목이 없으면 새 항목). 일반 저장과 같은 확인·동시 편집 규칙. 재판정 없음(5.1)
  - `routes: []` 알림(미등록 인터페이스인데 경로를 만들 수 없음): 더할 경로가 없으므로 이 버튼으로 경로를 저장하지 않는다. [제안] 버튼 대신 그 인터페이스의 프로토콜 설정 상세(새 항목 만들기)로 이동시키고, 저장 검증은 4절 그대로(경로 1개 이상 필요). [이상 유지]는 그대로 쓸 수 있다. 화면 문구는 WebUI 결정
  - [이상 유지]: 룰 세트를 바꾸지 않는다. 판정은 그대로 이상. 처리 기록 위치(분석 화면 처리 상태 014 표 재사용 등)는 WebUI·API 결정. Detector는 이 기록을 읽지 않는다
- 이미 추가된 경로를 가진 옛 알림: WebUI가 현재 룰 세트와 대조해 "추가됨"으로 보이는 것을 권고(Detector는 옛 판정을 고치지 않음)

### 5.4 보류·기록 요약

- 방법 결과 보류: **판정 cause가 있는** 3.4 사유(Trainer 집계 전용·`transaction_id_missing`·정보 항목 제외) → `not_ready`, `anomalous: null`, cause는 기존 `CAUSES`(`method_results.py:12-13`)만, `evidence: [{"reason": …}]`. relation-rules.md 5절 모양 그대로
- relationNotes(방법 결과 아님): `deferred_to_transaction`, `not_applicable`(사유 `protocol_disabled`·`transaction_id_missing`·`interface_absent`·`transaction_row_ambiguous`, 4.1 표). `kind: "protocol"`
- `capacity` 뜻 확장(입력 상한 포함)은 relation-rules.md 5절과 같이 detection-decision.md 11.6에 함께 반영

## 6. 룰 세트 hash v3 포함 [제안]

- relation-rules.md 8절 정규 객체에 `protocolRules`를 더한다: `{"hardRules": […], "softRules": […], "relationRules": […], "protocolRules": […]}`(없으면 `[]`). v3는 이 결정 때 API·Detector 어디에서도 저장·판정하지 않았으므로(API `CONTENT_HASH_VERSION = 2`, `server/backend/src/ruleset/ruleSetContract.ts:11`. Detector는 그 뒤 10절 1단계로 v3 행을 읽고 미구현 요소가 켜져 있으면 시도 실패) **같은 v3에 함께 넣었다** — v4를 따로 만들지 않음
- `protocolRules` 항목: `{id, interfaceId, paths, severity, enabled}`, 배열은 id 사전순. `paths` = 채널 배열들의 배열, **`pathKey`의 유니코드 코드 포인트 순**으로 정렬(Python `str` 비교와 같음 — JS는 UTF-16 단위 비교를 쓰지 말 것). 경로의 `source`·항목 `source` 제외
- `hardRules`에 `hard:unregistered-interface`가 `{id, enabled, severity}`로 들어간다(내장 규칙 공통)
- v1·v2 행 스냅샷에 `protocolRules`가 있으면 Detector는 시도 실패(`relation_rules_require_v3`와 같은 처리, 코드 `protocol_rules_require_v3`)
- DB: 스냅샷은 jsonb라 새 열·CHECK가 필요 없다. 버전 3 CHECK는 migration 020이 이미 허용(`server/infra/migrations/020_rule_set_hash_v3.sql`). 배포 순서도 020 주석 그대로: DB 020 → Detector v3 → API v3
- 시험 기준값(v3 정규 객체·SHA-256)은 [rule-set.md](rule-set.md) v3.8(경로 정렬 함정 (e)·(g) 포함). `byCode`는 정규 객체에서 배열(rule-set.md v3.3)

## 7. 위험·한계

- 미등록 인터페이스 이상: 프로토콜 기능을 켠 뒤 초안에 없던 인터페이스는 모두 이상이 된다(사용자 결정의 결과). 초안 만들기가 학습 기간에 관측된 모든 인터페이스를 담아야 첫 저장 직후 이상이 몰리지 않는다(Trainer·WebUI)
- 판정 단위가 PROCESS라 PROCESS 행이 없는 거래는 잡히지 않는다(relation-rules.md 7.1과 같음)
- 붙이는 판정(가장 늦게 끝난 PROCESS)에 v04 판정이 없으면 기록되지 않는다(relation-rules.md 6.1 한계와 같음)
- 같은 경로가 여러 거래에서 나오면 판정마다 근거가 생긴다 → 알림 묶음은 WebUI가 (`interfaceId`, `pathKey`)로
- 시작·끝 채널(`endpoints`) 의미 미확인 [확인 필요](데이터 접근 세션) — 3.3 (b)

## 8. 사용자 결정

| 질문 | 결정 |
|---|---|
| [이상 유지]를 누른 경로가 다시 나오면 알림을 또 띄울지 | **[확정 11/13 사용자]** 판정은 이상, 같은 (인터페이스, 경로) 알림은 다시 띄우지 않음. 기록(어떤 경로를 이상 유지했는지)은 API·WebUI 몫 — Detector는 읽지 않는다(5.3) |

- 그 밖의 항목은 이 문서의 [제안]대로 진행한다

## 9. 다른 세션 요청

| 세션 | 요청 |
|---|---|
| Trainer | `protocolPathDrafts`를 3절 정의로 집계: 연속 채널 합치지 않음, 갈라진 거래는 경로별로 셈(`branching` 제외 삭제, `fromBranching` 표시), 상한 64, 사유 이름 3.4, 시작 단계는 정확히 `NONE`, `endpoint_mismatch`는 정보 건수. 출력 형식: 인터페이스별 경로를 **거래 수 내림차순, 같으면 pathKey 코드 포인트 오름차순**으로 정렬해 앞 **64개**(4절 항목당 경로 저장 상한)만 내고, 나머지는 `omittedPaths: {paths, transactions}`(잘린 경로 수, 잘린 경로들의 거래 수 합 — 갈라진 거래는 여러 번 셀 수 있음)로 요약한다. `ratio`·`rare`·`status`·`distinctPaths`는 **자르기 전 전체 경로** 기준으로 계산한다. `transaction_id_missing`은 거래가 아니라 행 단위라 별도 키 `transactionIdMissingRows`(건수)로 낸다 |
| API | v3 저장(6절), `protocolRules` 검증(4절 422·상한), id 생성, `hard:unregistered-interface` 내장 목록, 경고 `protocol_unregistered_without_entries`, 알림 버튼용 경로 추가 저장 |
| WebUI | 프로토콜 설정 화면, `evidence.protocolReview` 알림·버튼, 그래프 process 노드 `channelId` 추가(같은 경로를 그리기 위해 — process-graph.md 개정은 WebUI 몫), 8절 질문 전달 |
| DB | 프로토콜 전용 새 표 없음. 재판정 커서는 Detector 제안 migration 024 `run_recheck_runs`(relation-rules.md 6.5·10절) 확정 요청 |
| 데이터 접근 | 단계 채널과 거래 행 시작·끝 채널 관계 확인(3.3 (b)) |

## 10. Detector 구현 계획 — 룰 세트 v3 판정 전체 [제안]

선행 조건: DB 020 적용, v04 판정 경로(`DETECT_JOB_CONTRACT=v04`, relation-rules.md 10절). 각 단계는 앞 단계 위에서 시험이 통과한 뒤 다음으로 간다. 시험은 단계마다 새 단위 시험 + 기존 Detector 시험 1회, DB 시험은 D 드라이브 임시 클러스터.

| 순서 | 내용 | 파일 | 시험 | 의존 |
|---|---|---|---|---|
| 0 | **rule-set.md v3 절 확정**(정규 객체 — relation-rules.md 8절·이 문서 6절, 저장 검증·422 코드, 미등록 규칙 기본값(켜짐 추천)·severity 편집, 시험 기준값). API v3 구현 전에 먼저 | `server/contracts/rule-set.md` | API·Detector가 같은 기준값으로 단위 시험 고정 | API 합의(필수 선행) |
| 1 | hash v3: 정규 객체(`scope`·`relationRules`·`protocolRules`·`{byCode, default}`), v1·v2에 새 키 있으면 거부, 스냅샷 검증(fail-closed). `byCode` 객체를 `[[code, level]]` 코드 포인트 순 배열로 바꾼 뒤 hash(모든 하드룰 — 현 `_canonical_hard_rule`(`operational_state.py:38-40`)은 저장값을 그대로 넣으므로 v3에서 수정 필요, rule-set.md v3.3) | `operational_state.py`(`_canonical_hard_rule`·`rule_set_content_hash`·`_soft_rules`·`PinnedState`·`pin_operational_state`) | 단위: v1·v2 기준값 불변, v3 기준값 = rule-set.md v3.8 (c)~(h), 잘못된 형식 거부. 통합: 020 적용 DB에 v3 행 넣고 고정 | API v3 기준값 합의 |
| 2 | 그래프 층 A Python 구현(묶음·모호·`precedes`·`hands_off`·`missing`) + 관계 입력 읽기(relation-rules.md 3.2, `channel_id` 포함) → `relationInputs` | 신규 `relation_graph.py`·`relation_inputs.py` | 공용 벡터 `process-graph-id-vectors.json`, 작은 거래 고정 데이터 | WebUI 벡터 추가분(묶음·모호 예시) |
| 3 | 내장 하드룰 `hard:transaction-failed`(7절)·`hard:unregistered-interface`(4.1) | `hard_rules.py`(`HARD_RULES`·`read_hard_rule_snapshot`·`HardRuleSelection`), `method-catalog.json`·`methods/registry.py` | 단위: 거래 행 0·1·여러 실행, 응답 코드 제외 목록, 위험도 `{byCode, default}` | API 내장 목록 |
| 4 | 판정 조립: 과제 사전 분류(relation-rules.md 3.3), `_rank`(내장 → relation 저장 순 → protocol 저장 순), 대조 집합, relationNotes·`protocolReview` 기록 | `attempt_v04.py`(`_tasks`·`run_attempt`), `decision.py`(`_rank`), `evidence_v4.py`(`_check_hard_rules`·`build_evidence`) | 단위: 방법 순서·n, 중간/최종 n 일치 | — |
| 5 | 관계 규칙 판정 kind 3종 + soft `scope` 선택 | 신규 `relation_rules.py`, `attempt_v04.py` | 단위: relation-rules.md 3.1·5절 표 행마다 1건 | Trainer 후보 형식 |
| 6 | 프로토콜 판정(3절 경로, 5절 판정, 4.1 미등록) | 신규 `protocol_rules.py` | 단위: 3.4 사유마다 1건, 갈라짐 2경로(하나 불일치 → T), 기능 꺼짐 → 기록 없음, 미등록 → T | Trainer 같은 정의 — 공용 시험 벡터 [protocol-path-vectors.json](protocol-path-vectors.json)(Trainer 시험도 읽기 권고) |
| 7 | 재판정: 탐색 주기 작업(거래 관측 커서·판정 이벤트 커서, 잠금 81742), 사전 검사 ①~⑤, `process-input-v2-recheck` 작업 생성, `readiness.relation` 검사 | 신규 `recheck_job.py`, `jobs.py`(재판정 작업 생성), `runner.py`(`relationRecheck` 기록·실행 위치) | 통합(임시 DB): 거래 행 도착 → 30초 뒤 1회, 두 번째 삽입 무시, 24시간 밖 제외 | DB 커서 저장 위치 |
| 8 | 나머지 계약 반영: detection-decision.md(4.1·4.2·11.4·11.6·11.7)·queues.md(rule-set.md는 0단계) | `server/contracts/` | 문서 검토 | 각 세션 합의 |

- 배포: Detector v3(1~7)는 API v3 저장보다 먼저. 1만 먼저 배포해도 v3 행을 읽을 수 있지만 관계·프로토콜 판정이 없으면 v3 행의 새 규칙은 시도 실패로 막아야 한다(조용히 무시 금지) — 1단계에서 "v3 행에 아직 구현 안 된 kind가 있으면 시도 실패"를 넣고 5·6단계에서 푼다
  - 1단계 실패 코드(시도 실패 사유, `run_jobs` 실패 문자열) [제안 — Detector 1단계 구현 `server/worker/detector/operational_state.py` `check_rule_set_version`·`hard_rules.py` `read_hard_rule_snapshot`]: 1단계에서 v3 행의 켜진 관계 규칙·`scope` 있는 소프트룰·프로토콜 항목을 `rule_set_v3_unsupported:<id>`로 막았고, 5단계(관계 규칙·scope 소프트룰)와 6단계(프로토콜 항목)가 판정을 갖춰 모두 풀었다. 새 내장 하드룰 2개도 판정을 갖췄다(`hard:transaction-failed` 3단계, `hard:unregistered-interface` 6단계). 프로토콜 항목 저장값 이상 → `invalid_protocol_rule:<id>`(꺼져 있어도, 아래 "6단계 구현 세부"). 판정 계획에 실행기가 없는 하드룰이 들어오면 같은 코드로 시도 실패(`hard_rules.py` `hard_rule_runners`, 방어). v1·v2 행의 v3 전용 요소 → `protocol_rules_require_v3`(`protocolRules` 키) / `relation_rules_require_v3`(`relationRules` 키·소프트룰 `scope` 키·새 내장 하드룰). v3 항목의 `enabled`가 boolean이 아니거나 배열이 아님 → `invalid_rule_set_snapshot:<relationRules|protocolRules>`. hash 불일치는 기존 `rule_set_hash_mismatch`. 저장소·그래프 불변 조건 위반(`relation_focus_unavailable`·`relation_graph_inconsistent`·`relation_transaction_columns_mismatch`)도 시도 실패(relation-rules.md 3.4). 6단계 뒤 "구현 안 됨" 때문의 시도 실패는 남지 않았다
- **구현 상태**(Detector — 1~7단계 구현, 8단계 계약 반영)
  - 2단계: 층 A Python 구현 `server/worker/detector/relation_graph.py`(TS `processGraph.ts`와 같은 노드·간선 — 공용 id·없음 벡터와 TS 출력 저장본 `server/worker/tests/fixtures/relation-graph-cases.json` 24사례로 확인), 관계 입력 읽기 `relation_inputs.py`(relation-rules.md 3.2, `channel_id`·`adapter_type`·`process_hub_id` 포함). WebUI 벡터 추가분(묶음·모호 예시)은 아직 없어 Detector 시험 사례로 대신한다
  - 3단계: `hard:transaction-failed` 판정(`hard_rules.py` `evaluate_transaction_failed`, relation-rules.md 7.2). `hard:unregistered-interface`는 내장 목록·위험도 읽기 — 평가(4.1 표의 기능 켜짐 행)는 6단계
  - 4단계: 과제 사전 분류 `relation_plan.py`(거래 행 없음 → relationNotes `transaction_pending`, 미등록 규칙 → 기능 꺼짐이라 기록 없음), `_rank`(내장 → `hard:rel-*` → `hard:proto-*`, 같은 순위는 저장 순), 대조 집합(`evidence_v4.py` `_check_hard_rules` — 이 판정에서 과제로 만든 하드룰), evidence `relationInputs`·`relationNotes`(관계 입력을 읽은 판정만). `protocolReview`는 6단계
  - 5단계: 관계 규칙 판정 `server/worker/detector/relation_rules.py`(relation-rules.md 2·3·5절), 소프트룰 scope 선택 `soft_rules.py` `in_scope`(4절). 켜진 관계 규칙·scope 있는 소프트룰은 이제 판정한다(`rule_set_v3_unsupported` 해제). 구현 세부는 relation-rules.md 3.4
  - 6단계: 프로토콜 판정 `server/worker/detector/protocol_rules.py`(3·4.1·5절). 켜진 프로토콜 항목은 이제 판정한다(`rule_set_v3_unsupported` 해제 — 이 코드는 판정 계획에 실행기가 없는 하드룰이 들어올 때의 방어로만 남는다). 구현 세부는 아래 "6단계 구현 세부"
  - 7단계: 재판정 탐색 주기 작업 `server/worker/detector/recheck_job.py`(별도 프로세스, Compose `recheck` profile `relation-recheck`, 잠금 81742), 사전 검사 ①~⑤(현재 룰 세트), `process-input-v2-recheck` 작업 생성(`jobs.py` `enqueue_recheck`), 재판정 시도의 `readiness.relation` 검사(`attempt_v04.py` `recheck_reference`, 어긋나면 시도 실패 `relation_recheck_reference_invalid`). 커서는 migration 024 `run_recheck_runs`(DB 세션 확정 전 [제안]). 구현 세부는 relation-rules.md 6.5. 남은 시도 실패 코드는 모두 저장소 이상(저장 검증을 거치지 않은 스냅샷·불변 조건 위반)이고 "구현 안 됨" 때문의 시도 실패는 없다
  - 배포 전제(남은 것): DB 024 적용 → Detector(1~7단계)·`recheck` 서비스 → API v3 저장(rule-set.md v3.7). 024가 없으면 `recheck`는 회차마다 실패하고(판정에는 영향 없음) 보류가 첫 판정에 남는다
  - 8단계(계약 반영): detection-decision.md 3.1(새 방법 id 형식)·4.1~4.4(내장 4 → 6, 관계·프로토콜 하드룰의 방법 버전·결과 코드)·11.4(`relationInputs`·`relationNotes`·`protocolReview`·`relationRecheck`, `methodResults` 순서, v3 투영 제외)·11.6(`capacity` 뜻 확장·세부 사유·새 시도 실패 코드)·11.7(상한), queues.md(재판정 작업), method-catalog.md(내장 목록 6개 — 목록 추가는 API v3 배포와 함께), rule-set.md·relation-rules.md의 낡은 구현 상태 문구. 모두 [제안](각 세션 합의 대기)
- **6단계 구현 세부** [제안 — `server/worker/detector/protocol_rules.py`]
  - 스냅샷 검증(시도 실패, 꺼진 항목도 검사): id 형식·`enabled`·`severity`·`interfaceId`(없음 값 금지)·`paths`(1~64개, 경로마다 `channels` 1~64개, 원소는 없음 값이 아닌 문자열, 같은 항목 안 같은 경로 금지) 위반 → `invalid_protocol_rule:<id>`. 같은 `interfaceId` 항목 2개 → 뒤 항목 id로 `invalid_protocol_rule:<id>`. 같은 id → `duplicate_method_id`. 항목 수 상한 500은 API 저장 검증만 본다. 켜진 항목은 시도 시작 방법 수(`ensure_executable`)에 들어간다
  - 경로 만들기 `build_routes`: 3.2 그대로(Trainer `server/worker/trainer/protocol_paths.py` `transaction_routes`와 같은 정의). 묶음 모호 판단은 그래프 층 A와 같은 함수(`relation_graph.py` `retry_group_ambiguous`). 갈래 순서는 시작 단계에서 깊이 우선, 갈래는 `process_id` 코드 포인트 순. 공용 시험 벡터 [protocol-path-vectors.json](protocol-path-vectors.json)(합성 값 29사례, 기대값은 손으로 적음) — Detector 단위 시험이 읽고, Trainer 구현에도 같은 결과임을 확인했다(입력 순서를 뒤집어도 같음)
  - 판정 순서(4.1 표): 기능 꺼짐 → 기록 없음 / 판정 관측 `transaction_id` 없음 → relationNotes `not_applicable` / `transaction_id_missing` / 거래 행 없음·도착 확정 전 → `deferred_to_transaction` / 붙이는 판정이 아님 → 기록 없음 / 인터페이스(3.1a) → 항목·미등록 규칙 순. **입력 상한(`relationInputs.truncated`)이면 붙이는 판정을 정할 수 없어 그 거래의 모든 판정에서 평가한다**(relation-rules.md 3.4 `required_step`의 `graph_truncated`와 같은 방식 — 켜진 항목과 미등록 인터페이스 모두 `capacity` + `graph_truncated` 보류, 4.1 표 예외)
  - 대표 행 모호(3.1a)의 "후보 행들의 `interface_id`가 모두 같음"은 관계 입력의 거래 행 조회가 함께 센다(`relation_inputs.py` `_TRANSACTIONS` — 없음 값 실행 수와 `COLLATE "C"` 최소·최대, 기록은 `RelationInputs.common_interface_id`, evidence에는 남기지 않음)
  - `protocolReview`는 판정 계획이 아니라 최종 결과로 넣는다(`evidence_v4.py` `build_evidence` — 그 방법이 최종 `methodResults`에서 T일 때만)
  - 방법 결과 evidence: N·T `[{interfaceMatched, routes: [{steps, matched}]}]`(`steps`는 process 노드 id), 미등록 T는 `interfaceMatched: false`·모든 경로 `matched: false`(경로를 만들 수 없으면 `routes: []`), 보류 `[{reason}]`. `protocolReview`는 T일 때만, 키는 5.3 그대로(`ruleSetRevision` = 시도에 고정한 룰 세트 리비전, `transactionObservationId` = 대표 행, 모호면 null)
  - 하드룰 대조(`evidence_v4.py` `_check_hard_rules`): 프로토콜 방법은 판정마다 많아야 1개, `hard:proto-*`는 시도에 고정한 켜진 항목이어야 하고, T의 위험도는 항목 `severity`(미등록은 `hardRules` 항목 `severity`)와 같아야 한다. 어긋나면 `hard_rule_selection_mismatch`
  - relationNotes 순서: 내장(`hard:transaction-failed`) → 관계 규칙 → 프로토콜
- 한계(CSV 재생 순서): CSV 재생 수집 경로는 거래 행을 PROCESS보다 먼저 커밋한다(`server/worker/collector/input_store.py` 정렬 `('T','P','M','B')`, `input_runner.py` tick마다 1행, P 커밋 즉시 detect 작업) — 거래 행이 있다는 것만으로 3.1 구조 전제("그 거래의 PROCESS가 다 기록됨")를 쓰면 뒤 PROCESS가 아직 없는 첫 판정에서 `required_step`·`precedes` 오탐이 난다. 그래서 "거래 행 도착"을 **도착 확정**(최소 지연 30초)으로 정의한다(3.4). 남는 오탐 조건은 행 사이 간격이 아니라 **그 거래의 TRANSACTION 행 커밋부터 마지막 PROCESS 커밋까지 누적 시간(≈ PROCESS 수 ÷ 재생 속도) > 30초**다 — CSV 재생은 거래마다 T → P → M 순으로 1행씩 커밋하므로(`server/worker/collector/input_store.py:41,47` 정렬, 속도 0.1~10000행/초·기본 10, `input_store.py:85-86`) 그 거래의 k번째 PROCESS 판정은 T 커밋 뒤 약 k ÷ 속도 초에 입력을 읽고, 이 값이 30초를 넘는데 뒤 PROCESS가 아직 커밋 전이면 도착 확정 상태에서 단계가 빠진 입력을 본다(예: 1행/초에서 PROCESS 31개 이상, 0.1행/초에서 4개 이상). 그래서 느린 재생·PROCESS가 많은 거래에서는 오탐이 날 수 있고, 근본 해결은 Collector 재생 순서 변경(PROCESS·MESSAGE 뒤 TRANSACTION)이다(Collector 세션 몫). 5단계 관계 규칙·6단계 프로토콜·7단계 재판정이 같은 도착 확정 정의(relation-rules.md 3.4)를 쓴다
- 성능 확인(relation-rules.md 4절 상한 상향 전제): 관계 입력 읽기 시간이 시도 기한(`deadlineMs` 기본 3000, `policy.py:15`) 안에 드는지 작은 측정 1회
