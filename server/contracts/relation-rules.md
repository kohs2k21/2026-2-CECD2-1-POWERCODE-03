Created: 2026-11-12T16:31:29+09:00
Updated: 2026-11-12T19:23:00+09:00
Author: Claude Code — Detector 세션(builder)
Status: current — 실험 기능 설계(구현 전 부분은 9절)

# 관계 규칙 Detector 설계 초안 (실험 기능)

- 범위: 승인된 거래 내부 관계 규칙(`relationRules[]`)의 판정 의미, 소프트룰 `scope` 평가, 새 내장 하드룰 `hard:transaction-failed`(7절), 거래 행 도착 계기 재판정(6절), 룰 세트 hash v3. **코드 변경 없음**(구현 시 바뀔 곳은 9절)
- 소유: Detector. 룰 세트 저장 형식·API 검증은 [rule-set.md](rule-set.md), 그래프 생성 규칙은 WebUI 그래프 계약(`process-graph.md`)을 따른다
- 근거 코드: origin/dev `e038b5e`. 행 번호(file:line)는 이 커밋 기준(짧은 파일명은 `server/worker/detector/` 아래)
- 실험 기능. v04 경로에서만 의미가 있다(10절)
- 표기: **[확정 11/12 사용자]** 사용자 결정(Trainer·WebUI 세션 질문 창 직접 답) / **[확정 11/12 사용자(3차)]** 3차 답(2차 두 항목 대체) / **[확정 11/12 사용자(4차)]** 18:10 WebUI 답 "둘 다 추천대로 진행해"(재판정 계기·마지막 단계) / **[합의]** Detector·WebUI·Trainer 세션 합의(11/12) / **[제안]** 이 초안이 정한 값 / **[확인 필요]** 데이터로 확인할 사실

## 1. 판정 단위·대분류·결과

- 판정 단위 = **PROCESS**(detect 작업 1건 = PROCESS 관측 1건, `jobs.py:68-123`) [확정 11/12 사용자]
- 대분류 = **hard**, 식별자 형식 `hard:rel-<8hex>` [확정 11/12 사용자] — 정확히는 `hard:rel-<소문자 hex 8자리>`(`^hard:rel-[0-9a-f]{8}$`, 소프트룰 `instanceIdPattern`의 hex 부분 `server/worker/methods/registry.py:41`과 같음) [제안]. 현 정규식 `METHOD_ID`(`method_results.py:16`)를 그대로 만족하므로 `CATEGORIES`(`:9`)는 바뀌지 않는다 [합의]
- id는 API가 승인 저장 때 생성하고 이후 불변, 내장 하드룰 key는 `rel-`로 시작하지 않는다(접두 예약) [제안]
- `required_step`은 **뒤 단계에서 판정**(앞의 필수 단계가 없으면 위반 = 이상) [확정 11/12 사용자(3차)]. 거래 완료 시점 최종 판정에 **마지막 단계 누락도 포함** [확정 11/12 사용자(4차)] — 사용자 답 "둘 다 추천대로 진행해"(WebUI 질문의 추천안 문안: 평가 위치는 Detector가 정함, 최소 지연 30초, 탐색 기간 24시간)
  - 해석·평가 위치 선택 [제안, Trainer 합의] — 4차 답은 위치를 Detector에 맡겼을 뿐 아래 위치를 정한 것이 아니다: "뒤 단계"를 **거래 완료**로 읽어, 거래 행 도착 때 그 거래의 **전체 단계 집합**에 필수 단계가 있는지 본다. 중간·마지막 단계를 구분하지 않는다. 위반이면 그 거래에서 가장 늦게 끝난 PROCESS의 판정을 재판정해 T를 낸다(3.1)
- `precedes`·`message_handoff`는 판정, `allowed_branch`는 정보 표시만 [합의 — Trainer 의견, Detector 보완]. 그 결과 **판정하는 kind는 셋**, 정보 표시는 `allowed_branch`뿐(`evidence.relationNotes`, 방법 아님, 종합 영향 없음)
- 늦게 도착한 행: **한 번 재판정** [확정 11/12 사용자(3차)], 계기는 **거래 행 도착**(최소 지연 30초, 탐색 기간 24시간, 오지 않으면 보류로 끝) [확정 11/12 사용자(4차)]. 24시간의 기준점을 "보류 판정 후(그 판정의 `decided_at`)"로 두는 것은 [제안] — 6절
- 대체된 결정: 2차 "required_step 정보 표시만"·"보류로 끝(재판정 없음)"은 3차로, 3차의 "선행 PROCESS 도착 + 10분 시간창" 계기와 "마지막 단계 누락 제외"는 4차로 **대체**됐다
- 위반 = 판정 규칙의 방법 T → hard 대분류 `anomalous` → 등급 `anomaly`(`decision.py:78-80`, k = 1 고정 U4) [확정 11/12 사용자]
- 임계값 기반 규약(단계별 timeout 등)은 관계 규칙(hard)이 아니라 **소프트룰(이상 징후) 후보로 추천** [확정 11/12 사용자]. `relationRules`에는 임계값 kind를 두지 않는다 [합의]
- rule-set.md 원칙(`rule-set.md:33`) 수정: "하드룰 = 내장 목록 + **승인된 관계 규칙**. 관계 규칙의 조건은 승인 때 고정, 켜기·끄기와 등급(`severity`)만 편집" [확정 11/12 사용자]. detection-decision.md 4.1 "사용자가 추가·수정할 수 없음"(`detection-decision.md:108`)도 같은 뜻으로 고친다
- 화면 "규약 위반" 표기는 근거가 `hard:rel-*`일 때만. 기존 내장 하드룰은 상태·정합성 이상 그대로 [합의]

## 2. 룰 세트 스냅샷 `relationRules[]` [합의]

```json
{"id": "hard:rel-3f9a2c1d", "kind": "message_handoff",
 "scope": {"interfaceId": "IF_A", "adapterType": "HTTP"},
 "from": {"processId": "IF_A_PR001"}, "to": {"processId": "IF_A_PR002"},
 "match": {"from": "OUT", "to": "IN", "key": "message_id"},
 "severity": "critical", "enabled": true,
 "source": {"jobId": "…", "snapshotId": "…", "patternEdgeId": "…",
            "counts": {"applicable": 100, "satisfied": 97, "violated": 2, "undetermined": 1},
            "period": {"from": "…", "to": "…"}}}
```

| 필드 | 규칙 |
|---|---|
| `kind` | 판정(방법): `required_step` [확정 11/12 사용자(3차)] · `precedes` · `message_handoff` [합의] / 정보 표시만: `allowed_branch` [합의]. 모르는 kind는 저장 거절(`PUT /api/rule-set` 422), 저장소에 있으면 판정 시 fail-closed(시도 실패, `relation_rule_kind_unknown:<id>` [제안]) |
| `scope.interfaceId` | `precedes`·`message_handoff`·`allowed_branch`: **표시·출처용** — PROCESS 관측에 interface_id가 없음(수집 P 투영 열에 없고 `server/worker/collector/storage.py:21-23`, `002_core_erd.sql:60-67`, P→T FK는 현재 적재 경로에서 거의 비어 있음). `required_step`: 거래 행에는 `interface_id`가 있으므로(`002_core_erd.sql:54`) **적용 대상 거래를 고르는 키**(필수) [제안 — 10절 결정 필요] |
| `scope.adapterType` | 선택. 있으면 판정 PROCESS의 `adapter_type`과 원래 값 정확 일치일 때만 적용. `required_step`에는 두지 않음 [제안] |
| `from`·`to` | 정규형은 **`{processId}`만**(다른 키는 저장 422, 판정 시 fail-closed) [제안]. `from` 필수. `to`는 `precedes`·`message_handoff`·`allowed_branch` 필수, `required_step`은 **null**(후보 하나에 필수 단계 하나 = `from.processId`) [제안, Trainer 합의] |
| `match` | `message_handoff`만, 처음엔 `{from:"OUT", to:"IN", key:"message_id"}` 하나만 허용 |
| `severity` | `critical`·`warning`·`info`. 판정 규칙의 위반 T일 때 `riskLevel`로 기록(위험도는 detection-decision.md 9절 그대로). 정보 표시 규칙은 저장·편집만 되고 판정에 쓰이지 않음 |
| `source` | 출처(Trainer 후보 작업·스냅샷·패턴 간선·건수 4종·기간). **hash 입력 아님**. 같은 규칙 재승인(건수만 변경)은 `rule_set_unchanged`, 출처는 최초 승인 유지 [합의] |

- `evaluatedAt` 어휘 [제안, Trainer 합의]: `precedes`·`message_handoff` = `successor`(뒤 단계 도착 때 1차, 거래 행 도착 때 최종), `required_step` = `transaction`. kind에서 정해지는 값이라 스냅샷에 저장하지 않고 hash 입력도 아님(후보·화면 표시용)
- Trainer 후보의 `evaluable:false`는 해제됐다(WebUI가 Trainer에 전달). 마지막 단계 후보도 `to: null`·"거래 완료 때 평가" 표시로 저장된다
- 같은 규칙의 기준 [제안]: 조건(`kind`·hash에 들어가는 `scope`·`from`·`to`·`match`)이 같으면 같은 규칙 → 재승인은 기존 id 유지. 되돌리기는 스냅샷의 id를 그대로 재생성(소프트룰 되돌리기와 같은 방식, rule-set.md 저장 절)
- 판정 PROCESS에 어떤 규칙을 고르나 [제안]: `precedes`·`message_handoff`·`allowed_branch`는 켜진 규칙 중 `to.processId` = 판정 PROCESS `process_id`(원래 값 정확 일치, `graph_value_absent`면 어떤 규칙도 적용 안 함) ∧ `scope.adapterType` 일치. `required_step`은 거래 단위(3.1). processId만으로 단계를 특정한다는 전제(실데이터 형식 `<인터페이스>_PRnnn`)는 확인 대기(10절)
- 선택되지 않은 규칙은 그 판정의 `methodResults`·n에 넣지 않는다(4절 soft scope와 같은 원칙). 선택된 정보 표시 규칙은 relationNotes에만 남는다

## 3. 평가 위치와 결과

### 3.1 평가 위치 — `required_step` 판정 [확정 11/12 사용자(3·4차)]·`precedes`·`message_handoff` 판정과 `allowed_branch` 정보 표시 [합의], 그 밖의 세부는 [제안]

| kind | 구분 | 평가 시점·PROCESS | 결과 |
|---|---|---|---|
| `precedes` | 판정(방법) | 후행(`to`) 도착 때 1차, 거래 행 도착 때 최종 | 판정 관측 자신의 `DEPEND_PROCESS_ID`가 `NONE`(시작 단계로 기록)이거나 `from.processId`와 다름 → **T(위반)**. 같은 값이고 from→to `precedes` 간선이 `verified` → N. 같은 값인데 선행 행이 없음(`missing` `not_logged`) → 1차는 수집 미완료 보류, **거래 행 도착 뒤에도 없으면 T** [제안, 6.2]. 간선이 `ambiguous`(선행 없음이면서 후행 묶음 모호 포함) → 방법 결과 보류(데이터 문제) `precedes_ambiguous`(5절) |
| `message_handoff` | 판정(방법) | 후행(`to`) 도착 때 1차, 거래 행 도착 때 최종 | from→to `precedes` 간선이 `verified`일 때만 판정. 그래프의 간선 evidence 요약("첫 undetermined")을 쓰지 않고 `hands_off` 간선별 `result`로 판단. 단 `no_out_message`·`messages_truncated`는 `hands_off` 간선이 없고 `precedes` 간선 evidence에만 있으므로(그래프 계약 `hands_off` 절 요약 문단) 거기서 읽는다. 순서 [제안]: `match`가 하나라도 있으면 N → `mismatch`가 있으면 **T** → 방법 결과 보류(데이터 문제 `missing_message_id`·`multiple_messages`) → 방법 결과 보류(입력 상한 `messages_truncated`) → 적용 안 함(`excluded_transaction_suffix`, 규약 확인 전 기준선 제외 — relationNotes) → 수집 미완료 보류(`no_in_message`·`no_out_message`). 간선이 없으면(다른 값·`NONE`) 적용 안 함 — 그 위반은 `precedes` 규칙 몫. 선행 간선이 `ambiguous`·`unmatched`이면 5절 공통 행(모호 → 방법 결과 보류, `not_logged` → 수집 미완료 보류, `not_loaded` → 방법 결과 보류)을 따름 |
| `required_step` | 판정(방법) | **거래 행이 있을 때만**(첫 판정 시도 시작 때 이미 있거나, 거래 행 도착 재판정 때), 그 거래에서 가장 늦게 끝난 PROCESS의 판정 | 아래 거래 단계 집합 검사. 필수 단계(`from.processId`)가 없으면 **T(위반)** |
| `allowed_branch` | 정보 표시 | 후행(`to`) | relationNotes `observed` 또는 `not_in_pattern` |

- 판정 관측 자신의 값 [제안]: T는 판정 관측 자신의 `DEPEND_PROCESS_ID` 값으로 내고, 그래프 묶음 `attrs.dependsOn`(시도 값이 다르면 null)보다 우선한다. 판정 PROCESS 묶음이 모호해도 판정 관측의 값이 하나면 T는 그 값으로 판단한다. N은 해당 `precedes` 간선이 `verified`일 때만 낸다
- `precedes` 평가 순서 [제안]: (1) 판정 관측 자신의 값으로 T 판정(`NONE`, 또는 없음이 아닌 값 중 `from.processId`와 다른 값) → (2) 키 결측(방법 결과 보류, 데이터 문제 — 아래) → (3) 간선 상태(`verified` → N, `ambiguous` → 데이터 문제, `missing` `not_logged` → 수집 미완료 보류(거래 행 도착 뒤면 T), `not_loaded`·`truncated` → 방법 결과 보류(입력 상한))
- 키 값이 없을 때 [제안]: `graph_value_absent`는 `NONE`과 NULL·`""`·`\N`을 모두 "없음"으로 보아 구분하지 못하므로, Detector는 **원래 값**으로 따로 판단한다
  - `DEPEND_PROCESS_ID`: ASCII 공백·탭·CR·LF만 앞뒤에서 뺀 값이 정확히 `NONE` → 시작 단계. NULL·`""`·`\N` → 데이터 문제 `input_missing` + `depend_process_id_missing`
  - `transaction_id` 없음(그래프 `unkeyed`) → 데이터 문제 `input_missing` + `transaction_id_missing`. 같은 거래 행을 찾을 수 없다고 수집 미완료로 빼지 않는다
- `required_step` 거래 단계 집합 검사 [제안, Trainer 합의]
  - 첫 판정 시도 시작 때 거래 행이 **아직 없으면**: 평가하지 않는다. 방법 결과도 보류도 아니고 relationNotes에 `{result: "deferred_to_transaction"}` 정보 기록만 남긴다. 1차 판정의 n에 넣지 않는다
  - 첫 판정 시도 시작 때 거래 행이 **이미 있으면**: 사용자 설명상 그 거래의 PROCESS가 모두 기록돼 있으므로 그 PROCESS가 가장 늦게 끝난 것인지 정할 수 있다. 그 시도에서 거래 단위 검사(거래 실패 7.2, 아래 단계 집합)를 바로 평가하고 `deferred_to_transaction`을 남기지 않는다
  - 평가(첫 판정 또는 거래 행 도착 재판정): 적용 대상 = 거래 행 `interface_id` = 규칙 `scope.interfaceId`. 단계 집합 = 같은 원본·같은 `transaction_id`의 PROCESS 행(실행마다 최신 리비전)의 `process_id` 원래 값. `from.processId` ∈ 집합 → N, 없으면 **T**
  - 붙이는 판정: 그 거래에서 가장 늦게 끝난 PROCESS — END_TIME 최대 → START_TIME 최대 → 관측 id 문자열 내림차순(가장 큰 값) 순으로 동률을 가른다(시각은 관측 `parsed_times`, `002_core_erd.sql:43`). END_TIME이 NULL이거나 해석되지 않는 PROCESS는 값이 있는 PROCESS 뒤에 두고(값이 있는 PROCESS가 하나라도 있으면 그중에서 고름), 그들끼리는 START_TIME(결측은 같은 방식으로 뒤) → 관측 id 내림차순으로 가른다. 위반이면 그 PROCESS 판정을 재판정해 T를 낸다. 그 PROCESS가 아닌 판정의 재판정에서는 `required_step`을 적용 안 함
  - 거래 묶음 판단은 방법 결과 보류: 거래 행 재시도 판단은 7.2와 같음(모호 → `input_invalid` + `transaction_row_ambiguous`), PROCESS 상한 초과(`relationInputs.truncated`) → `capacity` + `graph_truncated`, 키 결측은 위와 같음
  - 거래 행이 24시간 안에 오지 않은 거래는 평가하지 않는다(보류로 끝, 6절)
- 구조 전제 [확정 11/12 사용자 — Detector 세션 질문 답 "위반(이상)으로 판정", ESB 업체 확인 뒤 재검토 가능]: 거래 완료 시점에는 그 거래의 PROCESS가 다 기록돼 있다(사용자 설명 "PROCESS가 다 기록돼야 거래가 기록된다", 7.1). 그래서 거래 행이 왔는데 필수·선행 단계 행이 없으면 수집 미완료가 아니라 **위반**으로 본다. `DEPEND_PROCESS_ID`가 인터페이스 정의값이어도 이 판단은 성립한다(이전 "정의값이면 영구 보류" 위험은 해결됨). 사용자 설명을 근거로 한 Detector 해석이다
- 방법 결과(판정 규칙) [제안]: `methodVersion` = `relation-<kind>-v1`, T 코드 = `REL_<KIND 대문자>`(예 `REL_PRECEDES`·`REL_REQUIRED_STEP`), `severity`·`riskLevel` = 규칙 `severity`. `evidence`에는 비교한 노드·간선 id와 결과만(원천 값 복사 없음, detection-decision.md 11.5)
- 순서: `methodResults`에서 내장 하드룰(내장 목록 순) 다음, `relationRules[]` 저장 순. 현재 `_rank`는 내장 목록 밖 hard id를 `hard_rule_unknown`으로 막으므로(`decision.py:51-53`) 바꿔야 한다(9절)

### 3.2 판정 때 읽는 입력 [합의]

- 현재 detect 입력은 관측 1건: manifest hash `process-input-v1`이 PROCESS 관측 하나만 덮고(`jobs.py:88-89`) `message_observation_refs = '[]'`, `readiness`는 `transaction`·`messages` `unknown`으로 고정 삽입(`jobs.py:91-94`). 첫 판정의 manifest는 바꾸지 않는다 [제안]
- 관계 규칙 또는 `hard:transaction-failed`가 하나라도 선택된 판정만 **시도 시작 때** 같은 원본·같은 `transaction_id` 문자열의 TRANSACTION·PROCESS·MESSAGE 행을 읽는다(그래프 계약 층 A 입력 규칙: 실행마다 최신 리비전 1건 — 단 판정 PROCESS 자신의 실행은 판정에 쓴 관측(`focus`) 그대로, 상한 PROCESS 200·MESSAGE 500). 조회는 기존 인덱스 `data_processes_lookup_idx`·`data_messages_lookup_idx`(`002_core_erd.sql:68,77`). 첫 판정·재판정 모두 같다
- 읽은 행은 evidence에 고정: `evidence.relationInputs = {readAt, transaction, processes:[{observationId, revisionNo}], messages:[{observationId, revisionNo}], truncated}` [제안]. 운영 상태 고정(`pin_operational_state`, `attempt_v04.py:195`)과 같은 시점에 읽고 그 시도 안에서는 다시 읽지 않는다
- `message_observation_refs`에는 넣지 않는다(첫 판정·재판정 모두 `[]`): 트리거 `erd_v03_validate_manifest`(`004_erd_v03_contracts.sql:181-192`)가 snake_case 키, 판정 PROCESS와 같은 `process_id`, 검증된 FK `data_messages.process_observation_id`를 요구하는데 수집이 FK를 채우지 않아 관련 MESSAGE는 거부된다
- 그래프 구성(재시도 묶음·`ambiguous`·`hands_off` 결과)은 process-graph.md 층 A 규칙을 그대로 쓴다. Detector용 Python 구현은 같은 공용 벡터(`process-graph-id-vectors.json`)로 TS와 같은 결과를 확인한다
- 의존: `precedes` 검증(문자열 일치)과 `hands_off` 결과만. **`contains` 검증 상태에는 의존하지 않음**(수집이 FK를 채우지 않아 대부분 `not_checked`) [합의]
- 없음 판정은 그래프 계약 함수 `graph_value_absent`(`DEPEND_PROCESS_ID`의 `NONE` = 시작 단계 포함). Detector `source_absent`(`method_results.py:27-29`)와 바꿔 쓰지 않는다 [합의]. 키 값 결측은 3.1 "키 값이 없을 때"
- 알려진 한계: manifest는 관련 행을 고정하지 않으므로 같은 작업의 재시도 시도(`max_attempts 3`, `jobs.py:118`)가 다른 행을 읽을 수 있다. 최종 판정 행의 evidence `relationInputs`가 그 판정의 입력 기록이다 [제안]

### 3.3 실행 단계 [제안]

- 용어는 둘만 쓴다: **수집 미완료 보류**(relationNotes, 과제 없음)와 **방법 결과 보류**(데이터 문제·입력 상한, 과제로 만들어 `not_ready`). 3.3·5·6·7·9절 공통
- 관계 규칙·`hard:transaction-failed`의 입력 평가와 "수집 미완료 보류·적용 안 함·`deferred_to_transaction`" 분류는 `execute_methods` **전에**, 시도 시작 때 읽은 입력으로 끝낸다
- 결과가 **N·T·데이터 문제·입력 상한**인 판정 규칙만 과제(`MethodTask`, lane `rule`)로 만든다. 그 밖의 규칙(수집 미완료 보류·적용 안 함·정보 기록)은 과제를 만들지 않고 relationNotes에만 둔다
- 근거: `execute_methods`는 과제마다 결과 1개를 돌려주고 끝나지 않은 과제는 `timeout`으로 채운다(`method_runner.py:102-123`). 상태는 닫힌 집합 `STATUSES`(`method_results.py:10`)라 수집 미완료 보류를 둘 자리가 없다. 중간 기록도 과제 결과 목록으로 `decide`를 부른다(`attempt_v04.py:151-152`) — 과제 단계에서 빼야 중간·최종 판정의 n이 같다

## 4. 소프트룰 `scope` [합의 — Trainer·Detector 정정 11/12]

- 생긴 이유: 임계값 기반 규약은 소프트룰 후보로 추천한다는 사용자 결정(1절)에 따라 Trainer가 단계(pattern_step)별 `elapsed_ms` timeout 후보를 제안했고, 현재 소프트룰은 필드 전역이라 단계별 적용 범위가 필요해졌다
- 형식: `softRules[].scope` 선택, `{processId(필수), adapterType?, processHubId?}` + 표시용 `interfaceId`. 없으면 전역(현행)
- 대조: 판정 PROCESS의 `process_id`·`adapter_type`·`process_hub_id`(`002_core_erd.sql:63-64`)와 **원래 값 정확 일치**, 대조 값 결측이면 적용 안 함. 결측 판단 함수는 `graph_value_absent` [제안]
- **scope가 맞는 켜진 소프트룰만 실행·n에 포함**. 맞지 않으면 그 판정 `methodResults`에서 제외(n 부풀림 방지). 현재는 켜진 소프트룰 전부를 실행(`attempt_v04.py:128-137`)
- 저장 검사(API) [제안 — 검사 위치는 `detection_methods_empty`와 같이 **모든 저장 경로**: 룰 세트 저장(되돌리기 포함)·모델 `retire`·활성 집합 `replace`·`restore`, detection-decision.md 7.4]
  - **scope 없는 방법이 1개 이상**: 켜진 내장 하드룰(`hard:transaction-failed` 제외), scope 없는 켜진 소프트룰, 활성 모델 중 하나. 관계 규칙은 항상 단계·인터페이스에 묶이므로 세지 않음. `hard:transaction-failed`는 첫 판정에서 거의 항상 수집 미완료 보류라 n에서 빠지므로, 그것뿐이면 모든 첫 판정이 `detection_methods_empty`로 실패한다 — 그래서 세지 않음. 이 검사가 빠지면 Detector는 그 판정 시도를 실패로 끝낸다(`decision.py:22-23`, fail-closed 현행대로)
  - **`message_handoff`에 짝 `precedes`가 켜져 있지 않으면 저장은 허용하고 경고**(경고 코드 `relation_handoff_without_precedes`) [제안, WebUI·Trainer 수용]. 짝 = 같은 `from`·`to`이면서 `scope`가 handoff의 `scope`와 같거나 `null`(`interfaceId`는 비교에서 뺌 — 2절·8절과 같은 기준). 응답 위치: `PUT /api/rule-set`·`POST /api/rule-set/preview` 응답 최상위 `warnings: [{code, ruleId, message}]` [제안]. 경고 문장(WebUI가 그대로 씀): "짝 선행 규칙이 없어 선행이 다르면 인계를 확인하지 않습니다." 근거: 짝 `precedes`가 후보 기준 미달이면 그 선행 관계는 규약이 아닐 가능성이 크고 억지로 켜면 오탐이 생김. 결과: 선행이 다른 경로에서는 인계를 보지 않음(의도된 동작). 대상은 룰 세트 저장(되돌리기 포함)만 — 모델 `retire`·활성 집합 경로는 관계 규칙을 바꾸지 않아 대상 아님
- 시도 시작 검사 `ensure_executable`(`operational_state.py:124-125`)은 scope와 무관하게 켜진 방법 전체를 센다. 판정별 개수는 `decide`(`decision.py:113`)가 다시 검사
- k가 모두 1(U4)이라 지금은 문제없다. U4를 풀면 scope 선택으로 판정마다 n이 k보다 작아져 `ensure_executable`(`decision.py:27-28`)이 시도를 실패시킬 수 있다 — 그때 다시 정함
- 소프트룰 상한 32(`server/worker/methods/registry.py:43`, `method-catalog.json:31`)는 **상향** [확정 11/12 사용자], 값은 Detector 성능 확인 뒤. 구현 때 확인: 규칙 수별 시도 처리 시간 — 룰은 슬롯 1개에서 차례로 실행(`method_runner.py:40-41,88-89`) — 와 관계 입력 읽기 시간(같은 거래 행 조회가 시도 기한 `deadlineMs` 기본 3000(`policy.py:15`) 안에서 빠짐)

## 5. 보류 — 수집 미완료 보류와 방법 결과 보류

- [확정 11/12 사용자] **수집 미완료 보류만 예외로 `evidence.relationNotes`**에 넣는다(방법 결과 아님, 종합 영향 없음). 화면 표시는 **"관계 확인 보류"**
- [확정 11/12 사용자(3·4차)] 수집 미완료 보류 판정은 **거래 행 도착 때 1회 재판정**(6절). 탐색 기간 24시간 안에 거래 행이 오지 않으면 보류로 끝(기준점 = 보류 판정 `decided_at`은 [제안])
- [확정 11/12 사용자] 그 밖의 보류는 **방법 결과**로 남긴다(이 문서 용어로는 방법 결과 보류) → 판단 불가(hard 대분류 `undetermined`, `decision.py:73-74`)
- [제안] 이 문서는 이것을 **방법 결과 보류**(데이터 문제·입력 상한)라 부르고 판정 규칙(관계 규칙 판정 kind 셋, `hard:transaction-failed`)에만 둔다. 입력 상한 초과(그래프 `truncated`·`not_loaded`)는 행이 더 오기를 기다리는 것이 아니라 다시 해도 같은 결과이므로 수집 미완료가 아니라고 해석한다
- [제안] "적용 안 함"은 보류가 아니며 scope 불일치처럼 그 판정에서 뺀다(relationNotes `not_applicable`)
- 이 결정의 결과(위험): 다른 방법이 모두 N이면 relationNotes에 수집 미완료 보류가 남아도 **종합은 정상**이 된다. 화면이 relationNotes를 함께 보여 주지 않으면 보류가 조용히 정상으로 보인다. 또 6.2 원칙상 보류가 N으로 풀리는 판정은 재판정하지 않으므로 옛 판정에 "관계 확인 보류"가 그대로 남는다(10절)
- 기록 모양 [제안]: relationNotes 항목 `{ruleId, kind, result, reason, waitingFor}`(`hard:transaction-failed`는 `kind: "transaction"`). 수집 미완료 보류·적용 안 함·정보 기록 규칙은 `methodResults`·n에서 뺀다(3.3). 방법 결과 보류는 `status: not_ready`, `anomalous: null`, 기존 닫힌 원인 집합 `CAUSES`(`method_results.py:12-13`)의 코드 + `evidence: [{"reason": "<세부 사유>"}]`(ML 미판정과 같은 모양, `attempt_v04.py:52-53`). **새 cause는 만들지 않는다**
- 아래 관계 규칙의 상황별 분류는 모두 [제안](`hard:transaction-failed`는 7절)

| 상황(그래프 결과) | 구분 | 기록 |
|---|---|---|
| 선행 단계 행 없음(`missing` `not_logged`), 1차 | 수집 미완료 보류 | relationNotes `held` / `collection_incomplete`, `waitingFor: {processId}`. 거래 행 도착 뒤에도 없으면 T |
| 후행 IN 없음(`no_in_message`)·선행 OUT 없음(`no_out_message`) | 수집 미완료 보류 | relationNotes `held` / `collection_incomplete`, `waitingFor: {processId, direction}`. 거래 행 도착 때 다시 평가, 그래도 없으면 보류로 끝(메시지가 거래 행보다 먼저 기록되는지 [확인 필요]) |
| `required_step`, 첫 판정 때 거래 행 없음 | 정보 기록 | relationNotes `deferred_to_transaction` |
| `message_handoff`인데 from→to `precedes` 간선 없음(다른 값·`NONE`) | 적용 안 함 | relationNotes `not_applicable` / `precedes_absent`(위반은 `precedes` 규칙 몫) |
| 0채움 2자리 거래(`excluded_transaction_suffix`) | 적용 안 함 | relationNotes `not_applicable` / `excluded_transaction_suffix`(규약 확인 전 기준선 제외 — 판정을 확정할 수 없으나 데이터 문제가 아님) |
| PROCESS·MESSAGE 상한 초과(`truncated`, `missing` `not_loaded`, `messages_truncated`) | 방법 결과 보류(입력 상한) | `capacity` + `graph_truncated`(detection-decision.md 11.6의 `capacity` 뜻을 "입력 상한 초과"까지 넓힘 — 같이 고칠 것). 그래프 `truncated=true`이면 간선이 `verified`여도 N을 내지 않음 [제안]. 상한 판단은 evidence 사유가 아니라 Detector가 읽은 건수(`relationInputs.truncated`)로 함 [제안] |
| 메시지 후보 여럿(`multiple_messages`) | 방법 결과 보류(데이터 문제) | `input_invalid` + `message_link_ambiguous` |
| `precedes` 간선 모호(`ambiguous`, 선행 없음이면서 후행 묶음 모호 포함) | 방법 결과 보류(데이터 문제) | `input_invalid` + `precedes_ambiguous` |
| 거래 행 재시도 모호(가장 큰 재시도 번호 실행 여럿, 재시도 번호 없음(NULL)·실행 id 없음(NULL) 행 섞임, `required_step`·7절) | 방법 결과 보류(데이터 문제) | `input_invalid` + `transaction_row_ambiguous` |
| MESSAGE_ID 없음(`missing_message_id`) | 방법 결과 보류(데이터 문제) | `input_missing` + `message_id_missing` |
| 판정 관측 `DEPEND_PROCESS_ID`가 NULL·`""`·`\N` | 방법 결과 보류(데이터 문제) | `input_missing` + `depend_process_id_missing` |
| 판정 관측 `transaction_id` 없음(`unkeyed`) | 방법 결과 보류(데이터 문제) | `input_missing` + `transaction_id_missing` |

- `interface_pattern_missing`(조율 회신의 사유 후보)은 규칙을 processId·거래 행으로 고르므로 쓰지 않는다 [제안]

## 6. 재판정 — 거래 행 도착 계기, 1회

- 결정 [확정 11/12 사용자(3·4차)]: 보류된 판정을 1회 다시 판정. 계기는 **거래 행 도착**이고 관계 규칙과 `hard:transaction-failed`가 같은 계기를 쓴다. 최소 지연 30초, 탐색 기간 24시간(위 추천안 문안), 그 안에 거래 행이 오지 않으면 보류로 끝. 10분 시간창과 "선행 PROCESS 도착" 계기는 없앤다. 거래 완료 시점 최종 판정에 마지막 단계 누락도 포함, 평가 위치는 Detector가 정함(추천안 문안). 24시간의 기준점(보류 판정 `decided_at`)과 고른 평가 위치(3.1)를 포함해 아래 방식은 모두 [제안]
- 폐기된 시간창 근거(참고): 후행 − 선행 END_TIME 대리 지표로 수 초 수준(실측 아님, Git 밖 측정 기록)

### 6.1 탐색 작업 [제안]

- **Detector 소유 별도 주기 작업 1개**(판정 시도 밖, 층 C 사건 묶음과 같은 형태), 주기 30초, 잠금 **81742**(DB 세션 배정 11/12, 코드 사용처 아직 없음 — 순서는 81742 → 81740만, 81741과 같은 트랜잭션에서 잡지 않음). 수집 경로에서 거는 계기는 쓰지 않는다 — 운영 입력 경로(`server/worker/collector/input_store.py:136-142`)·데모(`server/worker/demo/runtime.py:242-244`)는 `ingest_observation`의 `detection_eligible` 경로를 거치지 않고 직접 enqueue하고, 보류 판정이 커밋되기 전에 거래 행이 도착하는 경합이 있으며, Collector 소유 경계에 걸린다
- 출발점 (a) **새 TRANSACTION 관측**: `data_observations(source_id, persisted_at)` 인덱스(`002_core_erd.sql:51`)로 `(persisted_at, id)` 영속 커서를 두고 마지막 60초를 겹쳐 다시 읽는다. 거래 행 `persisted_at` + **30초** 뒤에 처리(같은 거래의 늦은 MESSAGE를 함께 읽기 위해). 30초 최소 지연은 (a)·(b) 모두에 적용한다((b) 아래). "지금 시각"과 `persisted_at` 비교는 둘 다 DB 시계로 한다(`persisted_at`은 수집 때 `clock_timestamp()`, 비교는 같은 쿼리의 `clock_timestamp()`) [제안] — 앱 시계와 섞으면 (b)가 미룬 판정을 (a)가 이미 지나친 틈이 생길 수 있음
- 새 거래 행마다: 같은 원본·같은 `transaction_id`의 PROCESS 관측(`data_processes_lookup_idx`, `002_core_erd.sql:68`)과 그 관측별 최신 판정(`018_decision_observation_index.sql:11`)을 찾고, 그중 보류 판정(관계 규칙 수집 미완료 보류, `hard:transaction-failed` `transaction_pending`)과 `required_step` 적용 대상(그 거래의 `deferred_to_transaction` 판정 중 3.1 붙이는 판정 — 가장 늦게 끝난 PROCESS의 판정)을 6.2 사전 검사에 넘긴다. 대상 판정은 거래 행 `persisted_at` ≤ 그 판정 `decided_at` + 24시간인 것만
  - 한계: 가장 늦게 끝난 PROCESS에 v04 판정이 없으면(작업 재시도 3회 모두 실패(`jobs.py:118`), 또는 v03 경로 판정) `deferred_to_transaction`이 없어 붙일 판정이 없고, 그 거래의 `required_step` 위반은 기록되지 않는다(다른 PROCESS 판정으로 옮기지 않음). 구조 전제(3.1)가 깨져 거래 행 뒤에 PROCESS가 도착하면 그 PROCESS의 첫 판정이 거래 행을 보고 바로 평가하므로(3.1) 재판정된 판정과 그 첫 판정, 두 판정이 붙는 곳이 될 수 있다
- 출발점 (b) **판정 이벤트 커서**: 거래 행보다 늦게 커밋된 보류 판정(거래 행이 이미 있었는데 판정 시도가 그 전에 입력을 읽은 경우)을 놓치지 않기 위해 `run_decision_events.id`(`event_kind='final'`, `decision_id` 조인, `006_service_transition.sql:23-26`, `012_decision_interims.sql:178-185`)로 새 최종 판정을 읽는다. 대상: ① 보류 판정인데 거래 행이 이미 있는 것 ② `deferred_to_transaction`이 있고, 그 거래 행이 이미 있으며, 재판정 manifest가 없는 판정(가장 늦게 끝난 PROCESS의 첫 판정이 거래 행 처리보다 늦게 커밋됐는데 그 시도가 거래 행을 보기 전에 입력을 읽은 경우). 둘 다 (a)와 같이 6.2로 넘기고, 24시간 기준은 그 판정의 `decided_at`. 단 **지금 시각 ≥ 거래 행 `persisted_at` + 30초**인 대상만 넘기고, 아직이면 넘기지 않고 (a)에 맡긴다((a)가 30초 뒤 그 거래 행을 처리할 때 이 판정은 이미 커밋돼 있어 판정 조회에 잡힌다). 이유: 30초 지연의 목적은 같은 거래의 늦은 MESSAGE를 함께 읽는 것이고, (b)가 먼저 재판정하면 1회 키(6.3)가 그 전에 쓰여 늦은 MESSAGE를 반영할 기회가 없어진다. 006 트리거가 `pg_advisory_xact_lock(81736,0)` 아래에서 번호를 붙이므로(`006_service_transition.sql:31-38`, DB 세션 확인) 번호가 커밋 순서와 같다. 영속 커서(직전 실행의 마지막 id)를 두고 겹쳐 읽는다
- 판정 행 `decision_committed_at`(`002_core_erd.sql:224` 인덱스)은 값을 쓰는 곳이 없어(v04 INSERT `attempt_v04.py:233-240`에 없음, `operations-ui.md:52` "미사용") 쓰지 않는다
- (a)·(b)를 겹쳐 읽어 같은 작업을 두 번 넣으려 해도 `UNIQUE(source_id,content_hash)`(`004_erd_v03_contracts.sql:52`)가 1회를 보장한다. 커서 저장 위치는 DB 세션과 확정(10절). 커밋된 행만 읽으므로 Collector 트랜잭션에 부담이 없다
- 장애 때 부하 대비: 주기당 상한과 우선순위(예: 오래 기다린 거래 먼저)를 두고 값은 구현 때 측정

### 6.2 거래 단위 사전 검사 — 종합이 바뀌는 판정만 재판정 [제안]

- 거래 행 도착 때 탐색이 거래 단위 검사를 먼저 가볍게 계산한다. ①~④ 공통 기준: **그 시도와 같은 규칙(3.1·5절·7.2)으로 다시 평가해 결과가 N이 아닌 방법 결과(T, 방법 결과 보류 — 데이터 문제·입력 상한)이면 대상**. 방법 결과 보류도 hard 대분류를 판단 불가로 만들어(`decision.py:73-74`) 종합을 바꿀 수 있기 때문이다. N, 적용 안 함, 수집 미완료 보류 그대로(④ 메시지 없음)는 그것만으로는 대상이 아니다
  - ① 거래 실패(`hard:transaction-failed`, 7.2): T, `transaction_row_ambiguous`·`transaction_status_missing` 등
  - ② 필수 단계 전체 집합(`required_step`, 3.1 붙이는 판정): T(필수 단계 없음), `transaction_row_ambiguous`·`graph_truncated`
  - ③ 같은 거래 판정들의 관계 규칙 수집 미완료 보류(선행 행 없음): 거래 행 도착 뒤라 선행 행 없음 = T(3.1 구조 전제). 늦게 온 행으로 `precedes_ambiguous`(데이터 문제)·`truncated`(입력 상한)가 되는 경우 포함
  - ④ 메시지 대기 보류(`no_in_message`·`no_out_message`): 늦게 온 MESSAGE로 `mismatch`(T)·`multiple_messages`·`missing_message_id`(데이터 문제)·`messages_truncated`(입력 상한)가 되는 경우
- 그 결과로 **종합(`composite.grade`·`composite.risk.level`·`composite.basisMethods`(방법 id 집합))이 바뀌는 판정만** 재판정한다. `basisMethods`를 넣는 이유: 이미 이상인 판정에 `hard:rel-*`·`hard:transaction-failed` 근거가 더해지면 재판정해야 "규약 위반" 표시와 근거가 맞음. 결과가 그대로인 판정(성공 거래, 필수 단계가 다 있음, 보류가 N으로 풀림)은 재판정하지 않는다 — 부하를 줄이는 핵심 규칙
- 사전 검사는 거르기용이다. 재판정 시도가 시작할 때 다시 읽은 입력으로 낸 결과(3.2)가 정본이고, 사전 검사와 달라도 그대로 기록한다
- 메시지 대기 보류는 재판정되는 판정 안에서 함께 다시 평가된다. 풀려서 N이거나 여전히 메시지가 없으면 그것만으로는 재판정하지 않는다(④는 N이 아닌 방법 결과 — T·데이터 문제·입력 상한 — 가 될 때)

### 6.3 재판정 작업·1회 보장 [제안]

- 같은 manifest로는 다시 넣을 수 없다: manifest `UNIQUE(source_id,content_hash)`(`004_erd_v03_contracts.sql:52`), 작업 id = `uuid5(manifest)`(`jobs.py:103`)·`run_jobs_v04_detect_idx`(`016_detect_v04_contract.sql:105`), 판정 UNIQUE에 `input_manifest_hash` 포함(`002_core_erd.sql:219`, 구성 NULL은 `016:119`)
- 재판정 manifest hash 입력 = `["process-input-v2-recheck", source, observation, revision_no, "recheck:1"]` — **관련 행을 넣지 않는다**. 1회 키 = `process_observation_id`, 같은 관측이면 hash가 같아 `UNIQUE(source_id, content_hash)`가 DB에서 1회를 강제(두 번째 삽입은 `ON CONFLICT DO NOTHING`). 첫 판정 `process-input-v1` 입력은 그대로
- 재판정 manifest가 이미 있으면 건너뛴다(작업을 만들지 않음). 재판정으로 만든 판정(`evidence.relationRecheck` 있음)은 탐색 대상에서 뺀다. 거래 행의 새 리비전은 다시 계기가 되지 않는다(1회)
- 기존 구조 재사용은 **manifest·`readiness` 범위까지**: `message_observation_refs`는 `[]`(3.2), `transaction_observation_id`는 **NULL** — 이 열은 검증된 P→T FK와 같아야 하는데(`004_erd_v03_contracts.sql:174-180`) 수집기가 채우지 않는다. `contract_version`은 `'v03'`(`004_erd_v03_contracts.sql:50` CHECK, `jobs.py:93`과 같음). `readiness` = `{process:"ready", transaction:"unknown", messages:"unknown", body:"not_collected", relation:{recheck:1, recheckedDecisionId, rows:[{layer, observation_id, revision_no}]}}`(CHECK는 object만 강제, `004_erd_v03_contracts.sql:57`). `rows`는 계기 거래 행(snake_case, 나중에 열로 옮기기 쉽게)
- `readiness.relation` 참조 검사 [제안]: DB는 `readiness`가 객체인지만 표 CHECK로 보고(`004_erd_v03_contracts.sql:57`), manifest 트리거 `erd_v03_validate_manifest`(`004_erd_v03_contracts.sql:167-196`)는 `readiness`를 전혀 읽지 않는다. 그래서 그 안의 거래 행·관련 행 참조는 DB가 존재·source·as-of를 검사하지 않는다. Detector가 재판정 시도 시작 때 직접 확인한다 — 참조 관측이 있는지, `layer='T'`인지, 판정 PROCESS와 같은 source·같은 `transaction_id`인지, `persisted_at` ≤ manifest `input_as_of`인지, `recheckedDecisionId` 판정의 관측이 manifest `process_observation_id`와 같은지. 어긋나면 방법 결과 보류(데이터 문제)가 아니라 **시도 실패**(fail-closed, 저장소 이상)
- **입력 정본은 시도 시작 때 다시 읽은 evidence `relationInputs`**(3.2) — 고정 시점은 계기 순간이 아니라 시도 시작. 운영 상태(룰 세트 등)도 그 시도 시작 때 다시 고정
- 재판정 결과가 또 보류여도 더 하지 않는다. 재판정 작업이 재시도 3회(`jobs.py:118`) 모두 실패하면 **원 판정이 최종으로 남는다**
- 우회 경로 주의: jobId 없는 옛 backlog 연결 경로는 관측에 판정이 하나라도 있으면 새로 만들지 않으므로(`runner.py:50-54`) 재판정은 이 경로를 쓰지 않고 작업을 직접 만든다

### 6.4 결과와 부수 영향

- 새 작업 → 새 판정 행, **옛 판정은 지우지 않음**(append-only). 재판정 evidence에 `relationRecheck: {recheckedDecisionId, transactionObservationId}` [제안]
- 읽는 쪽은 합의된 "최신 판정" 규칙(관측별 `decided_at DESC, id DESC`, `018_decision_observation_index.sql:11` / 층 C는 `execution_id`별 `revision_no DESC NULLS LAST, decided_at DESC, event_sequence DESC`)으로 재판정 행을 고른다 — 같은 관측의 더 늦은 행이므로 두 규칙 모두 재판정 행이 최신 [합의]
- `input_as_of` [제안]: manifest 값 = 재판정 작업 생성 시각(트리거의 `persisted_at ≤ input_as_of` 검사 통과, `004_erd_v03_contracts.sql:172`). 관련 행 고정 시점은 시도 시작(`relationInputs.readAt`). 판정 행 `input_as_of`에 시도 시작을 넣을지(현재는 manifest 값 복사, `attempt_v04.py:238`)는 미정
- 판정 이벤트: 판정 행 INSERT마다 `run_decision_events` 1행(`006_service_transition.sql:31-38`) → 재판정도 알림 이벤트가 하나 더 생긴다. 알림 묶음·중복 표시는 WebUI·API와 미정
- 측정 행: 판정마다 `ops_pipeline_measurements` stage `detector` 1행(`attempt_v04.py:241-243`) → 같은 관측이 두 번 집계된다. 재판정은 측정 행을 넣지 않거나 지연 집계에서 빼는 안 [제안], 확정은 미정
- 실험 뒤 보류 비율 측정은 유지 [확정 11/12 사용자]: 측정 대상은 **재판정 뒤에도 남은** 수집 미완료 보류(관계 규칙이 선택된 판정 중 최신 판정 기준)
- 용량: 장애 규모 재판정 시 GB 단위 증가 가능(DB 계산, 실측 아님, Git 밖 측정 기록). 1GB를 넘는 로컬 재현은 사용자 확인 뒤에만

## 7. 내장 하드룰 `hard:transaction-failed`

- 근거: 사용자 결정 11/12 18:04(WebUI 세션 질문 창). 관계 규칙과 재판정 구조(6절)를 공유하므로 이 문서에 둔다

### 7.1 결정 [확정 11/12 사용자]

- 판정 PROCESS의 **거래 행이 STATUS `F` + 실패 코드**이면 이상. 실패 코드 제외 목록 = `0000`·`S`·결측
- 화면·사건 집계는 **거래 수**로 센다. 판정은 PROCESS마다 생긴다
- 위험도 기본값: 연결 실패 계열 응답 코드 = 위험, 그 밖의 실패 코드 = 주의. 후속으로 룰 설정에서 코드별 편집
- **거래 완료(TRANSACTION 행 도착) 기준으로 최종 판정** — 사용자 설명 "PROCESS가 다 기록돼야 거래가 기록된다"
- 한계: PROCESS 행이 없는 거래는 잡히지 않는다(판정 단위가 PROCESS)

### 7.2 판정 [제안]

- 거래 행 찾기: 같은 source(`data_observations` 조인 — `data_transactions`에 `source_id` 없음, `002_core_erd.sql:52-58`)·같은 `transaction_id` 문자열, 실행마다 최신 리비전
  - 0건 → 수집 미완료 보류(정상 경로 — 거래 행은 PROCESS보다 늦게 옴), relationNotes `held` / `transaction_pending`, `waitingFor: {layer: "T"}`. 재판정은 6절
  - 실행 1건 → 그 행으로 판정
  - 실행 2건 이상(거래 재시도) [제안, 확인 필요]: 그래프 계약은 TRANSACTION 재시도를 정상으로 다룬다(`process-graph.md` 층 A `transaction` attrs `retries[]`). 재시도 번호(`retry_count`)가 가장 큰 실행을 그 거래의 결과로 쓴다. 가장 큰 번호의 실행이 여럿이거나, 재시도 번호 없음(NULL)·실행 id 없음(NULL) 행이 섞이면 방법 결과 보류(데이터 문제) `input_invalid` + `transaction_row_ambiguous`(`retry_count`는 numeric 열이라(`002_core_erd.sql:57`) "숫자 아님"은 NULL뿐)
  - 한계: 1회 재판정은 첫 거래 행 도착에서 끝나므로 뒤에 온 재시도 행은 반영되지 않는다
  - 판정 관측 `transaction_id` 없음 → `input_missing` + `transaction_id_missing`(3.1과 같음)
- 고른 실행: STATUS 결측 → `input_missing` + `transaction_status_missing` / STATUS `F` ∧ 응답 코드가 제외 목록 밖 → **T**(코드 = 고정 `HARD_TRANSACTION_FAILED`, 응답 코드는 `evidence`에, 위험도는 아래) / 그 밖 → N
- 실패 코드 "결측"은 `graph_value_absent`와 같은 기준(ASCII 공백·탭·CR·LF 제거 뒤 `""`·`\N`, NULL)
- `methodVersion` = `transaction-failed-v1`, 내장 목록 `HARD_RULES`(`hard_rules.py:21-35`) 끝에 추가
- 위험도 모양: `{"byCode": {"<연결 실패 코드>": "critical"}, "default": "warning"}` — `byCode`의 키는 거래 행 **응답 코드**. 기본값(연결 실패 코드 = `critical`)은 [제안](연결 실패에 드는 코드 목록은 응답코드 사전 후속, 10절). 현재 내장 `byCode`는 정해진 코드를 전부 담는 형식이라(`server/backend/src/ruleset/ruleSetContract.ts:137` `normalizedByCode`) `default`가 있는 새 모양으로 hash v3에 넣는다. DB는 모양을 검사하지 않고, 버전 3 CHECK는 DB migration 020
- 켜짐: 스냅샷에 없으면 꺼짐(`ruleSetContract.ts:148` `resolveHardRule`)이므로 룰 세트 저장으로 켠다. r0는 바꾸지 않는다
- 재판정: 다시 평가한 결과가 N이 아니면(T, `transaction_row_ambiguous`·`transaction_status_missing` 등 방법 결과 보류) 같은 거래에서 `transaction_pending` 보류였던 판정은 재판정 대상(6.2 ①이 정본). 성공 거래(N)는 재판정하지 않는다

## 8. 룰 세트 hash `contentHashVersion: 3` [합의 — 정규화 세부는 제안]

- `relationRules`·소프트룰 `scope`·`hard:transaction-failed`의 새 위험도 모양을 **같은 버전 3에서 한 번에** 도입. v1·v2 행·정의·기준값은 그대로(hash 동일성은 같은 버전끼리, rule-set.md 리비전 형식 절)
- v1·v2 행의 스냅샷에 `relationRules`·소프트룰 `scope`·`hard:transaction-failed`(새 위험도 모양은 v3 hash에만 있음)가 있으면 Detector는 시도 실패(`relation_rules_require_v3`) [제안]
- 정규 객체: `{"hardRules": [...], "softRules": [...], "relationRules": [...]}`(관계 규칙 없으면 `[]`), 각 배열 id 사전순, 직렬화는 v1·v2와 같음(`operational_state.py:59-60`)
- `hardRules` 항목: v2와 같음 `{id, enabled, severity}`(`operational_state.py:38-40`). `severity`는 단일 문자열, `{byCode}`, 또는 `{byCode, default}`(7.2)
- 빈 scope 표기는 하나: 두 배열 모두 **`scope` 키는 항상 두고 전역·없음이면 `null`**, 있으면 판정에 쓰는 키 중 값 있는 키만
- `softRules` 항목: `{id, field, operator, threshold, severity, enabled, scope}`, scope 키 = `processId`·`adapterType`·`processHubId`(`interfaceId` 제외)
- `relationRules` 항목: `{id, kind, scope, from, to, match, severity, enabled}`(정보 표시 규칙 포함). scope 키 = `precedes`·`message_handoff`·`allowed_branch`는 `adapterType`(`interfaceId` 제외 — 값 있는 키가 interfaceId뿐이면 `null`), `required_step`은 `interfaceId`(판정 키, 2절). `source` 제외. `from`은 `{processId}`, `to`·`match`는 없으면 null 명시
- `interfaceId` 제외 이유: 판정 선택에 쓰지 않는 표시·출처 값이라 판정 의미가 같으면 hash가 같아야 하고, 소프트룰 scope의 interfaceId 제외와 같은 기준. `required_step`만 판정 키라 넣는다
- 저장소: `content_hash_version` CHECK가 `IN (1,2)`(`016_detect_v04_contract.sql:27`)라 DB migration 필요. 열 DEFAULT(현재 2, `016:28`)를 3으로 바꿀지는 DB 결정. Detector는 현재 1·2 외 버전을 시도 실패로 처리(`operational_state.py:50-51`)하므로 **Detector v3 지원이 API v3 저장보다 먼저** 배포돼야 한다
- 시험 기준값(v3 정규 객체·SHA-256)은 구현 때 API·Detector가 같이 고정(아직 계산하지 않음)

## 9. 구현 시 바뀌는 곳 (이 초안에서는 바꾸지 않음)

| 파일 | 함수·위치 | 변경 |
|---|---|---|
| `operational_state.py` | `rule_set_content_hash`·`_canonical_soft_rule`(:43-60) | 버전 3 정규 객체(scope·relationRules), v1·v2에 새 키 있으면 거부 |
| 〃 | `_canonical_hard_rule`(:38-40) | 지금은 `version == 2`에서만 `severity`를 넣음 → v3에서도 넣도록 |
| 〃 | `_soft_rules`(:74-84) | `scope` 형식 검증(키 집합·문자열 값, 어긋나면 fail-closed) |
| 〃 | `PinnedState`·`pin_operational_state`(:28-35, :103-126) | `relationRules` 읽기·kind·`from`/`to` 검증(fail-closed)·켜진 방법 개수에 판정 kind 셋만 포함 |
| `hard_rules.py` | `HARD_RULES`·`hard_rule_runners`·`read_hard_rule_snapshot`(:21-35, :192-199, :70-91) | `hard:transaction-failed` 추가(`{byCode, default}` 위험도 읽기). 관계 규칙 실행기는 별도 모듈(신규 `relation_rules.py` 제안) |
| 〃 | `HardRuleSelection`(:41-48) | 지금은 상태 규칙 단계만 담음 → `hard:transaction-failed`의 `{byCode, default}` 위험도도 담도록 |
| `decision.py` | `_rank`(:46-53)·`decide`(:110-133) | `hard:rel-*`을 내장 목록 뒤 **저장 순**으로 정렬. `hard:rel-*`에 상수 `(0, len(HARD_RULES))`를 주면 안정 정렬과 과제 순서 결과(`method_runner.py:113,123`) 덕분에 저장 순서가 유지됨 [제안] — `decide`·`build_interim_payload`(`evidence_v4.py:104-116`)에 인자 추가 불필요 |
| `evidence_v4.py` | `_check_hard_rules`(:50-57) | 대조 집합 = 켜진 내장 하드룰(`hard:transaction-failed`가 수집 미완료 보류면 제외) + **선택된 판정 규칙(관계 규칙 판정 kind 셋) 중 과제로 만든 것**(수집 미완료 보류·적용 안 함·`deferred_to_transaction` 제외, 3.3). 비교는 순서 있는 튜플이라(:52-54) 내장 목록 순 다음 저장 순까지 같아야 함. 판정마다 달라지는 대조 집합을 `build_evidence`에 넘기는 방법은 구현 때 정함. 어긋나면 `hard_rule_selection_mismatch`(:54-57)로 시도 실패. 위험도 일치 검사(:53-56)는 지금 `hard:process-status`만 보므로 `hard:transaction-failed`(응답 코드 → `byCode`/`default`)로 넓힘 |
| 〃 | `_rule_entry`(:34-47)·`build_evidence`(:60-101) | v3 투영 `rules[]`·`ruleSeverities`에 `hard:rel-*`을 넣을지: **넣지 않음** [제안](v3 판독기는 내장·소프트룰만 앎, 근거는 `methodResults`에 있음). 영향: LLM 설명 입력 `savedEvidence.rules`(`server/backend/src/routes/jobRoutes.ts:261`)에 관계 규칙 위반이 빠지고 `decisionCode`만 `REL_*`가 됨, 판단 불가일 때 `server/frontend/src/features/analysis/components/DecisionDiagnostics.tsx:35`가 "세부 사유 없음"을 보임. `relationNotes`·`relationInputs`·`relationRecheck` 기록 |
| `attempt_v04.py` | `_tasks`(:113-137)·`run_attempt`(:189-245) | soft scope 선택, 관계 규칙·거래 행 입력 읽기·사전 분류(3.3) 뒤 판정 과제만 추가, 첫 판정 시도에 거래 행이 이미 있으면 거래 단위 검사 바로 평가(3.1), `required_step` 붙임 대상 판정, 재판정 시도 시작 때 `readiness.relation` 참조 검사(존재·`layer`·source·`transaction_id`·as-of·`recheckedDecisionId` 관측, 어긋나면 시도 실패, 6.3), relationNotes 전달, 재판정 측정 행 처리(6.4). `runner.py:69-70`의 `fixed`는 manifest `readiness`를 읽지 않으므로 `readiness.relation`을 읽어 `relationRecheck`를 기록하도록 바꿈 |
| `method_runner.py` | — | **변경 없음**(3.3 방식이면 과제 목록만 달라짐) |
| `soft_rules.py` | — | 평가 자체는 그대로(scope 선택은 `_tasks`) |
| `jobs.py` | `enqueue_observation`(:68-123) 옆 | 재판정 작업 생성 함수(`process-input-v2-recheck` manifest·`readiness.relation`, 6.3) |
| `server/worker/detector/` 신규 | 재판정 탐색 주기 작업(6.1 거래 관측 커서·판정 이벤트 커서, 6.2 사전 검사) | 실행 위치(Detector 루프 `runner.py:154` `run` 안 / 별도 프로세스)는 구현 때 결정 |
| `server/worker/methods/registry.py`·`contracts/method-catalog.json` | `maxRules`(:43 / :31), 하드룰 목록 | 상향값(성능 확인 뒤, 4절), `hard:transaction-failed` 항목 |
| `backend/src/ruleset/ruleSetContract.ts` | `CONTENT_HASH_VERSION`(:11)·`contentHash`(:87)·`normalizedByCode`(:137)·`planRuleSetChange`(:261, :319) | v3 hash, relationRules·scope 검증(`required_step`은 `to` null·`scope.interfaceId` 필수), kind 422, scope 없는 방법 ≥ 1 검사(`hard:transaction-failed` 제외, 모델 retire·활성 집합 경로 포함), 경고 `relation_handoff_without_precedes`(응답 `warnings[]`), `{byCode, default}` 위험도, 소프트룰 상한 |
| `infra/migrations/` | 신규 | `content_hash_version` 3 허용(DEFAULT는 DB 결정, CHECK는 migration 020), 탐색 커서 저장, lock 81742 기록 |
| `contracts/rule-set.md` | 원칙(:33)·식별자·리비전 형식·저장 검증 | 1·2·4·7·8절 반영 |
| `contracts/detection-decision.md` | 4.1·4.2·5.2·7.4·11.4·11.5·11.6·11.7 | 관계 규칙 하드룰, `hard:transaction-failed`, scope 선택, relationNotes·relationInputs·relationRecheck 키, 사유 표기, 소프트룰 상한. 11.6 `capacity` 뜻 확장(유지): 슬롯 부족(일시적)과 그래프 상한(다시 해도 같음)이 한 코드로 묶임 — 세부 사유 `graph_truncated`로만 구분. 화면 `server/frontend/src/features/analysis/components/MethodResults.tsx:52-54`는 cause 이름만 보이고 세부 사유를 숨기므로 `graph_truncated`를 함께 보이도록 WebUI에 요청 |
| `contracts/queues.md` | detection 행 | 재판정 작업(새 manifest, payload 형식은 그대로) |

## 10. 남은 결정·요청

| 항목 | 상태·요청 대상 |
|---|---|
| `required_step` 적용 대상 거래를 `scope.interfaceId` = 거래 행 `interface_id`로 고르기 — `required_step`에 한해 interfaceId가 판정 키·hash 입력이 됨(2·8절) | 결정 필요(Trainer·WebUI 합의, 필요하면 사용자). 다른 방법은 규칙에 적용 범위를 정할 정보가 없음 |
| 메시지가 거래 행보다 먼저 기록되는지 | **[확인 필요]**(데이터 접근 세션). 확인되면 거래 행 도착 뒤 메시지 없음도 위반으로 볼지 검토 |
| 보류가 N으로 풀리는 판정은 재판정하지 않음(6.2) → 옛 판정에 "관계 확인 보류"가 남음. 화면에서 거래 행 도착 여부로 "확인 끝"을 보일지 | WebUI 확인 |
| 소프트룰 상한 상향값 | 상향 확정(4절). 값은 Detector 성능 확인 뒤 |
| 탐색 커서 저장(거래 관측 `(persisted_at, id)`, 판정 이벤트 id) | DB 세션 — 저장 위치·권한 |
| (a) 커서 60초 겹침은 수집 트랜잭션이 60초 안에 커밋된다는 가정 | 구현 때 확인(Detector·DB) |
| 관련 행 참조 저장 — 실험 단계는 `readiness.relation`·evidence `relationInputs` | 채택 시 DB 세션 요청: 전용 열(예 `relation_observation_refs`) 또는 `erd_v03_validate_manifest`(`004_erd_v03_contracts.sql:181-192`) 의미 확장 |
| Trainer 후보 형식 정렬 — `relationRules[]` 필드·`required_step` `to: null`·"거래 완료 때 평가" 표시·`evaluatedAt`·건수 이름 `undetermined`. 후보의 지지·위반 건수는 TRAIN 스냅샷 안에서 **TRANSACTION 행이 있는 거래로만** 센다(끝 무렵 미완료 거래가 위반으로 섞이지 않고 Detector 최종 판정 기준과 맞음) | Trainer 확인(`evaluable:false` 해제는 WebUI가 전달함) |
| "연결 실패"에 드는 응답 코드 목록(`hard:transaction-failed` 기본 위험도, 7.2) | 응답코드 사전 후속 |
| 거래 재시도 판단(재시도 번호 최대 실행 사용, 7.2)과 뒤에 온 재시도 행 미반영 한계 | [확인 필요](데이터 접근 세션) |
| 거래 노드 대표 행 — 그래프 계약은 "TRANSACTION 1건(검증된 FK가 있으면 그 행)"(`process-graph.md`)만 정하고 재시도 중 어느 실행이 노드를 대표하는지 정하지 않음 → 화면의 거래 상태와 판정이 다른 행을 쓸 수 있음. Detector는 재시도 번호 최대 실행(7.2) | WebUI 확인(대표 행을 같은 기준으로 맞출지) |
| processId만으로 단계 특정 가능 여부(interface 없이) | 데이터 접근 세션 확인 |
| 공용 벡터에 묶음·모호·`hands_off` 결과 예시 추가(`process-graph-id-vectors.json`) | WebUI 몫(합의됨). Detector Python 구현이 같은 파일로 확인 |
| 화면에서 relationNotes "관계 확인 보류"를 종합 정상과 함께 보이게 할지(5절 위험), 재판정 알림 중복 표시(6.4), `hard:transaction-failed` 거래 수 집계(7.1) | WebUI 확인 |
| 재판정 판정의 `input_as_of`·측정 행 처리(6.4) | Detector·WebUI·API 미정 |
| v04 전환 선행 — Compose 기본 `DETECT_JOB_CONTRACT=v04`(11/13 전환, 코드 기본은 `jobs.py:17` `v03`), 관계 규칙·scope·`hard:transaction-failed`는 v04 판정(evidence v4)에서만 동작. v03 경로는 룰 세트를 읽지 않아 영향 없음 | 사용자·총괄(전환 시점) |

- 해결됨(4차): 재판정 계기("거래 행 도착"으로 변경), 마지막 단계 누락 판정(포함), "시간창이 끝나도 없으면 위반으로 볼지"(거래 행 도착 뒤 행 없음 = 위반, 3.1 구조 전제), `DEPEND_PROCESS_ID` 정의값 위험(같은 이유로 해소 — 실행 기록 여부 확인은 참고로만), 선행 계기 탐색 잠금 번호(탐색 작업 1개, 81742)
- 층 C(거래 간 사건)는 판정 시도 밖 별도 1분 주기 Detector 작업으로 이 문서 범위 밖. advisory lock 81741, 같은 트랜잭션에서 81739·81740과 함께 잡지 않음 [합의]
