Created: 2026-11-04T22:00:00+09:00
Updated: 2026-11-05T16:30:00+09:00
Author: #109 Claude Code(WebUI builder)
Status: current

# 룰 설정 화면·룰 세트 API 구현 계약

- 범위: `/detection/rules` 관리자 화면(#109, 상위 #101). 화면 기준 와이어프레임 #93 `section[data-view="detect-rules"]`. 요청·응답·검증 정본은 [rule-set.md](rule-set.md), 수식 추천은 [llm-recommendation-job.md](llm-recommendation-job.md) "규칙별 수식 추천" 절. 이 문서는 화면 규칙과 구현에서 정한 값만 적는다
- 연결: `/api/rule-set`(`backend/src/routes/ruleSetRoutes.ts`), 순수 규칙 `backend/src/ruleset/ruleSetContract.ts`, 저장소 `ruleSetStore.ts`, 수식 추천 `thresholdStats.ts`
- 내장 상수: 이상 규칙 목록 `ruleset/hardRuleCatalog.ts`(4개: 상태 규칙, 시각 역전, 성공 상태이나 오류 건수가 기록됨, 건수 합계 불일치), 등록 필드는 `ruleset/methodCatalog.ts`가 `contracts/method-catalog.json`을 읽는다. 각각 rule-set.md `hardRules[]` 예시·method-catalog.md 예시와 같은 값이며 `tests/rule-set-contract.test.mjs`가 문서와 대조한다(detection-decision.md 4.3 대조는 #117이 dev에 들어온 뒤 되돌린다). 정본 코드가 생기면 이 두 파일만 바꾼다
- 인증·역할: 기존 Bearer. 없음·잘못된 토큰 401. 조회·이력 = user/admin, 저장·미리보기·수식 추천 = admin(그 외 403 `admin_required`). 화면 자체는 admin 메뉴
- 저장소: `ops_rule_set_revisions`(마이그레이션 015). 저장은 트랜잭션 1개 — `pg_advisory_xact_lock(81739, 0)` → 일반 SELECT로 현재 리비전 읽기 → `expectedRevision` 비교 → 검증 → 일반 INSERT. API 역할에 UPDATE가 없어 `SELECT … FOR UPDATE`는 쓰지 않는다. NOTIFY 없음. 조회·저장 실패 503 `rule_set_storage_unavailable`(오류 원문 비노출, 빈 룰로 대체하지 않음)

## 화면 표시 용어 — 사용자 11/04 결정

- 화면에는 '리비전'과 `r13` 표기를 쓰지 않고 **'버전'과 숫자**로 표기한다(머리글 `버전 13`, 성공 문구 `운영에 적용됨 · 버전 13`, 변경 기록 열 `버전`·값 `13`, 되돌리기 제목 `버전 10 상태로 되돌리기`, 충돌 안내 `현재 버전 14`). 와이어프레임의 '리비전'·`r13` 표기보다 이 결정이 우선한다
- 오류 코드 원문(`rule_set_revision_conflict` 등)과 API 필드·코드 내부 이름(`revision`, `expectedRevision`)은 그대로
- 규칙 분류는 '이상 규칙'·'이상 징후 규칙'. 판정 정책·k는 화면에 없음

## 화면 구성(위에서 아래)

| 영역 | 내용 |
|---|---|
| 현재 운영 룰 | `현재 운영 버전 N` · 마지막 변경 · 변경자 · 켜진 규칙 수(이상 규칙 n/n · 이상 징후 규칙 n/n). `GET /api/rule-set` 응답 그대로 |
| 고정 줄 | 이상 규칙 위, 머리글 아래에 스크롤과 무관하게 고정(사용자 11/04). 알림(저장 중 / 적용됨 / 저장 실패(코드 그대로) / 저장 거절(리비전 충돌, `새로 불러오기`)), 입력 확인(`확인 필요 · …`), 저장 줄(`변경 N건`(서버 `changes[]`와 같은 단위), `변경 취소`, `저장`(변경이 있고 입력 확인 통과 시 활성)). 표 내용을 가리지 않게 바탕은 `--background`, 층은 `--z-sticky`(머리글 아래), 위치는 `--top-nav-offset`(TopNav가 실제 높이로 갱신). 입력칸 초점이 고정 영역 밑으로 가지 않게 `html`의 `scroll-padding-top`이 머리글과 고정 줄 높이(`--sticky-block-height`)를 더한다 |
| 이상 규칙 | 규칙·조건·위험도·켜기. 켜기는 전부, 위험도는 `editable`에 `severity`가 있는 규칙(상태 규칙 `hard:process-status`)만 코드별 선택(아래 절). 목록은 API 응답대로 그리므로 규칙이 늘어도 코드 수정이 없다. `severity.byCode`는 위험 → 주의 → 참고 순서의 세로 묶음으로 표시하고, 코드는 한 줄씩 `F 실패` 형식이며 묶음마다 왼쪽 배지가 그 묶음의 코드 줄 전체 높이를 채운다(코드가 늘면 자동 반영, 사용자 11/04). 구역 맨 아래에 켜짐·꺼짐과 무관한 상시 경고 한 줄 `명백한 이상을 놓칠 수 있습니다. 변경을 권장하지 않습니다.`(규칙 이름 없음, 경보 역할 아님, 사용자 11/04). 저장 확인창의 강조·확인 체크는 그대로 |
| 이상 징후 규칙 | 규칙 이름·대상 필드(읽기 전용)·조건·임계값·단위·위험도·켜기·수식 추천·삭제. `규칙 추가`는 등록 필드를 받은 뒤 활성. 머리글에 `AI 규칙 추천`(아래 절) |
| 변경 기록 | 버전·시각·변경자·요약·되돌리기. 현재 버전 행은 되돌리기 없음, 그 밖의 모든 행에 `이 상태로 되돌리기`(현재와 내용이 같은 행은 비활성: 같은 hash이거나 hash 버전이 다른 행만 미리보기의 `rule_set_unchanged`로 판단). 50건씩 `더 보기`, 끝나면 버튼 없음 |

- 등록 필드는 `GET /api/jobs/capabilities`의 `methodCatalog.softRuleFields`로 받는다. `methodCatalog`는 레지스트리 생성 JSON 전체다(#128). 응답에 없으면 오류로 표시하고 규칙 추가를 막는다
- 변경자 표시: 관리자 응답은 계정 email, 일반 사용자 응답과 계정이 없는 값(r0의 `system`)은 계정 ID

## 상태 규칙 코드별 위험도(사용자 11/04)

- 코드 F·P·Z·H·N, 기본값 F 위험, P·Z 주의, H·N 참고(`process-status-hr-v2`). 코드마다 `NativeSelect`로 위험·주의·참고를 고른다. 위험도 드롭다운 항목은 위에서부터 위험 → 주의 → 참고이고, 이상 징후 규칙의 표·추가 창 위험도 선택도 같은 순서다(사용자 11/05). 묶음은 위험 → 주의 → 참고 순서의 세로 묶음이고, 묶음마다 왼쪽 배지가 그 묶음의 코드 줄 전체 높이를 채운다(읽기 전용 표시도 같음, 사용자 11/04). 값을 바꿔 코드가 다른 묶음으로 옮겨도 초점은 그 선택 칸에 남는다
- 코드 옆에 뜻을 표시: S 성공, F 실패, P 처리중, N 미처리, H 부분성공, Z 데이터없음. 목록에 없는 코드는 코드만 표시한다
- 저장 요청: `hard:process-status`에 `severity.byCode` 5코드 전부, 다른 규칙은 `severity` 없음. 위험도 변경은 변경 건수·이탈 확인·변경 취소·되돌리기에 포함되고, 확인창·변경 기록에는 `이상 규칙 수정 · 상태가 S가 아님 · 위험도 N 미처리 참고 → 주의`처럼 바뀐 코드만 표시한다(`hard_updated`)
- 켜기와 위험도를 함께 바꾸면 변경 2건(`hard_disabled`/`hard_enabled` + `hard_updated`)이다. 확인 체크는 새로 끄는 경우만
- 되돌리기 본문: 위험도는 편집 가능한 규칙만 스냅샷 값으로 보낸다. 이전 `methodVersion` 출처(버전 0 등)의 값은 서버가 무시하고 내장 기본값으로 채운다
- 스냅샷에 없는 내장 규칙은 꺼짐·내장 기본 위험도로 표시한다(버전 0은 규칙 2개만 있어 나머지 2개가 꺼짐으로 보인다). 그 버전으로 되돌리면 켜져 있던 그 규칙들은 새로 꺼지는 것이라 확인 체크가 필요하다
- 내용 hash는 `contentHashVersion` 2(이상 규칙 `severity` 포함). 응답의 버전은 행 값 그대로이고, 변경 기록의 같은 상태 판정은 같은 hash 버전끼리만 비교한다

## 저장·되돌리기 흐름

1. `저장`: `POST /api/rule-set/preview`로 서버가 계산한 `changes[]`를 받아 확인창을 연다. 미리보기는 `confirmHardRuleDisable: true`로 보내 체크 전에도 나머지 검증 결과를 받는다. `valid: false`는 확인창 없이 코드를 표시, 409는 충돌 안내
2. 확인창: 변경 목록, 꺼지는 이상 규칙 강조, 새로 끌 때만 확인 체크 필수, 이미 꺼진 채 남는 규칙도 강조(`hardDisabledAfterSave`), `저장 이후 새 판정부터 적용됩니다.`
3. 적용: `PUT /api/rule-set`. 성공하면 `운영에 적용됨 · 버전 N`, 초안을 새 상태로 맞춘다. 실패하면 코드 그대로 표시하고 편집 내용 유지, 운영 룰 불변. `편집 내용은 남아 있습니다.` 안내는 저장 경로에만 쓰고 되돌리기 실패에는 쓰지 않는다
4. 충돌: 응답의 `currentRevision`으로 안내하고 현재 상태를 다시 읽는다. 편집 내용은 `새로 불러오기`가 서버에서 읽는 데 성공할 때까지 유지(실패하면 기존 조회 실패 표시)
   - 저장은 됐는데 재조회가 실패하면 `운영에 적용됨 · 버전 N`과 기존 조회 실패 표시만 남기고 편집 화면을 닫는다(옛 상태로 다시 저장할 수 없게). `다시 조회`가 성공하면 새 상태로 연다
5. 되돌리기: `GET /api/rule-set/history/:revision` 스냅샷을 요청 형식(`hardRules[{id,enabled}]`, `softRules[{id,name,field,operator,threshold,severity,enabled}]`)으로 옮겨 `restoreFromRevision`과 함께 같은 흐름으로 보낸다. 읽기 전용 키(`createdAtRevision`·`unit`·`editable` 등)는 본문에 넣지 않는다. 서버의 `rule_set_unchanged`는 화면을 거치지 않은 경합에서만 그대로 표시된다

- 초안: `stores/draftStore.ts`(경로별). 편집 중 이탈은 공통 NavigationGuard 확인창, `변경 사항 저장 후 이동`은 세션 초안 보존(복귀 시 복원), 운영 적용과 별개
- 미리보기·저장 대기 중에는 편집 영역을 잠근다(요청은 누른 시점의 초안이고 성공하면 초안을 새 상태로 맞추므로 그 사이 입력이 사라지는 것을 막음)
- 변경 건수는 저장 값 기준: 이름 앞뒤 공백만 바뀌었거나 값을 원래대로 되돌리면 0건이고 저장 비활성
- 입력 확인(화면): 이름 필수, 임계값 숫자. 그 밖의 검사(정수 필드, 유효숫자, 이름 중복, `detection_methods_empty` 등)는 서버 코드를 표시

## 구현에서 정한 값(rule-set.md에 코드가 없는 곳)

| 경우 | 값 |
|---|---|
| 요청 `unit`이 카탈로그 단위와 다름 | 422 `soft_rule_unit_mismatch:<id or clientRef>`(대상 필드 변경 검사 다음) |
| `note` 길이 | 500자 이하, 초과 400 `invalid_rule_set_request` |
| 저장할 수 없는 문자 | `note`·`clientRef`의 NUL·짝 없는 서로게이트는 400 `invalid_rule_set_request`, `name`은 422 `soft_rule_name_invalid` |
| 이력 `limit`·`cursor`·미지 키 | 400 `invalid_limit` / `invalid_cursor` / `unknown_rule_set_filter`, 기본 limit 50 |
| 없는 이력·잘못된 경로 값 | 404 `rule_set_revision_not_found` / 400 `invalid_revision` |
| 멱등키 | 형식 오류 400 `invalid_idempotency_key`. 저장 표에 키 열이 없어 응답 재전송은 API 프로세스 메모리(최근 200건, 계정별)만 보장. 재시작 뒤 같은 키 재요청은 `expectedRevision`이 막아 409(이중 적용 없음) |
| 미리보기 | 본문 형식 오류는 400, `expectedRevision` 불일치만 409, 나머지는 `200 {valid: false, error: {code}}`. 새 규칙의 `changes[].id`는 null |
| 활성 모델 수(`detection_methods_empty`) | 활성 집합 저장소가 없어 v1·v2 투영: `ops_system_state.active_config_version_id`가 가리키는 구성의 `ml_config_models` 행 수 |

## AI 규칙 추천(일괄)

- 사용자 11/04 지시로 화면을 넣었다. 화면 이름은 `AI 규칙 추천`이고 계약 문서의 임계값 추천(`recommend_thresholds`)과 같은 기능이다. 요청·결과 형식은 [llm-recommendation-job.md](llm-recommendation-job.md) 그대로이고 **서버는 아직 `recommend_thresholds`를 받지 않는다**(`POST /api/jobs` 400 `invalid_job_request`). 운영 빌드에서는 그 실패와 코드가 그대로 표시된다
- 요청: `POST /api/jobs` `{kind: "recommend_thresholds", sourceId, ruleSetRevision, scope: {softRuleIds: null}, data, mode: "data_p95_with_llm_rationale"}`, 실행마다 새 `Idempotency-Key`. 결과는 `GET /api/jobs/:id`의 `resultSummary`(`threshold-recommendation-v1`), 진행 중만 5초 조회
- 와이어프레임에 기간 입력이 없어 `data`는 `{snapshotId: null, from: 3개월 전, until: 지금, timeBasis: "persisted_at"}`로 고정한다(수식 추천의 시간 기준과 같음). 출처가 여럿이면 머리글에 `출처` 선택
- 버튼: 저장된 켜진 규칙이 없으면 비활성. 대상이 저장된 버전이라 미저장 변경이 있어도 비활성이고 `저장 후 추천`을 표시한다(계약 `ruleSetRevision`). 출처 조회 실패는 `출처 조회 실패 · <코드>`, 출처가 없으면 `저장된 데이터 출처가 없습니다.`로 이유를 표시. 저장으로 버전이 바뀌면 결과를 닫되 실패 표시는 남긴다
- 표시 대상은 LLM 원본 출력(state·basis·condition·reason)이 아니라 서버가 코드 계산값과 합친 `threshold-recommendation-v1`이다. 계약 필드는 그대로 받고 표시만 합친다
- 실행 중 → 결과 표 5열(사용자 11/04) → `적용`
  - `선택`: 상태가 `제안`인 행만(기본 선택)
  - `규칙(대상 필드)`
  - `임계값`: 현재 → 추천, 조건 기호와 단위를 값에 붙임(예 `> 5,000 ms → > 7,211 ms`). 추천은 `displayThreshold`, 원값은 툴팁. `제안`이 아닌 행은 현재 값만
  - `상태`: 제안·근거 부족·보류·해당 없음·거부와 사유(`rejectedReason`). `제안`이 아닌 행은 회색
  - `근거`: 첫 줄 출처(`데이터 p95` 또는 `AI 제안`)·표본 수·min·max·p95, 다음 줄 근거 문장(응답 영문 그대로)
  - `measurementId`·`baselineId`·`reviews`·`quantileMethod`·`exclusionRule`·`adapterSha`는 표시하지 않는다
  - 375px에서는 표 내부 스크롤이고 페이지는 넘치지 않는다
- 표 행은 결과가 있는 규칙만이다: 저장된 켜진 규칙 중 결과의 `softRuleIds`에 든 규칙. 같은 필드라도 `softRuleIds`에 없는 규칙에는 값이 들어가지 않고, 초안에서 지운 규칙도 표에서 빠진다
- 결과 검증: 항목 하나라도 계약과 다르면(비객체 항목, `evidence` 형식, 모르는 `status`·`source`, `제안`의 `operator`·`threshold`·`displayThreshold` 형식, `제안`이 아닌데 값이 있음), `items`가 비거나, `softRuleIds`가 비었거나 저장된 켜진 규칙이 아닌 id가 있으면 `recommendation_result_invalid` 실패. 판정 기준은 편집 중 초안이 아니라 저장 상태다
- `적용`은 선택한 규칙의 조건·임계값 입력칸만 채운다. 위험도는 그대로, 서버 호출 없음, 저장해야 운영에 반영
- 실패: 작업 전체 실패 표시와 `다시 시도`, 오류 코드 그대로, 부분 결과 없음. `generation_timeout`은 `시간 초과`로 구분. `rule_set_revision_conflict`는 현재 상태를 다시 읽고 실패 표시는 남긴다(`다시 시도`는 새 버전으로 요청). 결과 형식이 계약과 다르면 화면이 `recommendation_result_invalid`로 실패 처리, 작업이 코드 없이 실패하면 `recommendation_failed`
- 조회 실패: 작업 상태 조회(`GET /api/jobs/:id`)가 실패하면 작업 실패가 아니라 `AI 규칙 추천 조회 실패 · <코드>`로 표시하고 5초 조회를 멈춘다. `다시 시도`는 같은 작업을 다시 읽고 새 작업을 만들지 않는다
- **개발 모드 예시 범위**: `import.meta.env.DEV`이고 서버가 `invalid_job_request`로 거절할 때만 계약 예시 JSON으로 결과 표를 보여 준다. 코드는 `features/detection/rules/ruleAiDevSample.ts`(예시와, 같은 필드의 켜진 규칙 id로 맞추는 함수)와 `ruleAiApi.ts`의 `devSampleAfter` 두 곳뿐이고, 서버 구현 뒤 지운다. 필드로 규칙을 잇는 것은 이 예시뿐이고 운영 경로에는 없다. 운영 빌드 산출물에 예시가 없는지는 `pnpm build` 마지막 단계 `tests/rules-dist.mjs`가 확인한다. 예시를 표시하는 별도 문구는 없다

## 수식 추천(규칙 행)

- 기간: 운영 머신러닝 모델 학습 기간 시작 ~ 현재(`active_model_training_start`) / 최근 1개월 / 최근 3개월 / 직접 지정. 출처가 여럿이면 `출처` 선택. 계산 → 값·표본 수·사용 기간 표시 → `적용`은 입력칸만 채운다(서버 저장 없음)
- `해당 없음`(표본 20건 미만 `최소 기준 미달`, 값이 모두 같으면 `분산 없음`), `거부`(`threshold_serialization_failed`), 실패는 코드 그대로, 입력칸 유지
- 화면은 항상 `statistic: p95`. 응답 `operator`(1차 항상 `gt`)를 `조건`으로 보여 주며 규칙 조건은 바꾸지 않는다(Q-L4 "화면 조건과 다르면 사람이 조정")
- 표본: 학습 스냅샷과 같은 순서로 PROCESS 관측(실행 식별 있음)에 기간을 먼저 적용하고 그 안에서 실행별 최신 revision(`revision_no`, `persisted_at`, `id` 내림차순)을 고른 뒤, `status=S`, 최신 라벨이 anomaly가 아닌 행, 필드 값이 있는 행. 기간 밖의 새 revision은 영향이 없다
- 제외 구간(`excludedIntervals`)은 선택된 `timeBasis`의 시각 열에 적용한다(`evidence.exclusionRule`의 건수와 실제가 맞게). trainer-service.md의 "항상 ESB 발생 시각"과 다른 점이며 원천 시각이 비어 있는 동안의 구현이다. 화면은 지금 제외 구간을 보내지 않는다
- 값 계산: `elapsed_ms`는 `worker/derived_expression.py`와 같이 `data_processes.start_time_raw`·`end_time_raw`로 계산한다(collector가 `parsed_times`를 비워 둠). ISO 형식이 아니거나 해석 불가, 시간대 유무가 섞인 행은 값 없음으로 제외
- `threshold`는 저장 가능한 값으로 만든다: 유효숫자 15자리로 반올림, 정수 필드는 반올림 정수. 원값은 `evidence.p95`
- `timeBasis`: API는 `esb_occurred_at`(계약 기본) / `persisted_at`을 받는다. **화면은 최근 1·3개월·직접 지정에 `persisted_at`을 보낸다**: `data_observations.source_log_occurred_at`을 채우는 worker·backend 코드가 없어 기본값으로는 표본이 항상 0건이고, 학습 스냅샷 기준도 `persisted_at`이다(`GET /api/jobs/capabilities` `snapshot.timeBasis`). mode (a)는 화면이 보내지 않고 v1·v2 투영값 `persisted_at`을 쓴다
- 시간 기준의 한계: CSV 재생 데이터의 `persisted_at`은 재생 시각이라([trainer-service.md](trainer-service.md) 거래량 집계 절) 선택한 기간이 ESB 발생 시각 기준 기간과 다를 수 있다. Collector가 `source_log_occurred_at`을 채우면 화면이 `esb_occurred_at`으로 바꾼다(Collector 후속). 화면에 별도 안내 문구는 두지 않는다
- 366일: `custom`은 400 `period_too_long`. mode (a)가 366일을 넘으면 Q-L5 추천안대로 면제하고 일별 최대 2,000건 표본(`quantileMethod: percentile_cont_daily_sampled`, `sampled: true`). Q-L6은 추천안대로 학습 제외 구간을 자동 적용하지 않음(요청 `excludedIntervals`만)
- 달력에 없는 시각(2월 30일 등)·형식 오류는 400 `invalid_threshold_stats_request`, 응답 `evidence.period`의 `custom` 시각은 UTC로 정규화. 없는 출처 404 `source_not_found`. 문장 시간 제한 10초, 초과는 503 `rule_set_storage_unavailable`
