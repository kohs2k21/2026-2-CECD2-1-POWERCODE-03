Created: 2026-11-12T19:10:00+09:00
Updated: 2026-11-14T02:20:00+09:00
Author: Claude Code(WebUI builder)
Status: current — 실험 기능(층 C). DB migration 019·Detector `incidents.md` 읽기 규칙 8절에 맞춤

# 사건 보기 읽기 API 계약(층 C, 거래 간 사건)

- 범위: Detector가 묶어 둔 사건(019 표)을 **읽기만** 하는 API와 화면 용어. 묶는 규칙(구성원 자격·기준 키·간격·병합 결정)은 Detector `incidents.md`, 표 형태는 `infra/migrations/019_incidents.sql`이 기준이다
- 저장: 019 표 4개 `run_incident_runs`·`run_incidents`·`run_incident_members`·`run_incident_events`. 모두 INSERT만(문장 단위 불변 트리거), API 역할 `powercode_api`는 **SELECT만**. 끝 시각·건수·해제·흐름은 저장하지 않고 **읽을 때 계산**한다
- 구현: `backend/src/services/incidentReadModel.ts`(순수 함수 `buildIncidentFlows` + 조회), 경로 `backend/src/routes/incidentRoutes.ts`(이상 분석 라우터 안), 프론트 `frontend/src/features/analysis/incidents/`
- **상관관계만**: 사건 소속은 "같은 시간창·같은 기준값으로 묶였다"는 뜻이다. 원인·전파(인과)를 뜻하지 않는다. 화면은 거래 사이 화살표를 그리지 않고, 기준 설명 이름표를 "같은 시간·같은 값으로 묶은 사건"으로 둔다(거래 간 간선을 인과로 표기하지 않는다)

## 1. 읽기 순서 — Detector 8절과 같음

- Detector 8절 "병합(`merge_root`)을 먼저 풀고, 병합 묶음 안 모든 사건의 `continues_from`을 따라간다"와 같은 순서다. 차이는 방어 처리뿐이다: Detector `merge_root`는 병합 고리면 예외(`merge_cycle`)지만, 화면은 고리의 병합을 쓰지 않고 `flags`에 남긴 채 나머지를 보인다(2절)

1. **병합 먼저**: `run_incident_events(kind='merged_into')`의 대상이 유효하면(아래 일관성) 그 사건은 대상의 일부로 읽는다. 대상도 병합돼 있으면 끝까지 따라간다. 끝 사건 = **묶음 뿌리**, 같은 뿌리의 사건들 = **구간(segment)** 1개. 구간 id = 뿌리 사건 id
2. **이어짐**: 구간 안 **모든** 사건의 `continues_from`을 본다(뿌리가 아닌 사건이 앞 구간을 가리킬 수 있음, Detector 8절). 가리킨 사건의 구간 → 이 구간 간선. 같은 구간으로 풀리면 버린다
3. **흐름(flow)** = 이어짐 간선으로 연결된 구간들(방향 무시 연결 요소). 화면은 흐름 1개를 사건 1건으로 보인다. 흐름 id = 가장 이른 구간 id(opened_at, id 순)
4. 구간 시작 = 구간 사건 `opened_at` 최솟값, 구간 끝 = 구간 구성원 `occurred_at` 최댓값(해제 포함, 구성원 없으면 시작과 같음). 흐름 시작·끝 = 구간들의 최소·최대

## 2. 일관성 — Detector 책임, API는 방어

- 같은 원본(`source_id`)·같은 `criteria_version`·같은 `group_key`인 사건끼리만 병합·이어짐이 유효하다. DB는 강제하지 않는다(019 주석)
- 어긋난 연결은 **쓰지 않고** 원인별로 흐름 `flags`에 남긴다. 병합끼리 고리를 이루면(자기 자신 포함) 그 고리의 병합은 모두 쓰지 않는다
- 구성원 판정의 원본(판정 PROCESS 관측 `data_observations.source_id`)이 사건 원본과 다르면 세지도 보이지도 않고 `member_source_mismatch`
- `flags` 고정 어휘(순서 고정, 화면 이름표 `incidentLabels.ts`):
  - `merge_mismatch`(병합 대상의 원본·기준 버전·기준값이 다름) · `merge_cycle`(병합 고리·자기 자신) · `merge_target_missing`(대상 행 없음)
  - `continuation_mismatch`(앞 구간의 원본·기준 버전·기준값이 다름) · `continuation_within_segment`(앞 구간이 같은 구간으로 풀림) · `continuation_target_missing`(대상 행 없음)
  - `continuation_branched`(앞·뒤 구간이 2개 이상인 구간 있음) · `continuation_cycle`(구간 앞뒤가 돌아옴) · `member_source_mismatch`
- **읽기 상한 경계**: 019 FK로 병합·이어짐 대상 행은 늘 있다. 읽기 상한(목록 골격 50,000건, 상세 사슬 10,000건)에 걸려 대상이 읽은 범위 밖이면 `*_target_missing`으로 표시하지 않고 그 흐름을 `truncated:true`로 둔다(그 연결은 쓰지 않음). `*_target_missing`은 상한에 걸리지 않았는데 대상이 없을 때(방어)만

## 3. 건수 — 거래 기준(사용자 11/12)

- 구성원 판정 → 판정 PROCESS 관측(`run_process_decisions.process_observation_id`) → `data_processes.transaction_id` 문자열. **서로 다른 거래 ID 수**가 주 건수, 판정 수는 보조
- **Detector와 다른 점(단위)**: Detector 8절 구성원 수(`active_execution_count`)는 해제되지 않은 구성원의 **실행 수**(실행당 1)다. 화면 주 건수는 사용자 11/12 결정대로 **거래 수**라 다를 수 있다(한 거래의 PROCESS 실행 여러 개 = Detector 여러 건, 화면 1건). 해제·최신 판정 규칙은 같다
- **해제**(Detector 8절과 같음): 구성원 판정의 실행(`execution_id`)에서 최신 판정의 저장된 v4 `composite.grade`가 더는 `anomaly`·`anomaly_sign`이 아니면 그 구성원은 해제. **최신 판정 등급을 모르면**(null: v3 evidence·형식 오류)도 이상·이상 징후가 아니므로 해제이고 `unknownGrade`로 따로 센다(Detector `active_execution_count`가 `ELIGIBLE_GRADES`만 세는 것과 같음. 11/12 이전 이 초안은 등급 모름을 활성으로 셌다). 최신 판정 = `PARTITION BY d.execution_id ORDER BY o.revision_no DESC NULLS LAST, d.decided_at DESC, d.event_sequence DESC`(o = 판정 PROCESS 관측, Detector 8절 합의, Detector `_BATCH_SQL` latest와 같은 정렬). 새 리비전 판정 전에는 옛 판정 유지(해제 아님). 표는 바꾸지 않는다
- 등급 읽기 조건 차이(영향 없음): 화면은 `evidence->'schemaVersion' = '4'::jsonb`(숫자 4)·`composite` 객체일 때만 등급을 읽고(analysisReadModel과 같음), Detector는 `evidence->>'schemaVersion' = '4'`(문자 "4"도 포함)로 읽는다. Detector evidence 작성기는 숫자 4를 쓰므로 지금 데이터에서는 같다
- 거래 ID가 없음(그래프 계약 `graph_value_absent`: NULL 또는 ASCII 공백·탭·CR·LF를 앞뒤에서 지운 값이 `""`·`\N`)인 판정은 거래로 세지 않고 `missingTransactionId`로 따로 센다
- `counts` 필드(정수, 모두 필수): `transactions`(해제되지 않은 구성원이 있는 거래 수) · `releasedTransactions`(구성원 모두 해제된 거래 수) · `decisions`(해제되지 않은 구성원 판정 수) · `releasedDecisions` · `missingTransactionId`(해제되지 않은, 거래 ID 없음 판정 수) · `unknownGrade`(최신 판정 등급을 몰라 해제된 판정 수, `releasedDecisions`에 포함)
- API 기본은 최소 구성원 수로 거르지 않는다. 목록 쿼리 `minTransactions`(아래)로 거를 수 있다
- **최소 구성원 수 — Detector와 다른 점(사용자 11/12 결정)**: Detector `min_members` 기본 2(`shown(active_count)`)는 **해제되지 않은 실행 수** 기준의 읽기 변수다. 화면 기본 `minTransactions=2`는 **해제되지 않은 서로 다른 거래 ID 수**(= `counts.transactions`: 구성원 판정 중 하나라도 실행의 최신 판정이 `anomaly`·`anomaly_sign`인 거래, 거래 ID 없음·다른 원본 판정 제외) 기준이다. 해제 제외·최신 판정 규칙은 Detector 8절과 같고, **다른 점은 단위(거래 vs 실행)뿐**이다. 예: (1) 한 거래가 재시도돼 PROCESS 실행 2건 → Detector 2(보임), 화면 1(기본 숨김). (2) 거래 X·Y 중 Y가 해제 → 화면 1(숨김, Detector도 실행 1이면 숨김). "모두 보기"는 거르지 않는다. 11/12 이전 이 초안은 해제를 포함해 셌다

## 3a. 구성원 시각 출처 — Detector 3절·8절

- 구성원 `occurred_at`은 원천 PROCESS END_TIME → START_TIME → 둘 다 못 쓰면 `decided_at`(판정 시각)이고, 실제로 쓴 출처가 `occurred_basis`(`process_time_raw` | `decided_at`)다. 지금은 모든 원본 `clock_basis.status = unknown`이라 구성원이 거의 모두 `decided_at`이다(Detector 3절 현재 사실)
- 그래서 흐름·구간 시작(`opened_at`)·끝·길이와 구성원 시각은 출처가 `decided_at`이면 **발생 시각이 아니라 판정 시각**이다. API는 출처 수를 돌려주고 화면이 표시한다
- `occurredBasis{processTimeRaw, decidedAt}`(정수, 필수): 흐름·구간의 구성원 판정 수를 출처별로. 시작·끝 시각과 같은 범위(해제·다른 원본 구성원 포함). Detector 8절 "대체 구성원 수"가 `decidedAt`
- 구성원 거래 줄 `occurredBasis`: 그 줄 판정이 모두 `process_time_raw`면 `process_time_raw`, 모두 `decided_at`이면 `decided_at`, 섞이면 `mixed`. 판정마다 `occurredBasis`도 그대로

## 3b. 긴 기준값 — Detector 4.2·8절

- 이스케이프한 값이 512바이트를 넘으면 `group_key` = `<키 이름>:sha256:<원래 값 SHA-256 hex>`이고 원래 값은 어디에도 저장되지 않는다(Detector 4.2). 화면은 해시를 값으로 보이지 않는다
- API는 그 흐름의 구성원 판정 원천 행에서 원래 값을 읽는다: `channel_id` = 판정 PROCESS 행 `data_processes.channel_id`, `response_code` = 거래(TRANSACTION) 값(같은 원본·같은 거래 ID 문자열 T 행을 실행마다 가장 큰 리비전 1건으로 줄여 정확히 1개일 때만, 실행 식별 불가 행이 있으면 없음 — Detector 2.3 `resolve_transaction_response_code`와 같은 규칙). **읽은 값의 SHA-256이 hex와 같을 때만** 쓴다(나중 리비전으로 값이 바뀐 행을 잘못 보이지 않게)
- 비용: 흐름마다 앞 사건 20개 × 사건마다 첫 구성원 3건(`run_incident_members_incident_idx (incident_id, occurred_at)`)만 보고 해시가 맞는 첫 값을 쓴다. 긴 값 흐름이 쪽(목록)·상세에 있을 때만 읽는다
- 못 찾으면 `criteria.value = null`, `criteria.longValue = true` → 화면 "값 생략(긴 값)"(`incidentLabels.ts`)

## 4. 엔드포인트

공통: `Authorization: Bearer` 필수(401), user·admin 모두 읽기(다른 이상 분석 읽기와 같음). 시각은 UTC ISO 마이크로초(`…T01:02:03.000000Z`). 저장소 오류는 `503 {code:"analysis_storage_unavailable"}`(성공·빈 목록으로 바꾸지 않음). 모르는 쿼리 키는 `400 unknown_incident_filter`.

### GET /api/analysis/incidents

- 쿼리: `from`·`to`(ISO, `Z` 또는 `±hh:mm`; 흐름 `[startAt, endAt]`이 `[from, to]`와 겹치면 포함, 경계 포함) · `criteria`(`response_code` | `channel_id`) · `sourceId`(uuid, 없으면 사건이 있는 모든 원본 — 고객 1 = 원본 1) · `minTransactions`(1~1000, 기본 1 = 거르지 않음. 흐름의 해제되지 않은 서로 다른 거래 ID 수(`counts.transactions`와 같은 기준, 3절)가 이 값 이상인 흐름만, `total`도 거른 뒤 수) · `limit`(1~100, 기본 20) · `cursor`(직전 응답 `nextCursor` 그대로)
- 읽기: 원본마다 `WHERE source_id=$1 ORDER BY opened_at DESC, id DESC LIMIT 50,001`(인덱스 `run_incidents_recent_idx (source_id, opened_at DESC, id DESC)`, 019 보완). 골격에 criteria 문서(jsonb)는 읽지 않는다. 원본 지정이 없으면 원본 목록을 그 인덱스 앞 열로 건너뛰며 읽고 원본마다 같은 쿼리(흐름은 원본을 넘지 않음)
- 정렬·쪽 넘김: 흐름 `(startAt, id)` 내림차순 키셋(커서 = 마지막 항목의 `[startAt, id]`)
- 응답 `{observedAt, truncated, total, nextCursor, items[]}`. `total` = 필터에 맞는 흐름 수, `truncated` = 어느 원본이든 골격 읽기 상한(최근 `opened_at` 50,000건)을 넘어 오래된 사건이 흐름 계산에서 빠짐
- 항목: `id`(흐름 id) · `sourceId` · `criteria{kind, value(문자열 | null — 3b), longValue(불리언), version}` · `startAt` · `endAt` · `durationMs` · `counts` · `occurredBasis`(3a) · `chain{length(구간 수), mergedIncidents(병합돼 들어온 사건 수)}` · `flags` · `truncated`(이 흐름이 골격 상한 밖 사건을 가리킴 — 흐름이 일부일 수 있음. 상한 밖 사건이 이 흐름을 가리키는 경우는 알 수 없어 최상위 `truncated`가 기준)
- `criteria.kind`·`value`는 API가 `group_key`(`<키 이름>:<이스케이프한 값>`, 이스케이프 `%`→`%25`·`:`→`%3A`)를 한 번 풀어 준 값이다. 소비자는 `group_key`를 쪼개지 않는다. 모르는 키 이름은 kind 그대로, `:`가 없으면 kind null. 값 부분이 `sha256:<hex 64자>`이면 긴 값(3b): `longValue:true`, value는 원천 행에서 찾은 원래 값 또는 null

### GET /api/analysis/incidents/:id

- `:id` = 아무 사건 id(병합된 사건·뒤 구간 포함). 응답 `id`는 흐름 id, `requestedId`는 요청 id(다르면 화면이 주소를 흐름 id로 바꾼다)
- 읽기: 목록 골격을 쓰지 않고 요청 사건에서 병합(양방향)·이어짐(양방향)을 재귀로 직접 따라가 사슬만 읽는다(`UNION`으로 고리 종료, 상한 10,000건). 그래서 목록 골격 상한(50,000건) 밖의 오래된 사건도 404가 아니다. 사슬이 상한을 넘으면 읽은 만큼으로 흐름을 만들어 **200 + `truncated:true`**(흐름이 일부일 수 있음, 상한 경계 연결은 위 2절대로 표시하지 않음). criteria 문서는 흐름 머리 1건만 읽는다
- 응답: `id` · `requestedId` · `sourceId` · `truncated`(사슬 읽기 상한에 걸림) · `criteria{kind, value, longValue, groupKey, version, timeBasis, document(저장된 criteria 객체 그대로)}` · `startAt` · `endAt` · `durationMs` · `counts` · `occurredBasis` · `flags` · `segments[]`. `groupKey`는 저장 값 그대로(긴 값이면 해시 형태)라 화면에 보이지 않는다
- 구간: `id` · `openedAt` · `endAt` · `durationMs` · `incidentIds`(뿌리 포함) · `mergedIncidentIds` · `continuesFrom`(앞 구간 id 목록, 첫 구간은 빈 목록) · `counts`(그 구간만) · `occurredBasis`(그 구간만)
- 오류: `400 invalid_incident_id`(uuid 아님) · `404 incident_not_found`(그 id의 사건 행이 없을 때만)

### GET /api/analysis/incidents/:id/members

- 흐름 전체 구성원을 **거래별**로. 쿼리 `limit`(1~200, 기본 50, 거래 줄 수) · `cursor`. 흐름은 상세와 같은 사슬 읽기로 정한다
- 비용: 흐름 구성원의 거래 키·첫 시각만으로 이 쪽의 키를 먼저 정하고, 그 키의 판정만 등급·실행별 최신 판정·jsonb로 묶는다(쪽마다 흐름 전체 jsonb_agg 없음)
- 정렬: 거래의 첫 구성원 시각 오름차순, 같으면 키 순. 거래 ID 없음 판정은 판정마다 한 줄(`transactionId` null)
- 응답 `{id, requestedId, total(거래 줄 수), nextCursor, items[]}`
- 줄: `key`(커서용, 화면은 쓰지 않음) · `transactionId` · `interfaceId`(같은 원본·같은 거래 ID TRANSACTION 행의 `interface_id`가 하나뿐일 때만, 그 밖 null) · `firstOccurredAt` · `lastOccurredAt` · `occurredBasis`(3a, `process_time_raw` | `decided_at` | `mixed`) · `released`(구성원 모두 해제) · `decisionCount` · `decisionsTruncated`(50건 넘음) · `decisions[]`
- 판정: `decisionId`(→ 화면 `/analysis/d/:decisionId`, 거래 내부 실행 그래프) · `incidentId`(저장된 사건) · `segmentId` · `occurredAt` · `occurredBasis` · `processId` · `grade`(그 판정의 저장 등급) · `released` · `latestDecisionId` · `latestGrade`(실행별 최신 판정) · `gradeUnknown`(최신 판정 등급을 모름 → 해제)

### 오류 code

`unknown_incident_filter` · `invalid_from` · `invalid_to` · `invalid_period`(from > to) · `invalid_criteria` · `invalid_source_id` · `invalid_min_transactions` · `invalid_limit` · `invalid_cursor` · `invalid_incident_id` → 400, `incident_not_found` → 404, `analysis_storage_unavailable` → 503

## 5. 화면(이상 분석 안)

- 주소: `/analysis/incidents`(목록, 쿼리 `period` = `all`·`24h`·`7d`·`30d`, 기본 `all`(전체, 과거 CSV 재생 데이터는 최근 7일이 비어 있음 — 사용자 11/13), `cursor`, `all=1`) · `/analysis/incidents/:incidentId`(상세). 이상 분석 왼쪽 메뉴 별도 구역 "사건"의 "사건 보기"
- 목록 기본값(사용자 선호 추천, 되돌릴 수 있음): 해제되지 않은 거래 2건 미만 흐름은 숨김(API `minTransactions=2`), "모두 보기"를 켜면 주소 `all=1` + `minTransactions` 없음
- 사용자 승인 문구(11/12, 그대로 둠): 목록 필터 "거래 2건 이상" · 빈 목록 보조 "(거래 2건 이상만 표시)" · 흐름 일부 표시 배지 "일부 구간 제외" · 상세 머리 "등급 모름 판정 N건 · 해제로 셈". "거래 2건 이상"의 거래 = 해제되지 않은 거래(3절)
- 목록: 위 = 시간축(흐름마다 막대 1개, 길이 = 기간, 색 = 기준 종류), 아래 = 표(기간 · 묶은 기준 · 거래 수 · 해제 · 이어짐). 표 줄을 누르면 상세. 이어짐 칸: 구간 2개 이상이면 "N개 구간", 병합·표시 배지, 아무것도 없을 때만 "—"
- 빈 목록: "…사건이 없습니다." 아래에 짧은 한 줄 "v04 판정 전환 전 판정(v3)은 등급이 없어 사건으로 묶이지 않습니다."(Detector 8절·열린 항목 2, 배너 아님)
- 시각 출처(3a): 출처가 원천 시각일 때만 "발생 시각"이라고 부른다. 목록 기간 칸 = 판정 시각 흐름이면 "판정 시각 기준", 섞이면 "일부 판정 시각 기준"을 길이 옆에. 시간축 = 보이는 흐름에 판정 시각이 있으면 "시간축: 판정 시각 기준"/"시간축: 일부 판정 시각 기준". 상세 기간 = 같은 표시 배지. 구성원 표 시각 칸 머리 = "발생 시각"(모두 원천) · "판정 시각"(모두 판정 시각, 줄 표시 없음) · "발생·판정 시각"(섞임, 판정 시각 줄·구간에 표시 배지), 설명 "첫 발생 시각 순"/"첫 판정 시각 순"/"첫 시각 순"
- 긴 기준값(3b): 찾은 원래 값은 기준값 칸에 전체(3줄까지, 넘으면 말줄임 + title), 제목·시간축 이름표는 60자에서 말줄임, 목록 칸은 폭 말줄임 + title. 못 찾으면 "값 생략(긴 값)"
- 상세: 머리(기준·값·기간·거래 수·판정 수·등급 모름이 있으면 그 수 "해제로 셈"), 구간이 2개 이상이면 구간 표, 구성원 표(거래별, 시각 = 구성원 `occurred_at`, 판정 → 판정 상세 링크). 판정 상세의 돌아가기는 사건 화면에서 왔으면 "사건으로"(그 주소), 아니면 "목록"
- 용어: 기준 종류 `response_code` = "응답코드", `channel_id` = "채널". 해제 = "해제", 이어짐 = "N개 구간". 화면 문구는 `features/analysis/incidents/incidentLabels.ts`

## 6. 열린 항목

1. 읽기 비용: 목록은 요청마다 원본별 사건 골격(최대 50,000행, criteria 제외)을 읽어 흐름을 계산하고 페이지 흐름의 구성원만 센다. `minTransactions` > 1이면 기간·기준에 맞는 흐름 전체의 구성원과 그 실행별 최신 판정을 읽어 해제되지 않은 거래 수를 센다. 한 장애 구성원이 대량이면 한 흐름 건수 계산이 무겁다 — 실측 후 흐름 요약 물질화 여부(DB·Detector). 인덱스 `run_incidents(source_id, opened_at DESC, id DESC)`는 DB가 019에 추가(`run_incidents_recent_idx`) — 골격 쿼리가 정렬 없이 이 인덱스로 읽음(작은 DB EXPLAIN 확인). 상세 사슬 읽기의 "병합돼 들어온 사건" 역방향 조회(`run_incident_events.target_incident_id`)는 인덱스가 없어 이벤트 표가 커지면 필요(DB 요청 후보)
2. (닫힘, 사용자 11/12) 최소 구성원 수 필터: 해제 제외는 Detector와 맞춤, 단위는 거래로 둠(3절). 남은 비용은 1번
3. 층 C 그래프 문서(`incident` 노드·`member_of` 간선, process-graph.md 예약 어휘) 응답은 아직 없음(Detector 열린 항목 9)
4. 키가 바뀐 리비전의 실행이 두 사건에 들면 양쪽에서 센다(Detector 열린 항목 5) — 화면 표시 방법
5. (닫힘) `navigation.md`에 사건 보기 주소 반영: 화면 경로·주소 쿼리 표에 실제 주소(`frontend/src/app/routes.ts`)를 적음
