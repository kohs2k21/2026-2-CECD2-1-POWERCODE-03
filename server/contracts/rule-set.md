Created: 2026-11-02T18:00:00+09:00
Updated: 2026-11-02T18:00:00+09:00
Author: Claude Code (Trainer 세션 builder)
Status: draft

# 룰 세트 계약 v1 (초안, Issue #94)

- 변경 요약(#94): 기존 [detection-config-version.md](detection-config-version.md)(HR·SR·ML 묶음 구성 버전, #74)를 **룰 세트(이 문서) / 모델 버전([model-version.md](model-version.md))** 두 계약으로 분리. 룰은 버전 관리 없이 저장 즉시 운영 적용·리비전 증가, 버전·교체·복원·비교는 모델 버전에만
- 계약 버전: `rule-set-v1`. 근거 결정: 사용자 11/02 결정(룰 설정 버전 없음·저장 즉시 적용, 룰 수정 범위 R1~R5, AI 임계값 추천 화면 설계)·Trainer·Detector 분담 합의 11/02. 절 번호(6·8.2·9)는 총괄 기록 기준. 근거 코드 origin/dev `7f24e03`
- 범위: 하드룰 켜기/끄기 + 소프트룰(등록 필드·조건·임계값·위험도) 한 벌의 현재 상태·저장·변경 요약·변경 이력·되돌리기·동시 편집·식별자·리비전 형식
- 제외(Detector 소유, detection-decision.md(#92)): 하드룰 내장 목록(id·조건·위험도), 소프트룰 실행 의미론(평가 시점·결측 처리), 종합 판정 `detectionPolicy` v2 `k:{hard,soft,ml}`, 이상/이상 징후 등급, evidence v4(판정마다 기록하는 룰 리비전 필드명). 소프트룰 등록 필드 목록은 [method-catalog.md](method-catalog.md) `softRuleFields[]`
- 표기: **[확정]** 사용자 확정(11/02) 또는 코드 사실 / **[제안]** 이 초안이 정한 값(결정 질문은 문서 끝)

## 용어 [확정 11/02, WebUI 경유]

| 식별자 kind | 화면 표시 용어 | 계약 내부 용어 | 탐지 결과 |
|---|---|---|---|
| `hard:*` | **이상 규칙** | 하드룰 | 이상 |
| `soft:*` | **이상 징후 규칙** | 소프트룰 | 이상 징후 |
| `ml:*` | **머신러닝 모델** | ML 방법 / 모델 버전 | 이상 징후 |

- 식별자·API 필드명(`hardRules`·`softRules`)은 내부 용어 그대로. 화면 문구·확인창·오류 안내·카탈로그 `displayName`은 표시 용어 사용. 이 문서 본문은 내부 용어를 쓰되 화면 문구 예시에는 표시 용어를 씀

## 원칙 [확정 11/02]

- **버전 없음, 저장 즉시 적용**: 화면은 현재 운영 중인 룰 상태를 보여 주고 [저장]을 누르면 즉시 운영에 적용. 룰 세트 버전 목록·교체·복원·비교 평가 없음
- **리비전**: 저장마다 정수 리비전 단조 증가(r0 = 시스템 기본값). 되돌리기 = 과거 스냅샷을 **새 저장으로 재적용**(새 리비전, 예 r10 상태로 → r14). 리비전마다 **룰 전체 스냅샷**을 이력에 저장(되돌리기·evidence 추적의 전제)
- **적용 시점**: "저장 이후 새 판정부터 적용". 진행 중 판정은 시작 시점 룰 고정, 판정마다 그 시점 룰 리비전(정수 + 내용 hash)을 evidence에 기록 — Detector 조건(필드명은 detection-decision.md(#92))
- **하드룰**: 시스템 내장 목록(Detector 정의)만, 켜기/끄기만 가능. 조건·위험도 읽기 전용. 끄기는 경고 + 클라이언트 확인 플래그 필수(R1)
- **소프트룰**: 등록 필드 목록에서 고르고 조건·임계값·위험도·이름 입력. 추가·수정·삭제 가능. **대상 필드 변경 불가**(삭제 후 새로 추가, R2). 위험도 참고·주의·위험 모두 선택 가능(R4)
- **실패 시 이전 룰 유지**: 저장이 어떤 단계에서 실패해도 운영 룰·리비전은 바뀌지 않음(트랜잭션 1개)
- **동시 편집**: 요청 `expectedRevision` ≠ 현재 리비전이면 409 거절, 화면은 새로 불러오기 안내
- 단일 룰 세트 [제안]: `ops_system_state`가 단일 행(id=1, `002_core_erd.sql:200-206`)이고 활성 모델 버전도 1개이므로 룰 세트도 시스템 전역 1개. source별 분리는 결정 질문 Q-R1

## 식별자 [합의 11/02 — 사용자 확인 필요 (a)]

- 형식 `<kind>:<key>`, kind ∈ `hard` / `soft` / `ml`(ml은 모델 버전 계약). key `^[a-z][a-z0-9_-]{1,63}$`(#74 규칙 유지)
- `hard:<key>`: 내장 하드룰. key는 **kebab-case**, 목록·조건·위험도는 detection-decision.md(#92). 이 문서의 예시 id `hard:process-status`(상태 HR, `methodVersion: process-status-hr-v1`)·`hard:negative-duration`(START_TIME > END_TIME)은 Detector 11/02 회신값. 접두 `hard:`/`soft:`가 다르므로 하드룰 key와 소프트룰 key는 충돌할 수 없음
- `soft:<key>`: 소프트룰 인스턴스. **서버가 생성** [제안]: `soft:<fieldId>-<8 hex>`(예 `soft:elapsed_ms-3f9a2c1d`, hex는 생성 시 난수; `fieldId` ≤ 54자라 key가 63자를 넘지 않음). 생성 후 불변. 클라이언트는 추가 요청에 `id: null` + `clientRef`를 보내고 응답의 `changes[]`에서 배정된 id를 받음
- 호환 매핑(v1/v3 판정·구성 **읽기 전용**, 자동 변환 없음)

| 기존 식별자 | 의미 | 새 체계 |
|---|---|---|
| `hr:process-status` | 상태 HR(`status_rule.py`) | 하드룰 1개(`hard:<status key>`, Detector 확정) |
| `rule:<rule_key>` | 등록 Rule DSL 버전 | 임계값형은 소프트룰 의미. 기존 `ml_rule_versions` 행은 읽기 보존, 소프트룰로 **자동 이관하지 않음**(사람이 새로 추가) |
| `sr:<key>` | 통계 SR R00~R23 | **폐기**. 저장된 v3 구성 없음(백엔드 미구현)이라 이관 대상 없음 |

## 룰 세트 상태 — `GET /api/rule-set` [신규, 로그인 사용자 조회·관리자 저장]

```json
{
  "contractVersion": "rule-set-v1",
  "revision": 12,
  "contentHash": "3b1f…",
  "contentHashVersion": 1,
  "savedAt": "2026-11-02T08:12:33Z",
  "savedBy": {"actorId": "admin-01", "displayName": "관리자"},
  "restoredFromRevision": null,
  "catalogVersion": "method-catalog-v2",
  "counts": {"hardEnabled": 2, "hardTotal": 2, "softEnabled": 1, "softTotal": 2},
  "hardRules": [
    {"id": "hard:process-status", "displayName": "상태가 S가 아님", "conditionText": "STATUS ≠ S", "methodVersion": "process-status-hr-v1",
     "severity": "critical", "enabled": true, "editable": ["enabled"]},
    {"id": "hard:negative-duration", "displayName": "시작 시각이 종료 시각보다 늦음", "conditionText": "START_TIME > END_TIME",
     "severity": "critical", "enabled": true, "editable": ["enabled"]}
  ],
  "softRules": [
    {"id": "soft:elapsed_ms-3f9a2c1d", "name": "처리 시간 5초 초과", "field": "elapsed_ms", "operator": "gt",
     "threshold": 5000, "unit": "ms", "severity": "warning", "enabled": true,
     "createdAtRevision": 7, "updatedAtRevision": 12},
    {"id": "soft:retry_count-91ab77e0", "name": "재시도 3회 이상", "field": "retry_count", "operator": "gte",
     "threshold": 3, "unit": "count", "severity": "info", "enabled": false,
     "createdAtRevision": 9, "updatedAtRevision": 11}
  ]
}
```

| 필드 | 규칙 |
|---|---|
| `revision` | 정수 ≥ 0, 단조 증가. r0 = 시스템 기본값(하드룰 전부 켬, 소프트룰 없음) |
| `contentHash`·`contentHashVersion` | 아래 리비전 형식 절 |
| `savedBy` | 저장한 관리자(JSON 계정 ID, FK 아님 — `rules.md` 규약과 동일) |
| `restoredFromRevision` | 되돌리기로 만든 리비전이면 출처 리비전, 아니면 null |
| `hardRules[]` | Detector 내장 목록 전체를 **항상 전부** 포함(꺼진 것 포함). `displayName`·`conditionText`·`severity`는 Detector 정의값의 읽기 전용 투영, `editable`은 항상 `["enabled"]`. **하드룰 위험도는 detection-decision.md(#92) 정의를 따르며 읽기 전용** — 예: `hard:process-status`는 #92 U2 기준 상태별 분기(F=critical, P·N=warning)이므로 단일 값이 아님. 이 문서 예시의 `"severity": "critical"`은 **참고값**이며 실제 응답 형식(단일 값 또는 상태별 객체)은 #92가 정함 |
| `softRules[]` | 저장된 소프트룰 전체(꺼진 것 포함). 순서 = `id` 사전순(실행 순서 의미 없음 — 조합은 Detector 정책) |
| `softRules[].field` | 카탈로그 `softRuleFields[].fieldId`(`method-catalog.md`). 생성 후 불변 |
| `softRules[].operator` | `gte` / `gt` / `lte` / `lt`(화면 ≥ > ≤ <). `eq`·`ne`·문자열 연산은 소프트룰에 없음(상태 등 범주 조건은 하드룰 영역) |
| `softRules[].threshold` | JSON number. 유한값, 유효 숫자 ≤ 15자리, 필드 `valueType=integer`면 정수. Detector 전달·저장 직렬화는 **고정소수 문자열**(지수 표기 금지, `ruleContract.ts:17` DSL 정규식 `^-?(?:0|[1-9]\d*)(?:\.\d+)?$`·40자 이내) — 변환 불가 값은 422 `soft_rule_threshold_invalid` |
| `softRules[].unit` | 필드 카탈로그의 단위를 서버가 채움(요청에 있으면 일치 검사, 불일치 422) |
| `softRules[].severity` | `info`(참고) / `warning`(주의) / `critical`(위험). 위험도 축의 판정 의미는 Detector |
| `softRules[].name` | 1~120자, 표시용. 룰 세트 안에서 유일(대소문자 구분 없이) |
| `createdAtRevision`·`updatedAtRevision` | 서버 기록(읽기 전용) |

## 저장 — `PUT /api/rule-set` [신규, 관리자, 동기]

- **전체 상태 치환**: 요청은 원하는 최종 상태 전체(하드룰 enabled 전부 + 소프트룰 전부). 부분 PATCH 없음 — 스냅샷·변경 요약·되돌리기를 한 형식으로 처리하기 위함 [제안]
- 헤더 `Idempotency-Key` 선택(기존 규약): 같은 키·같은 본문 → 첫 응답 재전송(200), 다른 본문 409 `idempotency_payload_conflict`

```json
{
  "expectedRevision": 12,
  "confirmHardRuleDisable": true,
  "restoreFromRevision": null,
  "note": "처리 시간 임계값 상향",
  "hardRules": [
    {"id": "hard:process-status", "enabled": true},
    {"id": "hard:negative-duration", "enabled": false}
  ],
  "softRules": [
    {"id": "soft:elapsed_ms-3f9a2c1d", "name": "처리 시간 8초 초과", "field": "elapsed_ms", "operator": "gt",
     "threshold": 8000, "severity": "warning", "enabled": true},
    {"id": null, "clientRef": "new-1", "name": "오류 비율 1% 초과", "field": "error_ratio", "operator": "gt",
     "threshold": 0.01, "severity": "critical", "enabled": true}
  ]
}
```

- `softRules[]`에 없는 기존 id = 삭제. `id: null` = 추가(`clientRef` 1~40자 필수, 본문 안 유일). 기존 id는 `field`가 저장값과 같아야 함(다르면 422 `soft_rule_field_immutable`)
- `hardRules[]`는 Detector 내장 목록 전체를 포함해야 함(누락 422 `hard_rule_missing:<id>`, 미지 id 422 `hard_rule_unknown:<id>`)
- `confirmHardRuleDisable`: **이번 저장에서 새로 꺼지는 이상 규칙(하드룰)이 1개 이상이면** `true` 필수, 아니면 422 `hard_rule_disable_confirmation_required` [제안 — R1 "끄기 허용(경고+확인)"의 범위를 '새로 끄는 행위'로 한정, Q-R5]. 이미 꺼져 있던 규칙을 그대로 두는 저장은 플래그 불필요(확인창에는 계속 강조 표시). 화면: 행 경고 "변경을 권장하지 않습니다 — 명백한 이상을 놓칠 수 있습니다" + 확인창에서 꺼진 이상 규칙 강조 + 확인 체크 [확정 R1]
- `restoreFromRevision`: 되돌리기 표시용. 값이 있으면 본문은 그 리비전 스냅샷과 **같아야 함**(다르면 422 `restore_payload_mismatch`; 화면은 `GET /api/rule-set/history/:revision` 스냅샷을 그대로 보냄). 되돌리기도 일반 저장과 같은 확인창·즉시 적용·실패 시 이전 룰 유지 [확정 R3]
- 검증 순서(첫 실패에서 중단, 운영 룰 불변)

| 검사 | 오류 |
|---|---|
| 본문 키 집합·타입, `expectedRevision` 정수 ≥ 0 | 400 `invalid_rule_set_request` |
| `expectedRevision` ≠ 현재 리비전 | 409 `rule_set_revision_conflict` + `{currentRevision, savedAt, savedBy}` |
| 하드룰 목록 누락·미지 id·중복 | 422 `hard_rule_missing:<id>` / `hard_rule_unknown:<id>` / `hard_rule_duplicate:<id>` |
| 이번 저장에서 새로 꺼지는 하드룰 있음 ∧ `confirmHardRuleDisable≠true` | 422 `hard_rule_disable_confirmation_required` |
| 소프트룰 id 미존재(삭제된 것 포함)·중복·`clientRef` 중복 | 422 `soft_rule_unknown:<id>` / `soft_rule_duplicate:<id>` / `soft_rule_client_ref_duplicate` |
| `field`가 카탈로그에 없음·`status≠supported` | 422 `soft_rule_field_unknown:<field>` |
| 기존 룰의 `field` 변경 | 422 `soft_rule_field_immutable:<id>` |
| `operator` ∉ 필드 허용 연산 | 422 `soft_rule_operator_invalid:<id or clientRef>` |
| `threshold` 비유한·정밀도 초과·정수 필드에 소수·고정소수 변환 실패 | 422 `soft_rule_threshold_invalid:<id or clientRef>` |
| `severity` ∉ {info, warning, critical} | 422 `soft_rule_severity_invalid:<…>` |
| `name` 길이·중복 | 422 `soft_rule_name_invalid` / `soft_rule_name_duplicate` |
| 소프트룰 수 > 32(현행 Rule 상한 `jobRoutes.ts:108`과 동일, #92와 합의) | 422 `soft_rule_limit_exceeded` |
| `restoreFromRevision` 미존재·본문 불일치 | 422 `restore_revision_unknown` / `restore_payload_mismatch` |
| 내용이 현재 상태와 완전히 같음(hash 동일 ∧ 이름까지 동일) | 409 `rule_set_unchanged`(리비전 증가 없음, 화면은 [저장] 비활성이 정상 경로) |

- 성공 응답 `200`

```json
{
  "revision": 13,
  "previousRevision": 12,
  "contentHash": "9c40…",
  "appliedAt": "2026-11-02T08:20:05Z",
  "appliesFrom": "next_decision",
  "changes": [
    {"type": "hard_disabled", "id": "hard:negative-duration", "displayName": "시작 시각이 종료 시각보다 늦음"},
    {"type": "soft_updated", "id": "soft:elapsed_ms-3f9a2c1d", "name": "처리 시간 8초 초과",
     "diff": {"name": ["처리 시간 5초 초과", "처리 시간 8초 초과"], "threshold": [5000, 8000]}},
    {"type": "soft_added", "id": "soft:error_ratio-5d2e0b19", "clientRef": "new-1", "name": "오류 비율 1% 초과"},
    {"type": "soft_removed", "id": "soft:retry_count-91ab77e0", "name": "재시도 3회 이상"}
  ],
  "summary": {"hardEnabled": 0, "hardDisabled": 1, "softAdded": 1, "softUpdated": 1, "softRemoved": 1, "softEnabled": 0, "softDisabled": 0}
}
```

- `changes[].type` ∈ `hard_enabled` / `hard_disabled` / `soft_added` / `soft_updated` / `soft_removed` / `soft_enabled` / `soft_disabled`. `soft_updated`의 `diff`는 바뀐 키만 `[이전, 이후]`(enabled만 바뀌면 `soft_enabled`/`soft_disabled`로 분류)
- `appliesFrom: "next_decision"` 고정 — 확인창 문구 "저장 이후 새 판정부터 적용"의 근거
- 화면 성공 문구 "운영에 적용됨 · 리비전 r13". 실패는 오류 코드 그대로 표시, 이전 룰 유지. 확인창 문구 예시: "이상 규칙 1개 끔 · 이상 징후 규칙 1개 추가, 1개 수정, 1개 삭제 — 저장 이후 새 판정부터 적용됩니다"

## 변경 요약 미리보기 — `POST /api/rule-set/preview` [신규, 관리자, 선택]

- 본문 = 저장 요청과 동일. 저장하지 않고 위 `changes[]`·`summary`와 검증 결과만 반환(`200 {valid: true, changes, summary, hardDisabledAfterSave: ["hard:negative-duration"]}` 또는 `200 {valid: false, error: {code, …}}`). 확인창이 서버 계산 diff를 쓰게 하기 위한 보조 경로; 화면이 자체 계산해도 됨. `expectedRevision` 불일치는 여기서도 409

## 변경 이력 — `GET /api/rule-set/history` [신규, 로그인 사용자]

- `GET /api/rule-set/history?limit=<1..100>&cursor=<base64url>`: 최신순 커서 페이지(기존 `reports/candidates` 커서 형식)

```json
{
  "items": [
    {"revision": 13, "savedAt": "2026-11-02T08:20:05Z", "savedBy": {"actorId": "admin-01", "displayName": "관리자"},
     "note": "처리 시간 임계값 상향", "restoredFromRevision": null, "contentHash": "9c40…",
     "summary": {"hardEnabled": 0, "hardDisabled": 1, "softAdded": 1, "softUpdated": 1, "softRemoved": 1, "softEnabled": 0, "softDisabled": 0},
     "changes": [{"type": "hard_disabled", "id": "hard:negative-duration", "displayName": "시작 시각이 종료 시각보다 늦음"}]},
    {"revision": 12, "savedAt": "2026-11-02T08:12:33Z", "savedBy": {"actorId": "admin-01", "displayName": "관리자"},
     "note": null, "restoredFromRevision": 10, "contentHash": "3b1f…",
     "summary": {"hardEnabled": 0, "hardDisabled": 0, "softAdded": 0, "softUpdated": 2, "softRemoved": 0, "softEnabled": 0, "softDisabled": 0},
     "changes": []}
  ],
  "pagination": {"limit": 50, "hasMore": true, "nextCursor": "…"}
}
```

- `GET /api/rule-set/history/:revision`: 위 항목 + `snapshot`(그 리비전의 `hardRules[]`·`softRules[]` 전체, `GET /api/rule-set`와 같은 형식) — 되돌리기 본문의 출처. r0 포함
- 이력은 **삭제·수정 없음**(append-only). 화면 하단 읽기 전용 목록 + 항목별 [이 상태로 되돌리기](→ 저장 흐름) [확정 R3]

## 리비전 형식 — 정수 리비전 + 내용 hash [합의 11/02 — 사용자 확인 필요 (b)]

- `revision`: 정수, 저장마다 +1. 전역 순서(단일 룰 세트). 판정 evidence에는 둘 다 기록 — **Detector evidence v4(`detector-evidence-v4`)가 `ruleSetRevision`(정수)·`ruleSetHash`(문자열) 이름으로 이 문서의 `revision`·`contentHash`를 그대로 복사**(Detector 11/02 회신)
- `contentHash`(= `ruleSetHash`) 문자열 형식: 모델 버전 `content_hash`(`contentHashVersion: 2`, `hashlib.sha256(...).hexdigest()`)와 같은 표기 — **소문자 16진수 64자, 접두 없음**(`sha256:` 같은 접두 금지)
- `contentHash` (`contentHashVersion: 1`): 아래 정규 객체를 `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`의 UTF-8 바이트로 SHA-256 hex. **주의: 현행 v1·v2 구성 hash 코드(`trainer/runner.py:408` `json.dumps(config_spec, sort_keys=True)` — 기본 separators `", "`·`": "`, `ensure_ascii=True`)와 직렬화 규칙이 다름**. 모델 버전 v3의 `contentHashVersion: 2`([model-version.md](model-version.md))는 룰 세트와 **같은 직렬화**(compact·ensure_ascii=False·UTF-8)를 쓰며, 다른 것은 현행 v1 코드뿐(Q-R6)
- **같은 리비전에 대해 API 저장 시 계산한 `contentHash`와 Detector가 evidence에 기록하는 hash는 동일해야 함** — Detector는 재계산하지 않고 이력 행의 `content_hash`를 읽어 기록(재계산한다면 위 규칙과 바이트 단위로 같아야 하며 시험으로 확인)

```json
{
  "hardRules": [{"id": "hard:negative-duration", "enabled": false}, {"id": "hard:process-status", "enabled": true}],
  "softRules": [
    {"id": "soft:elapsed_ms-3f9a2c1d", "field": "elapsed_ms", "operator": "gt", "threshold": "8000", "severity": "warning", "enabled": true}
  ]
}
```

- 위 예시의 키 표시 순서는 읽기용이며 실제 직렬화는 `sort_keys=True`로 모든 객체의 키를 정렬함(배열 순서만 id 사전순으로 서버가 정렬)
- **구현 시험 기준값**: 위 정규 객체를 이 절의 규칙으로 직렬화한 SHA-256 = `5b15237724b48fcc4f18335a374ad7d5d8d7bcbaa7b5f851120f4d17b23dd8aa`(2026-11-02 Python 3 `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`로 계산·reviewer 교차 확인). API·Detector 구현은 이 값을 단위 시험으로 고정
- 문서 다른 곳의 `3b1f…`·`9c40…`는 **형식 예시 값이며 계산값이 아님**
- 포함: 하드룰 `{id, enabled}`(id 사전순), 소프트룰 `{id, field, operator, threshold, severity, enabled}`(id 사전순, 꺼진 룰 포함, `threshold`는 **고정소수 문자열** — number 직렬화 차이를 없애기 위함)
- 제외: `name`, `unit`(필드에서 파생), 감사 필드(`savedAt`·`savedBy`·`note`·`restoredFromRevision`·`createdAtRevision`), `catalogVersion`. 이름만 바꾼 저장은 리비전은 늘지만 hash는 같음 → "판정 의미가 같은 룰"임을 hash로 알 수 있음 [제안]
- 되돌리기로 만든 리비전은 출처 리비전과 hash가 같음. 같은 hash라도 리비전이 다르면 다른 저장 사건

## 저장소 요구사항(PostgreSQL 세션 구현, 이름은 가칭)

- `ops_rule_set_revisions`(가칭): `revision bigint PK, content_hash text, snapshot jsonb(hardRules·softRules 전체), changes jsonb, summary jsonb, note text|null, restored_from_revision bigint|null, saved_by_actor_id text, saved_at timestamptz`. **append-only + UPDATE/DELETE 금지 트리거**(003/004의 `erd_v03_immutable`·`prevent_pipeline_immutable_mutation` 방식). r0 행은 마이그레이션이 시스템 기본값으로 삽입
- 현재 상태 = 최대 `revision` 행. 저장은 `SELECT … FOR UPDATE`(또는 advisory lock)로 현재 리비전을 잠근 뒤 `expectedRevision` 비교 → INSERT revision+1 → 같은 트랜잭션에서 Detector 통지(방식은 Detector/DB: `ops_system_state.revision`과 별개 카운터, 또는 NOTIFY) — 운영 룰이 "바뀌었다"는 사실과 새 리비전 행이 한 트랜잭션에서 확정
- 소프트룰 id 생성은 서버(API) 책임, 유일성은 스냅샷 안에서만 검사하면 충분(삭제된 id 재사용 금지는 hex 난수로 사실상 보장)
- 판정 행의 룰 리비전 기록 열·인덱스는 Detector·DB(evidence v4) 범위

## 기존 계약·코드와의 관계(사실)

- `rules.md`(`ml_rule_versions` Rule 초안 CRUD, `If-Match` 412/428): **그대로 둠**(기존 등록 Rule·LLM `adopt` 결과 읽기 보존 [확정 10/21]). 소프트룰은 이 테이블을 쓰지 않음 — 조건 형식이 단순(필드 1개·비교 1개)하고 리비전 단위가 "룰 세트 전체"라서. 실행 엔진(`detector/rules.py` `_condition`의 Decimal 비교)은 Detector가 재사용 가능 — 의미론은 detection-decision.md(#92)
- `hr:process-status`의 `statusRulePolicy`(`status_rule.py:5-7` `default_policy` S→no_hit·F→critical·P/N→warning): 하드룰로 흡수되는 방식(1개 룰 또는 F/P·N 분리)은 Detector 내장 목록 결정. 이 계약은 `hardRules[]`를 Detector 목록의 투영으로만 다룸
- `configuration_spec` v1·v2의 `ruleVersionIds`·`statusRulePolicy`·`ml_config_rules`: 기존 구성 실행에만 쓰이고 새 모델 버전(v3)에는 없음([model-version.md](model-version.md) 호환 절)

## 결정 질문(사용자·총괄)

| # | 질문 | 제안 |
|---|---|---|
| Q-R1 | 룰 세트를 시스템 전역 1개로 둘지, source별로 둘지 | 전역 1개(`ops_system_state` 단일 행과 일치). source가 늘면 `sourceId` 키 추가(리비전은 source별) |
| Q-R2 | `contentHash`에서 `name` 제외 | 제외(판정 의미 기준). 포함을 원하면 `contentHashVersion: 2` |
| Q-R3 | 소프트룰 상한 32(현행 `ruleVersionIds` ≤ 32 `jobRoutes.ts:108`, #92와 동일)·이름 유일 | 32 유지. **확대는 Trainer·Detector 합의로**(카탈로그 `softRule.maxRules`·#92 동시 개정) |
| Q-R4 | `rule_set_unchanged` 409 vs 무변경 저장 허용(리비전만 증가) | 409(의미 없는 리비전 방지). 화면은 변경 없으면 [저장] 비활성 |
| Q-R5 | `confirmHardRuleDisable` 범위: 새로 끄는 저장에만 vs 꺼진 규칙이 남아 있는 모든 저장 | **새로 끄는 저장에만**(reviewer 권고 8). 확인창 강조는 항상 |
| Q-R6 | 룰 세트 hash 직렬화(compact·ensure_ascii=False·UTF-8 = 모델 버전 v3 `contentHashVersion: 2`와 동일)를 **현행 v1 hash 코드**(`runner.py:408`, 기본 separators·ensure_ascii=True)에 맞출지 | 맞추지 않음(v1 코드는 v1·v2 행 전용으로 남김). 바꾸면 룰 세트 `contentHashVersion` 2로 |
| Q-R7 | 대분류 키 `hard/soft/ml`·식별자 접두 `hard:`/`soft:`/`ml:`은 Trainer·Detector 세션 합의(11/02)이며 **사용자 확인 전** | 사용자 확인 요청(#92에도 같은 질문 등재). 바뀌면 이 문서·method-catalog·model-version·detection-decision 동시 개정 |
