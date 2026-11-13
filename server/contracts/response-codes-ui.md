Created: 2026-11-13T21:00:00+09:00
Updated: 2026-11-13T23:00:00+09:00
Author: Claude Code(WebUI builder)
Status: current — 위험도 저장 형식·검증은 [rule-set.md](rule-set.md) v3 절(`hard:transaction-failed`), 판정 의미는 Detector [relation-rules.md](relation-rules.md) 7절

# 응답 코드(사전·코드별 위험도) 화면·API 계약

- 범위: 룰 설정 › 응답 코드(목록 → 코드 상세) 화면, 응답 코드 사전 표(migration 025)·읽기·메시지 편집 API, 사전 가져오기 도구, 이상 분석 항목의 응답 코드 메시지
- 근거: 사용자 결정 11/12 "응답코드도 룰 설정화면에서 수정할 수 있으면 좋겠어. 기본값은 이파일대로 가고"(긴 목록 = 목록 페이지 + 상세 페이지), 내장 하드룰 `hard:transaction-failed` 위험도 `{byCode, default}`를 응답 코드별로 편집(rule-set.md v3.5)
- 공개 저장소: 사전 값(코드·코드명·메시지)은 고객 자료라 저장소(코드·시험·문서·커밋 메시지)에 넣지 않는다. 기본값은 가져오기 도구로 **DB에만** 넣는다. 시험은 합성 코드만 쓴다
- 구현: 백엔드 `backend/src/responsecodes/`(파일 읽기·가져오기·조회·편집), `backend/src/routes/responseCodeRoutes.ts`, `backend/src/scripts/importResponseCodes.ts`, `backend/src/services/analysisReadModel.ts`(메시지 조인), 프론트 `frontend/src/features/detection/rules/responsecodes/`

## 1. 저장 위치 — 두 갈래

- **메시지(코드 설명)**: 사전 표 `ops_response_codes`(코드당 1행, 지우지 않음)와 변경 기록 `ops_response_code_changes`(추가만). API만 읽고 쓴다. Detector는 읽지 않는다
- **코드별 위험도**: 사전에 두지 않는다. 룰 세트 스냅샷 `hard:transaction-failed.severity = {byCode, default}`가 정본이고 저장은 `PUT /api/rule-set`(expectedRevision, 미리보기 → 확인창 → 저장)이다. 그래서 위험도 변경은 룰 세트 버전·이력·되돌리기에 그대로 남는다. 코드 등록 = `byCode`에 넣기, "기본값 따름" = `byCode`에서 빼기(그 코드는 `default` 단계)
- 사전에 없는 코드도 `byCode`에 있으면 목록·상세에 나온다(`inDictionary: false`, 화면 "사전에 없음")

## 2. 표(migration 025, `infra/database/README.md` "025 응답 코드 사전")

- `ops_response_codes`: `code`(PK, 인쇄 가능한 ASCII 1~64자 = rule-set.md v3.6 `byCode` 키 형식), `enum_name`(≤128)·`code_type`(≤64)·`http_status`(≤64, 원본 글자 그대로)·`message_key`(≤256)는 없으면 NULL(빈 문자열 금지), `message`(1~500자), `origin` `import`|`edited`, `revision`(1부터, 메시지 편집 낙관적 잠금), `updated_at`, `updated_by_actor_id`(계정 id, 가져오기는 `system:import`)
- `ops_response_code_changes`: 메시지 변경 기록(`kind` `import`|`edit`, `message_before` NULL = 처음 넣은 행, `message_after`, `actor_id`, `changed_at`). 기록을 사전 행 jsonb 열이 아니라 따로 둔 이유: API가 사전 행 UPDATE 권한을 가지므로 같은 행 열이면 기록도 고칠 수 있다. 따로 두고 SELECT·INSERT만 주면 기록이 바뀌지 않는다
- 권한: `powercode_api` 사전 SELECT·INSERT·UPDATE(DELETE 없음), 기록 SELECT·INSERT. 다른 역할 없음

## 3. API(`/api/response-codes`, 읽기 = 로그인 사용자, 편집 = 관리자)

- 공통: Bearer만(CSRF 토큰 없음), JSON 본문 16KB, 오류 `{code, …}`. 토큰 없음 401, 편집 user 403 `admin_required`, 잘못된 JSON 400 `invalid_json`, 저장소 오류 503 `response_code_storage_unavailable`(원문 비노출, 빈 목록으로 바꾸지 않음). 현재 룰 세트의 `hard:transaction-failed` 위험도가 `{byCode, default}` 모양이 아니면 503
- `GET /api/response-codes?q=&type=&limit=&cursor=`: 사전 행 + `byCode`에만 있는 코드. 정렬·커서 = 코드 포인트 순(`COLLATE "C"`, 룰 세트 정규 객체 `byCode` 순서와 같음). `q`(1~100자, 앞뒤 공백 제거·대소문자 무시) = 코드·코드명·메시지 포함, `type` = 유형 정확히(사전 행만), `limit` 1~200(기본 50), `cursor` = 앞 쪽 마지막 코드(base64url `{"code"}`). 다른 키 400 `unknown_response_code_filter`, 형식 오류 400 `invalid_query`·`invalid_type`·`invalid_limit`·`invalid_cursor`
  - 응답 `{ruleSet{revision, ruleId, enabled, defaultSeverity, registeredCodes}, counts{dictionary, edited, missingFromDictionary}, types[{codeType, count}], items[], pagination{limit, hasMore, nextCursor}}`
  - `items[]` `{code, inDictionary, enumName, codeType, httpStatus, messageKey, message, origin, severity{level, source}}` — `severity.source` = `code`(`byCode`에 있음) | `default`(기본 단계를 따름)
- `GET /api/response-codes/:code`(경로는 퍼센트 인코딩, 형식 오류 400 `invalid_response_code`, 사전에도 `byCode`에도 없으면 404 `response_code_not_found`, 쿼리 키 있으면 400 `unknown_response_code_filter`)
  - 응답 = 항목 필드 + `revision`(사전 행 수정 번호, 없으면 null)·`updatedAt`·`updatedBy{actorId, displayName}`·`ruleSet`·`messageHistory[]`(최근 50, `{kind, messageBefore, messageAfter, changedAt, changedBy}`)·`severityHistory[]`
  - `severityHistory[]`(최근 순 50): 룰 세트 저장 리비전 `changes[]`의 `hard_updated`(`hard:transaction-failed`) `diff.severity [이전, 이후]`(rule-set.md 저장 이력, 새 저장소 없음)에서 **그 코드의** 단계 또는 출처(코드별 ↔ 기본값)가 달라진 버전만 `{revision, savedAt, savedBy, restoredFromRevision, before{level, source}, after{level, source}}`. 기본 단계만 바뀐 버전도 그때 기본값을 따르던 코드에는 바뀜이다. 되돌리기도 바뀜이면 나온다(`restoredFromRevision`). 거래 실패 규칙이 바뀐 최근 기록 500건(위험도 외 켜기·끄기 등 포함) 안에서 찾는다
  - 변경자 표시: 계정 email은 관리자에게만(룰 세트 API와 같음), 그 밖은 계정 id
- `PUT /api/response-codes/:code`(관리자) 본문 `{message, expectedRevision}`: `message` 앞뒤 공백 제거 1~500자·제어 문자(줄바꿈 포함)·짝 없는 서로게이트 없음(아니면 400 `response_code_message_invalid`), 키가 둘이 아니거나 `expectedRevision`이 1 이상 정수·null이 아니면 400 `invalid_response_code_edit`
  - 사전 행: `expectedRevision` ≠ 행 수정 번호 → 409 `response_code_revision_conflict` + `currentRevision`, 같은 메시지 409 `response_code_unchanged`. 성공하면 `origin` = `edited`, 수정 번호 +1, 기록 `kind` `edit`
  - 사전에 없는 코드: 현재 `byCode`에 있을 때만 `expectedRevision: null`로 새 행(`origin` `edited`, 나머지 열 NULL). `byCode`에도 없으면 404, null이 아니면 409(`currentRevision: null`), 동시에 만들면 한쪽 409
  - 응답 = 저장 뒤 상세(GET과 같은 형식)

## 4. 화면(탐지 설정 › 룰 설정, 관리자)

- 룰 설정 카드 "응답 코드": `거래 실패 규칙의 코드별 위험도 · 코드별 N개 · 그 밖의 코드 <단계>`(+ ` · 규칙 꺼짐`), [목록 보기] → `/detection/rules/response-codes`. 값은 현재 운영 룰(추가 조회 없음)
- 목록 `/detection/rules/response-codes`: 머리 `현재 운영 버전 N · 사전 N개 · 코드별 위험도 N개 · 그 밖의 코드 <단계>`(+ ` · 사전에 없음 N개`), 검색(코드·코드명·메시지, 서버 조회)·유형 필터(`전체 유형`, `<유형> (N)`), 표 코드·유형·위험도(배지 + 기본값을 따르면 작은 글자 `기본값`)·메시지, 사전에 없는 코드는 배지 `사전에 없음`, 행·코드 → 상세. 한 번에 200개, 더 있으면 [더 보기]
- 상세 `/detection/rules/response-codes/:code`(뒤로 = 넘겨받은 목록 주소): 코드 정보(코드·코드명·유형·HTTP 상태·메시지 키·출처 `가져온 값`|`화면에서 수정`|`사전에 없음`·마지막 변경), 위험도(`위험`·`주의`·`참고`·`기본값 따름 (<기본 단계>)` 선택 → 서버 미리보기 → 확인창 → 룰 세트 저장, 결과 안내는 룰 설정과 같은 배너), 메시지(편집·[메시지 저장]), 위험도 변경 기록(버전·저장 시각·변경자·`<이전> → <이후>`), 메시지 변경 기록(시각·변경자·구분·메시지)
- 용어: 룰 세트 리비전 = `버전`(사용자 11/04), 단계 이름 `위험`·`주의`·`참고`(룰 설정과 같음)
- 새 문장(승인 대상 — `responseCodeLabels.ts`·화면 파일):
  1. `거래 실패 규칙이 응답 코드별로 쓰는 위험도와 코드 설명입니다. 위험도를 바꾸면 룰 세트 새 버전으로 저장됩니다.`(목록 머리)
  2. `거래 실패 규칙이 꺼져 있어 이 위험도는 지금 판정에 쓰이지 않습니다. 룰 설정에서 켤 수 있습니다.`(규칙 꺼짐, 목록·상세)
  3. `응답 코드 사전이 비어 있습니다. 운영자가 가져오기 도구로 기본값을 넣어야 합니다.`(빈 사전)
  4. `룰 세트 위험도에만 등록된 코드입니다. 메시지를 저장하면 사전에 추가됩니다.`(사전에 없는 코드 상세)
  5. `저장하면 룰 세트 새 버전으로 바로 운영에 적용됩니다(그 뒤 판정부터).`(위험도 카드 설명, 앞에 `거래 실패(hard:transaction-failed) · `)
  6. `메시지를 저장했습니다.` / `다른 변경이 먼저 저장되어 최신 메시지를 다시 불러왔습니다. 확인한 뒤 다시 저장해 주세요.` / `메시지 저장 실패 · <code>`
  7. 확인창 제목 `응답 코드 위험도 변경`, 요약 `응답 코드 <코드> 위험도 <이전> → <이후>`(예: `주의(기본값) → 위험`), 단추 `저장하고 적용`
  8. 짧은 이름: `사전에 없음`, `기본값 따름`, `기본값`, `가져온 값`, `화면에서 수정`, `가져오기`, `위험도 변경 기록`, `메시지 변경 기록`, `위험도 변경 기록이 없습니다.`, `메시지 변경 기록이 없습니다.`, `이 코드의 위험도가 바뀐 룰 세트 버전(최근 순)`, `코드·코드명·메시지 검색`, `전체 유형`, `목록 보기`, `더 보기`, `다음 목록 조회 실패 · <code>`, `응답 코드 조회 중…`, `응답 코드 조회 실패 · <code>`, `<N>/500자`, `메시지 저장`(단추), ` · 규칙 꺼짐`(카드 요약 뒤)
  9. 상세 표 머리글: 위험도 변경 기록 `버전`·`저장 시각`·`변경자`·`위험도`, 메시지 변경 기록 `시각`·`변경자`·`구분`·`메시지`. 코드 정보 이름: `코드`·`코드명`·`유형`·`HTTP 상태`·`메시지 키`·`출처`·`마지막 변경`

## 5. 이상 분석 — 응답 코드 메시지

- 판정 항목(목록·상세, analysis-ui.md 항목 필드)에 `responseCodeMessage` 추가: 항목 `responseCode`가 사전에 있으면 그 `message`, 없으면 null. 같은 조회 안의 PK 조인(페이지 행·상세 1건에만 계산, 항목마다 따로 묻지 않음)
- 문구: 목록 원인 줄 상태 규칙 `상태 F 실패 · 응답 코드 <코드> (<메시지>)`, 상세 `로그 상태 · 응답 코드` 값 `<상태> · <코드> (<메시지>)`. 메시지가 없으면 지금과 같다
- 이 메시지는 사전 설명이다. ESB 응답 메시지 원문(로그 값)은 여전히 내지 않는다(analysis-ui.md)

## 6. 가져오기 도구(`backend/dist/scripts/importResponseCodes.js`)

- `node dist/scripts/importResponseCodes.js <파일.xlsx|.csv> [--dry-run] [--force]`, `DATABASE_URL`은 환경 변수로만(`.env`를 읽지 않음), API 역할(`powercode_api`) 권한으로 충분. 서버 실행 절차는 `infra/database/README.md` "025 응답 코드 사전"
- 파일: `.xlsx` 첫 시트(통합 문서 순서) 또는 UTF-8 `.csv`(BOM 허용). 의존성 없이 읽는다(zip ZIP64·암호화 미지원). 머리글 = 처음 20행 안에서 코드·메시지 열이 함께 있는 첫 행. 머리글 이름(공백 제거·소문자 비교): 코드 `에러코드`|`응답코드`|`코드`|`code`|`errorcode`|`responsecode`, 코드명 `코드명(enum)`|`코드명`|`enum`|`enumname`, 유형 `유형(type)`|`유형`|`type`, HTTP 상태 `http상태`|`httpstatus`|`http`, 메시지 키 `메시지키`|`messagekey`, 메시지 `메시지(한국어)`|`메시지`|`message`. 같은 항목 열이 둘이면 `header_duplicate_column`
- 행: 셀 값 앞뒤 공백 제거. 빈 행 무시. **값 1칸뿐이고 그 값이 코드 형식이 아닌 행 = 묶음 제목 행**(건너뜀). 잘못된 행 사유 `code_missing`·`code_invalid`·`message_missing`·`message_invalid`·`enum_name_invalid`·`code_type_invalid`·`http_status_invalid`·`message_key_invalid` — 잘못된 행이 하나라도 있으면 아무것도 쓰지 않고 종료 2. 코드 열이 숫자 셀이면 앞자리 0이 사라졌을 수 있어 건수를 따로 낸다
- **같은 코드가 다시 나오면 한 행으로 합친다**(사용자 결정 11/13 — 원본 문서에 같은 코드가 서로 다른 오류 두 줄에 쓰인 경우가 있고, 로그에는 코드만 찍혀 어느 쪽인지 구분할 수 없어 둘 다 보인다)
  - 메시지·코드명·메시지 키: 서로 다르면 `<첫 값> 또는 <둘째 값>`(파일 순서, 셋 이상이면 계속 ` 또는 `로 잇는다). 이미 들어 있는 값과 같으면 다시 붙이지 않는다. 빈 칸은 값이 없는 것으로 보고 다른 행 값을 쓴다
  - 유형·HTTP 상태: 같아야 합친다(빈 칸도 값으로 비교). 다르면 그 뒤 행이 잘못된 행 `duplicate_code_type_mismatch`·`duplicate_http_status_mismatch`
  - 합친 결과가 상한(메시지 500자·코드명 128자·메시지 키 256자)을 넘으면 그 뒤 행이 잘못된 행 `merged_message_too_long`·`merged_enum_name_too_long`·`merged_message_key_too_long`
  - 출력 `merged codes: N (rows …)` = 합친 코드 수와 합쳐 들어간 뒤 행 번호(값은 내지 않는다). 다시 가져오면 같은 파일은 같은 합친 값이 되어 `unchanged`
- 반영(트랜잭션 1개): 새 코드 INSERT(`origin` `import`), 가져온 값 그대로인 행은 값이 다르면 UPDATE, **화면에서 고친 행(`edited`)은 `--force`일 때만 덮는다**, 파일에 없는 코드는 그대로(지우지 않음). 메시지가 바뀌면 기록 `kind` `import`. `--dry-run`은 같은 계산 뒤 ROLLBACK
- 출력은 건수·행 번호·사유 코드만(셀 값·연결 문자열 없음, DB 오류는 SQLSTATE만). 종료 코드 0 성공 / 1 사용법·파일·DB 오류 / 2 잘못된 행

## 7. 미검증·남은 일

- 배포 순서: DB 025 → 역할 스크립트 재적용(API의 025 표 GRANT, 023·024 적용 뒤 — protocol-settings-ui.md 6절) → API(이 화면·이상 분석 응답 코드 메시지). 025가 없으면 이상 분석 목록·상세 조회가 503
- 실제 서버 DB 가져오기 실행(운영자 작업, 이 계약은 절차만), 실제 Detector `hard:transaction-failed` 판정에서 응답 코드별 단계가 쓰이는지는 Detector 구현 몫(relation-rules.md 7절)
- 사전 중복 코드: 확정(사용자 11/13) — 메시지·코드명·메시지 키를 `A 또는 B`로 합친다(6절). 유형·HTTP 상태가 다르면 잘못된 행
- "연결 실패 계열 코드 = 위험" 기본값(rule-set.md 내장 표)은 코드 목록이 정해지면 새 `methodVersion`과 함께 넣는다. 지금은 화면에서 코드별로 등록한다
