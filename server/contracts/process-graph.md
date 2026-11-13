Created: 2026-11-12T15:43:06+09:00
Updated: 2026-11-12T19:43:27+09:00
Author: Claude Code(WebUI builder)
Status: current — 실험 기능(층 A 구현, 층 B·C 어휘 예약)

# 실행 그래프 응답 계약(층 A 구현, 층 B·C 예약)

- 범위: 세 층이 같은 형식으로 내보내는 그래프 문서 `{graph, nodes, edges}`. 지금 구현은 **층 A(거래 내부 실행 그래프, `layer="instance"`)** 만이다. 층 B(인터페이스별 정상 패턴)는 어휘·id만, C(거래 간 사건)는 어휘만 예약
- 저장: **새 표·migration 없음**. 층 A는 기존 Process Flow 조회(`analysisReadModel.ts` `readDecisionFlow`와 같은 쿼리 3개·상한)로 읽은 행에 그래프 전용 조회 3개(시도별 최신 판정·거래 재시도·거래 대표 행, `/graph`만)를 더해 API 메모리에서 노드·간선으로 바꾼다. 원천 행 복제·간선 표·인덱스 추가 없음
- 묶음은 기존 Process Flow와 같은 **화면 표시용**이다. 부모 연결을 새로 만들거나 저장하지 않는다(002 `data_*` 행 불변)
- 연결: `GET /api/analysis/decisions/:id/graph`(아래), 프론트 `features/analysis/data/analysisApi.ts` `readDecisionGraph`·`parseGraph`
- 근거: `infra/database/schema.dbml` `data_transactions`·`data_processes`·`data_messages` 주석(부모 FK는 검증 뒤에만, 미연결·모호는 NULL+flags), migration 004 `body_link_status` 어휘, 원천 값 형식 가정(아래 "없음 판정")

## 문서 형식

```json
{
  "graph": { "layer": "instance", "id": "<거래 ID>", "version": null, "sourceId": "<uuid>", "snapshotId": null,
             "period": null, "ruleSetRevision": null, "generatedAt": "2026-11-12T08:00:00.000Z" },
  "nodes": [ { "id": "process:TX-1:PR001", "kind": "process", "attrs": { } } ],
  "edges": [ { "id": "precedes:process:TX-1:PR001:process:TX-1:PR002", "kind": "precedes",
               "from": "process:TX-1:PR001", "to": "process:TX-1:PR002",
               "verification": "verified", "evidence": { "handoff": "match", "reason": null } } ]
}
```

- `graph.layer`: `instance`(A) | `pattern`(B) | `incident`(C)
- 층 A 머리: `id` = 원천 TRANSACTION_ID 문자열(없음이면 null; 응답 최상위 `id`는 요청한 판정 ID), `version`·`snapshotId`·`period`·`ruleSetRevision` = null(이 조회 경로에 값이 없음), `sourceId` = 판정 PROCESS의 원본, `generatedAt` = 응답 생성 시각(UTC ISO)
- 노드·간선 순서에 의미 없음. 깊이·분기 수를 가정하지 않는다(2단계 고정 아님). 순환이 있어도 그대로 내보낸다(DAG 가정 금지)

## 어휘(고정)

| 구분 | 값 | 층 | 의미 |
|---|---|---|---|
| 노드 | `transaction` | A | 거래(재시도 묶음 1개) |
| 노드 | `process` | A | 같은 거래의 같은 Process ID(재시도 묶음) |
| 노드 | `message` | A | MESSAGE 실행 1건 |
| 노드 | `missing` | A | 참조됐지만 응답에 PROCESS 행이 없는 단계(화면 점선) |
| 노드 | `interface`·`pattern_step` | B 예약 | 인터페이스 묶음·패턴 단계 |
| 노드 | `incident` | C 예약 | 사건 |
| 간선 | `contains` | A | T→P, P→M |
| 간선 | `precedes` | A, B 예약 | 선행 → 후행 |
| 간선 | `hands_off` | A, B 예약 | 선행 OUT 메시지 → 후행 IN 메시지 인계 비교(층 B는 메시지 노드 없이 단계 간 인계) |
| 간선 | `branches_to` | B 예약 | 패턴 분기. **층 A는 내보내지 않음**(분기 = `precedes` 나가는 간선 2개 이상, 노드 `attrs.branch=true`) |
| 간선 | `member_of` | C 예약 | 거래 → 사건 |

- `verification`(간선): migration 004 `body_link_status` 어휘 재사용 — `not_checked`·`unmatched`·`verified`·`ambiguous`·`failed`. 층 A 간선에서만 의미가 있고, 층 B·C는 null(지지 건수 등은 `evidence`). 층 A는 `failed`를 내보내지 않는다(예약)
- `evidence`: 참조 id와 판정 결과만(원천 값·payload 복사 없음). `contains`는 null

## id

- 노드 id는 원천·실행 값으로 만들고 도착 순서·리비전에 따라 바뀌지 않는다. **소비자는 id를 쪼개 읽지 않는다** — 구성 요소에 `:`가 들어갈 수 있으므로 간선 끝은 `from`·`to`로만 찾는다
- 구성 요소 이스케이프가 **유일한 변환**: `%` → `%25` 다음 `:` → `%3A`. trim·대소문자 변환·`\N` 같은 표기 변환 없음. attrs에는 원래 값
- **없음 판정** — 계약 함수 `graph_value_absent(value, field)`(백엔드 `graphValueAbsent`, `backend/src/services/processGraph.ts`). Detector `source_absent`(`worker/detector/method_results.py`)·`worker/derived_expression.py`와 **별개 함수**다(규칙이 다르고 서로 바꿔 쓰지 않는다)
  - 없음: NULL, 또는 **ASCII 공백(0x20)·탭(0x09)·CR(0x0D)·LF(0x0A)만** 앞뒤에서 뺀 값이 `""` 또는 `\N`. 그 밖의 문자(U+FEFF·U+0085·U+001C·U+00A0 등)는 지우지 않는다. JS `trim()`·Python `strip()`을 쓰지 않는다(`trim()`은 U+FEFF, `strip()`은 U+0085·U+001C를 지워 결과가 갈린다)
  - `DEPEND_PROCESS_ID`만 추가: 위처럼 뺀 값이 정확히 `NONE`(대소문자 구분)이면 없음 = 시작 단계(선행 간선·missing 노드 없음). `none`·`None`·U+FEFF가 붙은 `NONE` 등은 값(선행 묶음을 찾고 없으면 missing). 다른 열의 `NONE`은 값
  - 형식 가정: `DEPEND_PROCESS_ID` 값은 `NONE` 또는 `<인터페이스>_PRnnn` 형식으로 본다. 다른 철자·앞뒤 공백·`:` 포함 값은 위 규칙대로 값으로 다룬다(없음으로 바꾸지 않음)
  - 없음이 아닌 값은 **원래 값(앞뒤 문자 포함)** 그대로 id·비교에 쓴다. 없음 값으로 원천 키 id를 만들지 않는다

| 대상 | id | 없음일 때 |
|---|---|---|
| 거래 | `transaction:<TRANSACTION_ID>` | `unkeyed:<observation_id>` |
| PROCESS 묶음 | `process:<TRANSACTION_ID>:<PROCESS_ID>` | 둘 중 하나라도 없음 → 행마다 `unkeyed:<observation_id>`, `attrs.unkeyed=true`, 선행·인계 간선을 만들지 않음 |
| MESSAGE | `message:<execution_id>`(`data_observations.execution_id`, 리비전과 무관) | `unkeyed:<observation_id>` |
| missing | `missing:<참조한 노드 id>:<찾은 PROCESS_ID>` | — |
| contains·precedes | `<kind>:<from 노드 id>:<to 노드 id>` | — |
| hands_off | `hands_off:<from 노드 id>:<to 노드 id>:<OUT 메시지 노드 id>` | — |

- missing·간선 id에 들어가는 노드 id는 다시 이스케이프하지 않는다
- **노드 kind마다 id 구성 요소 수 고정, 결측 구성 요소는 빈 칸(이스케이프 후 빈 문자열)**. 구성 요소를 빼서 id 길이를 바꾸지 않는다
  - 이유: 간선 id는 노드 id를 다시 이스케이프하지 않고 `:`로 잇는다. 구성 요소 수가 바뀌면 다른 끝점 쌍이 같은 간선 id가 된다. 예(Trainer): adapter를 빼는 가변 길이라면 `from=pattern_step:I:P`(adapter 없음)·`to=pattern_step:pattern_step:X:Y`와 `from=pattern_step:I:P:pattern_step`·`to=pattern_step:X:Y`가 둘 다 `precedes:pattern_step:I:P:pattern_step:pattern_step:X:Y`. 빈 칸 고정이면 앞쪽은 `precedes:pattern_step:I:P::pattern_step:pattern_step:X:Y`로 갈린다
  - 층 A kind는 이미 고정이다: `transaction` 1칸, `process` 2칸, `message` 1칸, `missing` = 참조한 노드 id + process_id, `unkeyed` 1칸. 간선 kind 다음 첫 토큰은 노드 kind 리터럴(`transaction`·`process`·`message`·`missing`·`unkeyed`·`pattern_step`)이고 kind마다 칸 수가 정해져 있어 생산자 쪽 해석은 모호하지 않다. 그래도 **소비자는 id를 쪼개 읽지 않는다**(`from`·`to` 사용)
- 공용 시험 벡터: [`process-graph-id-vectors.json`](process-graph-id-vectors.json) — id 조합(일반·`:`·`%`·`%3A`·앞뒤 공백·한글·U+FEFF·U+0085·U+001C·missing·간선)과 없음 판정(NULL·`""`·공백·탭·CR·LF·`\N`·` \N `·` A1 `, `DEPEND_PROCESS_ID`의 `NONE`·` NONE `·`none`, U+FEFF·U+0085·U+001C·U+00A0가 앞뒤에 붙은 값, 벡터 `field`가 없으면 `DEPEND_PROCESS_ID`가 아닌 열). 백엔드 단위 시험(`backend/tests/process-graph.test.mjs`)이 전부 확인한다. Trainer·Detector Python도 같은 파일을 쓴다
- `unkeyed:`는 노드 종류가 아니라 id 접두어다(`kind`는 그대로 `transaction`·`process`·`message`)

### 층 B id 예약(구현 전)

| 대상 | id | 없음일 때 |
|---|---|---|
| 패턴 단계 | `pattern_step:<interfaceId>:<processId>:<adapterType>` | adapterType 없음 → 마지막 칸 빈 문자열(`pattern_step:IF_A:IF_A_PR001:`). interfaceId·processId 중 하나라도 없음 → 노드를 만들지 않고 미결(`undetermined`)로 센다 |
| precedes·branches_to | `<kind>:<from 노드 id>:<to 노드 id>` | — |
| hands_off | `hands_off:<from 노드 id>:<to 노드 id>` | — |

- 없음 판정·이스케이프는 층 A와 같다(`graph_value_absent`, `DEPEND_PROCESS_ID`가 아닌 열 규칙)
- 층 B에는 `message` 노드가 없으므로 **층 B `hands_off` id에는 메시지 구성 요소가 없다**(층 A는 `hands_off:<from>:<to>:<OUT 메시지 노드 id>`). 층 B 간선 `from`·`to`는 `pattern_step` 노드
- 층 B 간선: `verification` = null, 지지 건수 등은 `evidence`, 머리 `graph.layer` = `pattern`
- 벡터: `process-graph-id-vectors.json` `patternIdVectors`(8건, 노드 없음 벡터는 `expectedId: null`·`outcome: "undetermined"`). 백엔드는 시험용 순수 함수 `patternGraphIds`(`processGraph.ts`)로 확인하며 API 응답은 바뀌지 않는다

## 층 A 생성 규칙

### 입력 행

- Process Flow와 같다: 판정 PROCESS와 같은 원본 안 같은 거래 ID 문자열의 TRANSACTION 1건(검증된 FK가 있으면 그 행)·PROCESS·MESSAGE, **실행마다 가장 큰 리비전 1건**(같은 실행의 새 리비전은 중복이 아님). 판정 PROCESS 실행은 판정에 쓰인 관측 그대로(`focus`)
- 단 **거래 노드는 아래 "거래 노드 대표 행"을 쓴다**(`/graph`만). Flow 응답 `transaction`(FK 행 또는 최신 리비전 1건)은 바꾸지 않는다

### 거래 노드 대표 행(Detector `relation-rules.md` 7.2와 같은 규칙, 11/12 합의)

- 후보: 같은 원본·같은 거래 ID 문자열의 TRANSACTION 행, **실행(`execution_id`, 없으면 관측)마다 최신 리비전 1건**
- 0건 → 위 입력 행(FK 행 등)을 그대로 쓴다(모호 아님). 1건 → 그 행
- 2건 이상(거래 재시도): **재시도 번호(`retry_count`)가 가장 큰 실행**이 대표. 가장 큰 번호의 실행이 2건 이상이거나, 재시도 번호가 없는(NULL) 행 또는 실행 id가 없는(NULL) 행이 하나라도 있으면 **모호** — 대표를 고르지 않는다
- 노드 `status`·`responseCode`·`observationId`·`revisionNo`·`interfaceId`·`channelId`·`processTimeMs`·`processCount`는 대표 행의 값. 모호면 모두 null(화면 보류)이고 `attrs.verification = "ambiguous"`(아니면 null). 노드 id는 거래 ID로 만들어 대표와 무관
- 거래 ID가 없음(`graph_value_absent`)이면 후보를 찾지 않고 입력 행 그대로(`unkeyed:`)
- 구현: 백엔드 순수 함수 `pickTransactionRow`(`processGraph.ts`) + 조회 1개(재시도 번호 큰 순 앞 2건과 실행 수·실행 id 없음 수·재시도 번호 없음 수, 재시도가 많아도 2행)
- 상한도 같다: PROCESS 200·MESSAGE 500. 넘으면 응답 `truncated=true`, `counts`는 상한과 무관한 실제 건수
- BODY·응답 메시지 원문·원천 시각 원문은 읽지 않는다(Process Flow와 같음)

### 노드

- `transaction` attrs(대표 행 값, 위 절): `observationId`, `revisionNo`, `transactionId`, `interfaceId`, `channelId`, `status`, `responseCode`, `processTimeMs`, `processCount`, `unkeyed`, `verification`(대표 모호 `ambiguous`, 아니면 null), `retries[]` `{retryCount, status, observationId}`(같은 원본·같은 거래 ID의 TRANSACTION 실행마다 최신 리비전, 재시도 번호 순 **앞 50건**), `retriesTruncated`(50건을 넘어 잘렸으면 true). 거래 노드는 재시도와 무관하게 1개
- `process` attrs: `transactionId`, `processId`, `channelId`(시도들의 `data_processes.channel_id` 원래 값이 **모두 같고 값이 있을 때만** 그 값, trim 없음 — 없음(`graph_value_absent`)·있음과 없음이 섞임·값이 다름·묶음 모호면 null. protocol-rules.md 3.2 6단계 `channel_absent`·`channel_ambiguous`와 같은 기준이라 화면이 판정과 같은 경로를 그린다), `dependsOn`(시도들의 값이 같으면 그 값, 다르면 null), `status`·`responseCode`(가장 큰 재시도 번호 시도의 값, 모호면 null = 화면 보류), `focus`(판정 PROCESS 포함 여부), `verification`(모호면 `ambiguous`, 아니면 null), `unkeyed`, `branch`, `selfDependency`(DEPEND_PROCESS_ID가 자기 Process ID → 간선 없음), `attempts[]` `{observationId, executionId, revisionNo, retryCount, status, responseCode, dependsOn, totalCount, focus, decisionId, grade, channelId}` 재시도 번호 순(`channelId`는 그 시도의 원래 값, `/graph`에서만 — `/flow` 응답은 그대로)
  - `decisionId`·`grade`: 그 관측의 최신 최종 판정 1건(018 인덱스 순서 `decided_at DESC, id DESC`, 운영 리포트와 같음)의 ID와 저장된 v4 `composite.grade`. 판정이 없거나 v4가 아니면 null
  - 최신 판정 순서 `decided_at DESC, id DESC`는 **의도적으로 유지**한다: 018 인덱스·기존 분석 화면·운영 리포트와 같은 순서여야 같은 상세 화면의 카드들이 같은 판정을 가리킨다. 층 C(예약)는 리비전을 가로질러 비교하므로 다른 순서 `PARTITION BY d.execution_id ORDER BY o.revision_no DESC NULLS LAST, d.decided_at DESC, d.event_sequence DESC`를 쓴다(층 A 순서를 바꾸지 않는다)
  - `DETECT_JOB_CONTRACT=v03`으로 저장된 판정은 전부 `grade` = null이다(`composite.grade`는 v04 / migration 016 전환 뒤부터, detection-decision.md C28). 화면은 `decisionId`가 있는데 `grade`가 null인 시도를 "판정 없음"으로 그리지 않는다(판정은 있고 등급 값만 없음)
  - 최신 판정·거래 `retries[]`·거래 대표 행은 `/graph` 요청에서만 따로 조회한다(상한 안 PROCESS 시도마다 1건, 재시도 51건까지, 대표 후보 2건). `/flow`는 이 조회를 하지 않고 응답도 그래프 추가 전과 같다
  - **모호(ambiguous)**: 실행마다 최신 리비전으로 줄인 뒤에도 같은 (거래, Process ID, 재시도 번호)가 2건 이상, 또는 시도가 2건 이상인데 재시도 번호가 없거나 숫자가 아닌 것이 있음. 대표 시도를 고르지 않고 전부 `attempts[]`에 둔다
- `message` attrs: `observationId`, `executionId`, `revisionNo`, `messageId`, `processId`, `direction`, `status`, `dataSize`, `messageIndex`, `unkeyed`
- `missing` attrs: `processId`(찾은 값), `reason` — `not_logged`(PROCESS 상한 안에서 행 없음) | `not_loaded`(PROCESS가 상한에 걸려 있을 수 있음)

### contains

- T→P: 묶음의 **모든 시도**의 FK `transaction_observation_id`가 **이 거래 노드의 대표 관측(`attrs.observationId`)과 같을 때만** `verified`. 하나라도 NULL이거나 다른 TRANSACTION 관측(같은 거래 ID의 다른 재시도·리비전 포함)을 가리키거나 대표가 모호(`observationId` null)면 `not_checked`(기존 Flow 쿼리의 문자열 묶음). 거래 행이 없으면 T→P 간선 없음
- P→M: 메시지 `process_observation_id`가 그래프 안 PROCESS 시도를 가리키면 그 묶음에서 `verified`. 아니면 같은 거래의 Process ID 문자열로 묶음을 찾아 `not_checked`. 그 묶음이 없으면 `missing:<메시지 노드 id>:<PROCESS_ID>` 노드 → 메시지 `unmatched`. 메시지 Process ID가 없음이고 FK도 없으면 간선 없음
- DB가 검증한 연결은 이 두 FK뿐이다. 수집기는 지금 FK를 채우지 않으므로(analysis-ui.md Process Flow 절) 실제 수집분은 `not_checked`가 기본이다

### precedes

- 원천 `DEPEND_PROCESS_ID`는 "이 PROCESS(후행)가 그 PROCESS(선행)에 의존"을 뜻한다. 간선 방향은 그 열과 **반대**: `from` = 선행(DEPEND_PROCESS_ID가 가리키는 묶음), `to` = 후행(그 열을 가진 묶음)
- DEPEND_PROCESS_ID가 없음(`graph_value_absent(value, DEPEND_PROCESS_ID)`, `NONE` 포함) → 시작 단계(간선·missing 없음)
- 문자열 일치(같은 거래): 선행 후보 묶음이 정확히 1개 → `verified`. 0개 → `missing:<후행 노드 id>:<값>` → 후행, `unmatched`(후행 묶음이 모호하거나 시도 값이 서로 다르면 `ambiguous`). 후행 시도들의 DEPEND_PROCESS_ID가 서로 다르거나(값마다 간선 1개씩) 양 끝 묶음 중 하나가 모호 → `ambiguous`. 재시도는 한 묶음이라 모호 원인이 아니다
- 분기: 한 노드에서 서로 다른 후행으로 나가는 `precedes`가 2개 이상이면 그 노드 `attrs.branch=true`
- evidence `{handoff, reason}` = 아래 인계 비교 요약. `verified`가 아니면 `{handoff:"undetermined", reason:"precedes_not_verified"}`

### hands_off

- `verified` 선행 간선마다 선행 묶음의 `DIRECTION='OUT'` 메시지 × 후행 묶음의 `DIRECTION='IN'` 메시지를 같은 거래 안 MESSAGE_ID 문자열로 비교(일치 정의: OUT 집합 ∩ IN 집합 ≠ ∅). 방향 값은 저장값 그대로 비교(방향 없음 행은 제외)
- 간선은 선행 OUT 메시지 1건마다 1개: `from` = 선행 PROCESS 노드, `to` = 후행 PROCESS 노드, `verification` = 그 선행 간선 값, evidence `{result, reason, outMessage, inMessages[]}`(메시지 노드 id)

| result | reason | 조건 |
|---|---|---|
| `match` | null | 같은 MESSAGE_ID인 IN이 있음(`inMessages` = 그 IN들) |
| `mismatch` | null | OUT 1·IN 1, 둘 다 ID 있음, 다름, 제외 구간 아님 |
| `undetermined` | `excluded_transaction_suffix` | 위와 같지만 그래프 거래 ID(`graph.id` = TRANSACTION 행 값, 행이 없거나 값이 없음이면 판정 PROCESS 값) 끝자리가 0채움 2자리(`/-0\d$/`, 예 `-01`) — 인계 기준선 산정 제외 구간, ESB 업체 확인 전 |
| `undetermined` | `multiple_messages` | OUT 또는 IN이 2건 이상이고 같은 ID 없음(후보를 고르지 않음, `inMessages` = 비교한 IN 전부) |
| `undetermined` | `missing_message_id` | 비교할 MESSAGE_ID가 없음 |
| `undetermined` | `no_in_message` | 후행 IN 없음 |

- 선행 간선 evidence 요약: `match`가 하나라도 있으면 `match`, 아니면 `mismatch`가 있으면 `mismatch`, 아니면 첫 `undetermined`의 사유. OUT이 없으면 hands_off 간선 없이 `{handoff:"undetermined", reason:"no_out_message"}`. MESSAGE가 상한에 걸리면 `{handoff:"undetermined", reason:"messages_truncated"}`(간선 없음)

## GET /api/analysis/decisions/:id/graph

- 기존 `GET …/flow`는 바꾸지 않고(응답 필드 그대로, 현재 `ProcessFlowCard` 계속 사용) 같은 조회를 쓰는 형제 경로를 더했다. 근거: 기존 응답 필드·소비자 불변, 그래프 문서 최상위 형식을 그대로 응답에 쓸 수 있음
- `:id` 해석·인증·역할(user/admin 읽기)·오류(400 `invalid_decision_id`, 404 `decision_not_found`, 503 `analysis_storage_unavailable`)는 Flow와 같다. DB 권한은 런타임 역할 `powercode_api` SELECT만(`data_*`·`run_process_decisions`)
- 응답: `{id, jobId, truncated, counts, graph, nodes, edges}` — `id`·`jobId`·`truncated`·`counts`는 Flow와 같은 값, 나머지는 위 문서 형식

## 미정·조율 항목

- 0채움 2자리 끝자리 정규식(`/-0\d$/`)은 분석 결과 기반 임시 기준. ESB 업체 확인 뒤 확정
- 거래 노드 대표 행은 `/graph`만 바꿨다. 트리 보기(Flow 응답 `transaction`)는 여전히 FK 행 또는 최신 리비전 1건이라 재시도가 있으면 두 보기의 거래 상태가 다를 수 있음 — Flow 계약을 맞출지 결정 필요
- 층 B·C 머리 `version`·`snapshotId`·`period`·`ruleSetRevision` 채우는 규칙은 각 층 설계에서 정한다
