Created: 2026-11-12T21:40:00+09:00
Updated: 2026-11-13T01:50:00+09:00
Author: Claude Code(WebUI builder)
Status: current — 실험 기능(층 B). Trainer `relation_candidates.py`(결과 계약 `relation-candidates-experimental-2`)·Detector `relation-rules.md`에 맞춤

# 규칙 추천(층 B 후보 검토) API·화면 계약

- 범위: Trainer 후보 작업 `recommend_relations`의 결과를 **읽고**, 그 작업을 **요청**하는 API와 탐지 설정 › 룰 설정 › 규칙 추천 화면. 후보 계산(집계·기준·반례)은 Trainer, 규칙 판정 의미는 Detector `relation-rules.md`가 기준이다
- 저장: **새 표 없음**. 결과는 작업 표 `run_jobs.result_summary`(jsonb, Trainer `runner._finish`가 성공 시 기록)에 있다. API는 결과를 고치지 않고 화면에 쓰는 값만 골라 낸다
- 구현: `backend/src/ruleset/ruleCandidates.ts`(해석·조회·요청), 경로는 룰 세트 라우터(`backend/src/routes/ruleSetRoutes.ts`, 같은 64KB JSON 파서·`{code}` 오류 형식·503 처리), 프론트 `frontend/src/features/detection/rules/candidates/`
- 권한: 세 경로 모두 **관리자**(탐지 설정 화면, 작업 결과는 관리자만 읽음 — `GET /api/jobs`와 같은 범위). 토큰 없음 401, user 403 `admin_required`

## 1. DB 준비 상태 — 배포 전 필요(`infra/migrations/021_recommend_relations.sql`)

| 필요 | 없을 때 API |
|---|---|
| `job_kind` enum 값 `recommend_relations` | 요청 503 `relation_job_kind_unavailable`(PG 22P02이고 routine이 `enum_in`이며 형식 이름이 `job_kind`일 때만 — 형식 이름은 `dataType` 필드가 있으면 그 값, 없으면 메시지 안 식별자 `job_kind`. 영어 문장에 기대지 않아 서버 언어 설정과 무관). 읽기는 영향 없음 — `kind::text` 비교라 결과 없음 = `result: null` |
| `run_jobs_v03_contract` CHECK의 training 분기에 `recommend_relations` 추가(017 방식, `queue='training'`·`producer_kind='user'`·`requester_role='admin'`) | 요청 503 `relation_job_contract_unavailable`(PG 23514이고 제약 이름이 `run_jobs_v03_contract`일 때만) |

- 작업 행 모양은 기존 학습 작업과 같은 v03 training(`jobRoutes.ts`): `contract_version 'v03'`, `queue 'training'`, `producer_kind 'user'`, `producer_key` = 요청자, `input_snapshot_id` = 스냅샷, 큐 `powercode_queue.training_jobs`에 `{schemaVersion:1, jobId, enqueuedAt}` 1건
- 작업 요청은 Trainer가 이 kind를 꺼낼 때만 끝까지 돈다: `runner.py` trainer claim 목록(`job_kinds`)과 관리자 전용 목록(`_claim_job`)에 `recommend_relations`가 있어야 한다(현재 `runner.py`에 둘 다 있음). 없으면 행·큐 메시지는 생기지만 대기에서 멈춘다
- 배포 순서: **DB(021) → Trainer → API**. 021 전에는 요청이 503, Trainer 전에는 요청이 대기로 남는다. 021은 legacy 모양(`contract_version` NULL) `recommend_relations` 행을 금지하므로, 그런 행이 있는 DB는 먼저 v03 행으로 바꾸거나 지운 뒤 적용한다(있으면 021 CHECK 검증 실패 → 전체 ROLLBACK, 정리 후 재실행)
- 실패한 요청은 행·큐 메시지를 남기지 않는다(트랜잭션 ROLLBACK)
- 같은 SQLSTATE라도 다른 원인(다른 CHECK·다른 enum·형식 오류)은 503 준비 전 코드로 바꾸지 않고 그대로 저장소 오류로 둔다
- 확인: 통합 시험 `backend/tests/rule-candidates.integration.mjs`는 DB 상태를 먼저 본다. 021 전 DB(001~020)면 enum 없음 → 503, enum만 추가 → 503, CHECK 삭제 모의(시험 DB에서만) → 201 순서. 021 적용 DB(enum 있음·CHECK에 `recommend_relations` 있음)면 503 단계를 건너뛰고 CHECK를 지우지 않은 채 실제 021 CHECK·트리거로 201을 본다. 021 CHECK·트리거 자체는 DB 세션 시험 `infra/tests/erd-v03-recommend-relations.mjs`

## 2. `GET /api/rule-set/candidates`

- 쿼리: `interfaceId`(선택, 1~512자). 다른 키 400 `unknown_candidate_filter`, 형식 오류 400 `invalid_interface_id`, 결과에 없는 인터페이스 404 `rule_candidate_interface_not_found`
- 결과 작업 = 가장 최근 **성공한** `recommend_relations` 작업(`finished_at DESC NULLS LAST, created_at DESC, id DESC`). `latestJob` = 상태와 무관하게 가장 최근 작업(대기·실패를 따로 보이기 위함)
- 응답:

```json
{"latestJob": {"id": "…", "status": "queued", "errorCode": null, "createdAt": "…", "finishedAt": null, "snapshotId": "…"},
 "approval": {"available": false, "reason": "rule_set_hash_v3_pending", "contentHashVersion": 2},
 "result": {
   "job": {"id": "…", "status": "succeeded", …}, "snapshotId": "…", "sourceId": "…",
   "period": {"from": "…", "until": "…", "timeBasis": "persisted_at"}, "generatedAt": "…", "experimental": true,
   "parameters": {"minApplicable": 30, "candidatePercent": 99, "reviewPercent": 95, "maxCounterexamples": 5, "excludeProcessIds": [], "handoffMismatchSuffixPatterns": ["-0[0-9]\\Z"]},
   "interfaces": [{"interfaceId": "IF-A", "transactions": 122, "relationCandidates": 2, "approvable": 1, "thresholdCandidates": 2}],
   "selectedInterfaceId": "IF-A",
   "graph": {"graph": {"layer": "pattern", …}, "nodes": [{"id": "pattern_step:IF-A:P1:HTTP", "kind": "pattern_step", "attrs": {…}}],
             "edges": [{"id": "precedes:…", "kind": "precedes", "from": "…", "to": "…", "evidence": {"status": "candidate", "satisfactionRate": 0.999, "applicable": 1000, …}}]},
   "relationCandidates": [ … 3절 ],
   "thresholdCandidates": [ … 4절 ],
   "thresholdsWithoutInterface": 0, "thresholdLimitations": ["elapsed_ms = END_TIME - START_TIME. …"],
   "totals": {"relationCandidates": 8, "approvable": 4, "thresholdCandidates": 6, "thresholdApprovable": 0}}}
```

- 결과 없음(작업 없음·성공 작업 없음): `result: null`(빈 성공 목록으로 바꾸지 않음)
- 인터페이스 정렬: 승인 가능 후보 수 내림차순 → 거래 수 내림차순 → id. `interfaceId` 생략 시 첫 항목을 고른다(`selectedInterfaceId`)
- `graph`: Trainer 층 B 그래프 중 그 인터페이스의 `pattern_step` 노드(`attrs.interfaceId` 일치)와 양 끝이 그 안에 있는 `precedes`·`hands_off`·`branches_to` 간선. 간선 evidence는 화면용 값(status·satisfactionRate·applicable·satisfied·violated·undetermined·observedTransactions)만. id는 쪼개 읽지 않는다(`process-graph.md`)
- `relationCandidates`: 그 인터페이스(`display.interfaceId`)의 후보. `thresholdCandidates`: `display.interfaceIds`에 그 인터페이스가 있는 후보(여러 인터페이스 공통 단계는 각 인터페이스에 모두 나온다). 인터페이스가 비어 있는 처리 시간 후보는 어느 목록에도 없고 `thresholdsWithoutInterface`로 센다
- 결과 머리 검사: `relationContractVersion`이 `relation-candidates-experimental-2`가 아니면 503 `rule_candidate_contract_unsupported`, 배열·건수 모양이 다르면 503 `rule_candidate_result_invalid`(해석하지 않음)
- 저장소 오류 503 `rule_set_storage_unavailable`(룰 세트 라우터 공통)

## 3. 관계 후보 항목·후보 id

- 후보 id: 관계 `<작업 id>.r<번호>`, 처리 시간 `<작업 id>.t<번호>` — 번호는 그 작업 결과 배열(`candidates`·`thresholdCandidates`)의 위치. 결과는 바뀌지 않으므로 고정. 새 작업이 생겨도 옛 id는 옛 결과를 가리킨다(상세 `isLatest:false`)
- 관계 항목: `candidateId`, `kind`(`required_step`·`precedes`·`message_handoff`), `status`(`candidate`·`review_needed`·`insufficient_evidence`·`not_candidate`), `approvable`, `listedForPairing`, `pairApprovable`(`message_handoff`만, 그 밖 null), `interfaceId`, `scope`, `from`, `to`(`required_step`만 null), `match`, `counts{applicable,satisfied,violated,undetermined}`, `satisfactionRate`, `evaluatedAt`, `patternEdgeId`, `nonStandardProcessId` — 값은 Trainer 그대로, API가 다시 계산하지 않는다
- `allowed_branch`는 후보가 아니라 Trainer `relationNotes`(정보)라 이 목록에 없다

## 4. 처리 시간(이상 징후) 후보 항목

- `candidateId`, `status`(`candidate`·`insufficient_evidence`·`no_variation`·`rejected`), `reason`, `scope{processId, adapterType?}`(Detector 4절 대조 키), `interfaceIds`(표시용), `operator`, `threshold`(JSON number 또는 null, 아래), `displayThreshold`, `unit`, `source`(`data_p95` = 코드가 계산한 기준 표본 p95, LLM 제안 아님), `evidence{n, rows, referenceRows, endTimeMissingReferenceRows, unavailableReferenceRows, p95, min, max, quantileMethod}`, `limitation`(Trainer 한계 문구 한 줄, 없으면 null). 새 Trainer 결과의 `limitationRefs`(내부 경로·식)와 `evidence.note`는 내보내지 않는다(옛·새 모양 모두 읽음)
- `threshold`: Trainer 결과는 방법 카탈로그 `softRule.thresholdSerialization: "fixed-decimal-string"`대로 고정소수 **문자열**(`"704"`, worker `threshold_values.fixed_threshold`)이다. API는 룰 세트 저장 검증과 같은 규칙(40자·DSL 숫자 `-?(0|[1-9]\d*)(\.\d+)?`·유효숫자 15자리·정수 필드 정수·number 왕복 시 같은 문자열, worker `threshold_valid`)을 통과한 문자열만 **number로 바꿔** 낸다 — 왕복 불변이라 값 손실이 없고, `PUT /api/rule-set` 소프트룰 `threshold`(number)와 같은 형식이다. 옛 결과의 finite number도 그대로 읽는다. 규칙에 어긋난 문자열(`"1e3"`·`"+1"`·`"704.0"`·`""`·`"abc"`)·그 밖의 형식은 503 `rule_candidate_result_invalid`. 화면 표시는 `displayThreshold`(문자열)를 쓰고, 프론트 해석은 number·null 외 값을 응답 형식 오류로 본다

## 5. `GET /api/rule-set/candidates/:candidateId`

- id 형식 오류 400 `invalid_candidate_id`, 작업·번호 없음(또는 작업이 성공 아님) 404 `rule_candidate_not_found`, 쿼리 있으면 400 `unknown_candidate_filter`
- 공통 머리: 2절 결과 머리 + `isLatest`(가장 최근 성공 작업인가 — 그 작업 id만 읽고 결과 JSON은 한 번만 읽는다) + `approval`
- 관계 후보 `type: "relation"`:
  - `relation`: 3절 항목 + `violationReasons`·`undeterminedReasons`·`violationsByTransactionStatus`·`fromAdapterTypes`·`bySuffixClass`(Trainer 그대로)
  - `counterexamples[]`: Trainer `counterexampleRefs`(위반 거래 앞 최대 5건, 거래 ID 순) → `{transactionId, reason, transactionStatus, decisionId}`
    - 거래 id = 첫 참조 `transaction:<id>`의 구성요소를 한 번 되돌린 값(`%3A`→`:` 다음 `%25`→`%`, Trainer `transaction_ref`). 형식이 다르거나 본문이 비면 `transactionId: null`(행은 남김). 반례 참조는 다른 필드가 없어 이 경로만 id 구성요소를 읽는다
    - `decisionId`: 결과 원본(`sourceId`)의 같은 거래 PROCESS 판정 중 **판정 위치 단계**(`to.processId`)가 있으면 그 단계, 없으면(필수 단계 누락 등) 아무 단계의 가장 최근 판정(`decided_at DESC, id DESC` — 분석 화면 최신 판정 순서). 거래마다 1건을 따로 고른다(`DISTINCT ON (transaction_id)`, 공용 행 상한 없음 — 판정이 많은 거래가 다른 반례를 밀어내지 않음). 판정이 없으면 null. 화면은 `/analysis/d/:decisionId`(판정 상세 실행 순서)로 잇는다
  - `pairedPrecedes`(`message_handoff`만): Trainer `pairedPrecedesEdgeId`가 있으면 `patternEdgeId`가 그 값인 `precedes` 후보(못 찾으면 null, 다른 기준으로 추측하지 않음). 그 필드가 없거나 null인 결과(옛 결과)만 같은 인터페이스·`from`·`to`·`scope`의 `precedes` 후보. 문자열도 null도 아닌 값이면 짝 없음(null). `{candidateId, status, approvable, counts, satisfactionRate}`, 없으면 null
  - `ruleDraft`: 승인 때 `relationRules[]`에 들어갈 조건(Detector 2절) `{kind, scope, from, to, match, enabled:true, source}`. `scope`는 `required_step`이면 `{interfaceId}`(판정 키), 그 밖은 `{interfaceId(표시·출처, hash 제외), adapterType?}`. `match`는 `message_handoff`만 `{from:"OUT", to:"IN", key:"message_id"}`. id(`hard:rel-<8hex>`)·`severity`는 승인 저장 때 서버·관리자가 정한다
- 처리 시간 후보 `type: "threshold"`: `threshold`(4절) + `ruleDraft` `{field, operator, threshold(number, 4절), unit, enabled:true, scope}` — `scope.interfaceId`는 인터페이스가 하나일 때만(표시용). 제안 임계값이 없으면 `ruleDraft: null`. 소프트룰 `name`·`severity`는 승인 때 정한다

## 6. `POST /api/rule-set/candidates/jobs`(관리자)

- 본문(JSON): `{snapshotId, excludeProcessIds?, handoffMismatchSuffixPatterns?}` — Trainer `parse_input` 키. 다른 키·형식 오류 400 `invalid_candidate_job_request`. 상한: 제외 단계 200개·각 200자, 패턴 10개·각 100자(Trainer와 같음), 중복 불가. 정규식 문법 검사는 Trainer(Python 문법)
- 스냅샷: `data_snapshots` `status='ready'`·`schema_version='process-v1'`가 아니면 409 `snapshot_not_ready`. 원본 id는 스냅샷에서 읽어 입력 `sourceId`로 고정
- 작업 입력 `input_spec` = `{kind:"recommend_relations", snapshotId, sourceId, excludeProcessIds?, handoffMismatchSuffixPatterns?}`
- 멱등: 헤더 `Idempotency-Key`(형식은 룰 세트 저장과 같음, 없으면 새 키). 같은 요청자·키의 작업을 **스냅샷 확인보다 먼저** 찾는다 → 같은 본문 200 같은 작업(그 사이 스냅샷 상태가 바뀌어도), 다른 본문 409 `idempotency_payload_conflict`. 본문 비교 값 `request_hash` = 요청 본문(`kind`·`snapshotId`·선택 키)의 sha256, 스냅샷에서 읽는 `sourceId`는 넣지 않는다. 호환: hash에서 `sourceId`를 빼기 전 구현으로 만든 작업의 `request_hash`는 입력 전체(`kind`·`snapshotId`·`sourceId`·선택 키) sha256이라, 재요청은 새 hash 또는 그 작업 `input_spec.sourceId`로 다시 계산한 옛 hash 중 하나와 같으면 같은 본문 200. 새 작업 201 `{id, status:"queued", kind, inputSpec}`
- DB 준비 전 503(1절)

## 6.1 `GET /api/rule-set/candidates/snapshots`(관리자) — 요청할 수 있는 저장 데이터셋

- 화면 확인창의 선택지. 조건은 6절 작업 요청과 같다: `data_snapshots` `status='ready'`·`schema_version='process-v1'`. 만든 시각 최신순(`created_at DESC, id DESC`) 200개. 쿼리 키가 있으면 400 `unknown_candidate_filter`, 저장소 오류 503 `rule_set_storage_unavailable`
- 응답 `{items: [{id, sourceId, createdAt, readyAt, from, until}]}` — `from`·`until`은 스냅샷 `selection_spec`의 학습 기간(문자열 그대로, 없으면 null), 시각은 UTC 마이크로초 ISO. 한 고객 = 한 원본이라 `sourceId` 필터는 두지 않는다
- 평가 세트 기준 목록 `GET /api/evaluation-sets/snapshots`(evaluations-ui.md)는 모델 버전이 쓴 스냅샷만 보여 여기에 쓰지 않는다(학습이 실패해도 스냅샷은 남을 수 있고, 추천 작업은 모델과 무관). 스냅샷 목록 API가 계약에 없어 WebUI가 정한 형식이다. 새 표·migration 없음
- 경로는 `/candidates/:candidateId`보다 먼저 연결한다(`snapshots`가 후보 id 형식 오류 400으로 읽히지 않게). 구현 `backend/src/ruleset/candidateSnapshots.ts`

## 7. 승인 — 룰 세트 저장, 지금은 꺼짐

- 승인 = 기존 룰 세트 저장 `PUT /api/rule-set`(새 버전, 되돌리기 대상, plan 20261112 2절 보완 2). 관계 후보는 `relationRules[]`, 처리 시간 후보는 `softRules[]` + `scope`
- 이 두 필드는 **룰 세트 hash v3에서만** 저장한다(Detector 8절). API `CONTENT_HASH_VERSION`은 Detector v3 배포 전까지 2이고, 현재 `PUT /api/rule-set`은 두 필드를 모르는 키로 400 `invalid_rule_set_request` 거절한다 → **`approval.available = (CONTENT_HASH_VERSION >= 3)`**, 사유 `rule_set_hash_v3_pending`
- 화면은 승인 단추를 끄고 사유를 그대로 보인다. v3 전환 때 할 일: `parseRuleSetRequest`·`planRuleSetChange`에 `relationRules`·`scope` 검증(Detector 9절 표), 화면 승인 → 미리보기(`POST /api/rule-set/preview`, `warnings[]` 확인창) → 저장 연결
- "승인됨" 표시는 별도 플래그가 아니라 현재 룰 세트에 같은 조건 규칙이 있는지로 판단(plan 2절 보완 2). v3 전에는 룰 세트에 관계 규칙이 없으므로 표시하지 않는다

## 8. 화면 — 탐지 설정 › 룰 설정 › 규칙 추천

- 진입: 룰 설정 이상 징후 규칙 아래 "규칙 추천" 카드(설명 한 줄 + [후보 보기]). 긴 목록은 별도 페이지 → 행 클릭 상세(사용자 11/12 화면 구성 원칙)
- 목록 `/detection/rules/candidates?interface=<id>`: 결과 머리(후보 작업 완료 시각·기준 기간·전체 건수, 결과 작업과 다른 최근 작업이 있으면 그 상태), 인터페이스 선택(주소 `interface`. 인터페이스가 수백 개일 수 있어 검색 선택: 트리거 `<ID> · 승인 가능 N` → 팝오버 검색 입력 `인터페이스 ID 검색`(부분 일치·대소문자 무시) + 목록(ID · 승인 가능 N, 받은 순서), 일치 없음 `일치하는 인터페이스가 없습니다.`)
  - **관계 규칙 후보**: 패턴 그래프(React Flow + dagre, 실행 순서 보기와 같은 상자·노드 폭, 읽기 전용) — 노드 = 단계 ID·어댑터·관측 거래 수, 간선 = `precedes`만, 이름 = 선행 충족률 + 후보 표(종류 · 경로·단계 · 어댑터 · 적용 · 충족 · 위반 · 보류 · 충족률 · 상태 · 승인 가능)
  - **처리 시간(이상 징후) 후보**: 표(단계 · 어댑터 · 제안 임계값(근거 "기준 표본 p95(코드 계산)") · 근거 건수 · END_TIME 없음 · 상태) + Trainer 한계 문구("한계(Trainer) · …"). 문구에 내부 경로(`.py` 또는 `trainer/foo`처럼 소문자·`_` 단어 뒤 `/`; `S/F`·`건/분`은 경로 아님)가 있으면(옛 결과) Trainer 새 결과 `limitation` 원문 전체인 고정 문장 "종료 시각(END_TIME)이 없는 실행은 처리 시간 계산에서 제외됩니다. 늦게 끝난 단계는 잡지만, 끝나지 않고 멈춘 단계는 잡지 못합니다."(`candidateLabels.ts` `END_TIME_LIMITATION`, 근거: Trainer 한계 + 화면에 내부 경로·식별자를 보이지 않는 문구 규칙)로 바꾸고 같은 문장은 한 번만 보인다
- 상세 `/detection/rules/candidates/:candidateId`: 건수·충족률·판정 시점, 사유별 건수, 대표 반례(→ 판정 상세 "실행 순서 보기", 판정 없으면 "판정 없음"), 짝 선행 관계 링크, 승인 시 저장할 조건, [승인](꺼짐 + 사유). 상단 뒤로가기 = 넘겨받은 목록 주소(없으면 룰 설정)
- 용어 근거: 종류 이름 plan 3절 4번, 상태 기준 Trainer `candidate_status`(30건·99%·95%), 건수 이름 "적용·충족·위반·보류"(plan 3절 4번), 판정 시점 Detector 2절 `evaluatedAt`
- `message_handoff`이고 `pairApprovable:false`이면 문장 "짝 선행 규칙이 없어 선행이 다르면 인계를 확인하지 않습니다."(Detector 4절 경고 문장 그대로)
- 충족률 표기: 한 자리 내림(99.96% → "99.9%"), 0과 1 사이 값은 0.1%~99.9%, 정확히 0·1만 "0%"·"100%"
- 화면은 "버전"만 쓴다(리비전 표기 없음)
- **후보 작업 요청**(목록 머리 오른쪽 단추 `후보 작업 요청`): 확인창 `후보 작업 요청` + 설명 "고른 저장 데이터셋으로 관계 규칙·처리 시간 후보를 다시 계산합니다. 운영 룰은 바뀌지 않고, 작업이 끝나면 이 화면에 새 결과가 나옵니다." + 선택 칸 `저장 데이터셋`(6.1절, 확인창을 열 때만 읽음. 이름 `<학습 시작> ~ <학습 끝> · 생성 <만든 시각>`, 지금 보이는 결과의 데이터셋이면 뒤에 ` · 현재 결과`, 기본 = 가장 최근) + [취소]·[요청]. 화면 용어 "저장 데이터셋"은 평가 결과 화면 세트 생성과 같다
  - 요청 본문은 `{snapshotId}`만(제외 단계·인계 접미사 패턴은 보내지 않음 = Trainer 기본값). `Idempotency-Key`는 확인창이 열린 동안 데이터셋마다 같은 값이라 응답을 놓친 재전송은 같은 작업(6절)
  - 접수되면 확인창을 닫고 목록을 다시 읽는다. 진행은 결과 머리의 `최근 작업 <상태> · <시각> · <작업 id 8자>`(작업 상세 `/detection/operations/jobs/:jobId`로 링크)이고, 최근 작업이 대기·실행 중이면 목록을 5초마다 다시 읽는다(공통 진행 중 조회 규칙). 끝나 성공하면 그 결과가 목록이 되고(2절 "가장 최근 성공 작업"), 실패하면 머리에 `실패 · <코드>`가 남고 이전 결과가 그대로 보인다
  - 최근 작업이 대기·실행 중이면 단추를 끄고 옆에 `후보 작업 진행 중`(같은 계산을 겹쳐 쌓지 않게 — 서버는 막지 않는다)
  - 저장 데이터셋이 없으면 "저장 데이터셋이 없습니다. 학습을 요청하면 학습 기간의 저장 데이터셋이 만들어집니다." + 링크 `머신러닝 모델 › 새 학습`(`/detection/models?step=1`, 학습 작업이 `process-v1` 스냅샷을 만든다)이고 [요청]은 꺼진다. 목록 조회 실패 `저장 데이터셋 조회 실패 · <코드>`, 접수 실패 `후보 작업 요청 실패 · <코드>`(확인창 안, 닫지 않음)

## 9. 남은 결정·미검증

- DB: 1절 enum·CHECK는 migration 021. 큰 스냅샷의 결과 JSON 크기·`result_summary` 읽기 비용(목록 조회마다 결과 전체를 읽음) 미측정 — 크면 결과 표(층 B 채택 단계 `run_pattern_*`) 또는 요약 열로 옮김
- 화면에서 후보 작업 요청(스냅샷 선택)을 둘지, 인터페이스가 많을 때 선택 칸 대신 검색 목록을 둘지
- v3 전환 시 승인 저장 연결(7절)과 "승인됨" 판단 기준(같은 조건 = Detector 2절 "같은 규칙의 기준")
- `relationNotes`(허용 분기 정보)를 화면에 보일지
