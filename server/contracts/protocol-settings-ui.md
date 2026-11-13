Created: 2026-11-13T22:30:00+09:00
Updated: 2026-11-14T01:30:00+09:00
Author: Claude Code(WebUI builder)
Status: current — 실험 기능. 판정 의미는 Detector [protocol-rules.md](protocol-rules.md), 저장 형식·검증은 [rule-set.md](rule-set.md) v3 절

# 프로토콜 설정(정상 프로토콜) 화면·API 계약

- 범위: 룰 설정 › 프로토콜 설정(목록 → 인터페이스 상세) 화면, 관측 경로 초안 읽기 API, 룰 세트 v3 저장에서 API가 정한 값(본문·스냅샷 상한·경고 문장), 알림 [정상 프로토콜에 추가]·[이상 유지]
- 근거: 사용자 결정 11/13(프로토콜 설정 = 룰 하위 페이지, 인터페이스별 허용 경로 = 채널 순서 전체, 안전 상한만, 처음 보는 경로·인터페이스 = 이상 + 알림, 추가는 그 뒤 판정부터, 룰 세트 안에 저장, LLM은 설명만)
- 구현: 백엔드 `backend/src/ruleset/ruleSetContract.ts`(v3 검증·hash)·`ruleSetStore.ts`·`protocolDrafts.ts`, `backend/src/services/notificationReadModel.ts`(알림), 프론트 `frontend/src/features/detection/rules/protocols/`, `frontend/src/features/notifications/`

## 1. 저장 — `PUT /api/rule-set`(rule-set.md v3.6) 재사용

- 프로토콜 항목은 룰 세트 스냅샷 `protocolRules[]`에 있다. 별도 저장 경로 없음 — 화면은 현재 상태 전체(`GET /api/rule-set`) + 바꾼 프로토콜 항목을 본문으로 보낸다(전체 상태 치환). 서버 미리보기(`POST /api/rule-set/preview`) → 확인창 → 저장. 충돌 409·검증 422는 코드 그대로 안내, 운영 룰은 그대로
- 본문 상한: 저장(`PUT /api/rule-set`)·미리보기(`POST /api/rule-set/preview`)만 **관리자 확인 뒤** JSON 본문 **2MB**(v3 본문은 프로토콜 경로 전체를 담음), 룰 세트 라우터의 그 밖 경로는 64KB 그대로. 넘으면 413 `request_too_large`. 일반 사용자의 큰 본문은 읽기 전에 403
- 스냅샷 상한(rule-set.md v3.6 "스냅샷 전체 크기 상한은 API·DB가 정한다"): 저장할 스냅샷 JSON UTF-8 **4MiB** 초과 422 `rule_set_snapshot_too_large`(새 코드). DB `snapshot jsonb`는 크기 CHECK 없음
- 경고 문장(`warnings[]`): `relation_handoff_without_precedes` = relation-rules.md 4절 문장 그대로, `protocol_unregistered_without_entries` = "켜진 프로토콜 항목이 없어 등록되지 않은 인터페이스 규칙이 아무것도 판정하지 않습니다."(WebUI 결정). 미리보기의 새 관계 규칙 경고는 `ruleId: null` + `clientRef`
- 첫 항목 저장: 현재 `protocolRules`가 비어 있고 `hard:unregistered-interface`가 꺼져 있으면 상세 화면이 그 규칙 켜기를 **미리 선택**해 같은 저장에 싣는다(사용자가 끌 수 있음, rule-set.md v3.5). 이미 켜져 있으면 선택 칸 없음
- 새 항목 `source` = 초안을 읽었으면 `{jobId, snapshotId, period}`(초안 작업), 경로 `source` = 초안에서 고른 경로 `{kind:"draft", jobId}` / 알림에서 가져온 경로 `{kind:"alert", decisionId}` / 직접 입력 = 없음. 기존 경로·항목의 source는 서버가 저장값 유지(hash 입력 아님)
- 기본값: 새 항목 `severity` = `critical`(protocol-rules.md 4절), `enabled` = true

## 2. 관측 경로 초안 — `GET /api/rule-set/protocol-drafts[?interfaceId=]`(관리자)

- 원천: 가장 최근 **성공한** `recommend_relations` 작업 결과(`run_jobs.result_summary`, 규칙 추천과 같은 결과 — rule-candidates-ui.md 2절 정렬)의 끝 키 `protocolPathDrafts`(Trainer `trainer/protocol_paths.py`, protocol-rules.md 9절). 새 표 없음, 결과를 고치지 않는다
- 응답 `state`:
  - `protocol_drafts_unavailable` + `reason`: `no_result`(성공 작업 없음) / `not_in_result`(초안 집계 전 Trainer가 만든 결과 — 키 없음). `latestJob`(가장 최근 작업, 상태 무관), `not_in_result`면 `job`. **빈 초안·가짜 초안으로 바꾸지 않는다**
  - `available`: `{latestJob, job, snapshotId, sourceId, period{from,until}, split, parameters{minTransactions, rarePercent, maxPathSteps, maxRoutes, maxProcessRows, maxOutputPaths, reasonOrder}, excludedReasons, transactionIdMissingRows, endpointMismatch, interfaces[]}`. `interfaces[]` = Trainer 인터페이스 요약에서 `paths`를 뺀 값 + `listedPaths`(출력 경로 수). `interfaceId`를 주면 `interface` = 그 인터페이스 전체(`paths[]` 포함), 학습 기간에 없으면 `interface: null`(오류 아님)
  - `paths[]` 항목: `{channels, pathKey, transactions, ratio, rare, fromBranching, exampleTransactionIds}` — `exampleTransactionIds`는 Trainer `exampleRefs[].refs[0]`(`transaction:<id>`)를 한 번 되돌린 값(형식이 다르면 null). 원천 행·단계 id는 내지 않는다
- 엄격 검사(형식이 다르면 503 `protocol_drafts_invalid`, 해석하지 않음): `version` = 1(아니면 503 `protocol_drafts_version_unsupported`), 건수는 0 이상 정수, `status` ∈ {`draft`, `insufficient_evidence`}, 사유 키 ⊆ `parameters.reasonOrder`, `endpoints{compared, mismatch, notCompared}`, `omittedPaths{paths, transactions}`, 인터페이스·경로 중복 없음, 경로 수 ≤ `maxOutputPaths`, 경로 단계 1~`maxPathSteps`·채널 문자열, **`pathKey` = 채널로 다시 만든 값**(`%`→`%25` 다음 `:`→`%3A`, 쪼개 읽지 않음), `ratio` 0~1, `rare` boolean, `exampleRefs[]` `{refs: string[], steps: string[]}`. 결과 머리 `relationContractVersion`이 다르면 503 `rule_candidate_contract_unsupported`(규칙 추천과 같음)
- 쿼리: `interfaceId`만(1~512자). 다른 키 400 `unknown_protocol_draft_filter`, 형식 오류 400 `invalid_interface_id`. 권한·오류 형식은 룰 세트 라우터 공통(토큰 없음 401, user 403 `admin_required`, 저장소 503 `rule_set_storage_unavailable`)
- 자동 채택 없음: 화면은 경로마다 체크(기본 해제) → [선택한 경로 N개 추가] → 저장

## 3. 화면 — 탐지 설정 › 룰 설정 › 프로토콜 설정

- 룰 설정 진입 카드 "프로토콜 설정": "인터페이스별 허용 경로 · 정상 프로토콜 N개(켜짐 M개)" + [설정 보기]. 룰 설정 화면 저장은 프로토콜 항목을 현재 값 그대로 싣는다(편집은 이 하위 화면에서만)
- 목록 `/detection/rules/protocols`: 안내 한 줄, "등록되지 않은 인터페이스" 카드(내장 하드룰 `hard:unregistered-interface` 이름·조건은 서버 값, 켜기·위험도 — 바꾸면 바로 확인창 → 저장, 켜졌는데 켜진 항목이 없으면 경고 문장), 정상 프로토콜 목록(현재 운영 버전·등록 수·켜짐 수, 초안 상태 한 줄, [인터페이스 추가], 검색 — 인터페이스 ID 부분 일치·대소문자 무시). 표: 인터페이스 · 허용 경로 수 · 위험도 · 상태(켜짐/꺼짐/미등록) · 학습 기간 관측(경로 N개 · 거래 M건 · 근거 부족). 행 = 등록 항목 + 초안에 있는 미등록 인터페이스. 행 클릭 → 상세(뒤로가기 = 목록)
- 상세 `/detection/rules/protocols/:interfaceId`(주소의 인터페이스 ID는 `encodeURIComponent`, 값은 원래 문자열 그대로): 운영 버전·등록 여부(내부 규칙 id는 보이지 않음)·[항목 삭제]·[저장](변경 있고 확인 필요 없을 때), 켜기·위험도, 첫 항목이면 미등록 규칙 켬 선택(1절), 허용 경로 표(채널 칩을 화살표로 — 공백이 보이게, 같은 채널 연속은 합치지 않음, 추가 표시·삭제·저장하면 삭제될 경로 줄), [경로 추가](한 줄에 채널 하나, 값 그대로·CR만 뗌, 빈 값·`\N`·중복·64단계 초과는 먼저 막음), 관측 경로 초안(단추를 눌렀을 때만 조회 — 기준 기간·경로를 만든 거래 수·갈라진 거래 수·근거 부족·경로를 만들지 못한 거래 사유별 건수·표시하지 않은 경로 요약, 경로 표 선택 · 경로 · 거래 · 비율 · 표시(드묾 n% 미만·갈라진 거래 N건), 이미 허용된 경로는 "허용됨")
- 상한 표시: 허용 경로 `N/64`, 64개면 [경로 추가] 꺼짐. 서버 검증이 최종(422 코드 그대로)
- 비율 표기: 규칙 추천 충족률과 같은 한 자리 내림(0·1만 정수)

## 4. 화면 문장(새 문장 — 근거는 결정 11/13·protocol-rules.md·rule-set.md 용어)

- 목록 안내: "인터페이스별 허용 경로(거치는 채널 순서 전체)를 등록합니다. 켜진 항목이 있으면 거래 완료 때 그 인터페이스의 허용 경로에 없는 경로는 이상으로 판정합니다."
- 경고: "켜진 프로토콜 항목이 없어 등록되지 않은 인터페이스 규칙이 아무것도 판정하지 않습니다."
- 첫 항목: "등록되지 않은 인터페이스의 거래도 이상으로 판정(첫 항목 저장 때 함께 켬, 끌 수 있음)"
- 적용 시점: "저장 이후 새 판정부터 적용됩니다. 이미 이상으로 기록된 판정은 다시 판정하지 않습니다."
- 확인 필요: "허용 경로를 1개 이상 등록해 주세요" / "허용 경로는 64개까지입니다"
- 초안: [관측 경로로 초안 만들기], "학습 기간에 관측된 경로를 코드가 집계했습니다. 확인한 경로만 골라 허용 경로에 추가하고 저장합니다.", "학습 기간에 이 인터페이스의 경로가 관측되지 않았습니다.", "관측 경로 초안 없음 · 성공한 규칙 추천 후보 작업 결과가 없습니다", "관측 경로 초안 없음 · 최근 후보 작업 결과에 경로 집계가 없습니다(경로 집계 전 Trainer 결과)"
- 경로 입력: "채널 ID를 한 줄에 하나씩 거치는 순서대로 입력합니다(시작 → … → 끝). 값은 입력한 그대로 저장합니다.", "빈 값·\N인 채널은 저장할 수 없습니다.", "이미 있는 허용 경로입니다.", "경로 하나는 64단계까지입니다."
- 삭제: "<인터페이스> 항목(허용 경로 N개)을 삭제합니다. 다음 확인창에서 저장하면 운영에 반영됩니다."
- 알림에서 연 상세: "알림에서 가져온 경로를 허용 경로에 더했습니다. 저장하면 정상 프로토콜에 추가됩니다(그 뒤 판정부터 적용)."
- 사유 이름(protocol-rules.md 3.4): 학습 분할 경계·이상 라벨 거래·거래 행 없음·거래 행 모호·인터페이스 없음·단계 수 상한 초과·PROCESS_ID 없음·선행 모호·선행 값 없음·시작 단계 모호·선행 사슬 끊김·채널 없음·채널 모호·경로 단계 상한 초과·경로 수 상한 초과
- 관계 규칙(룰 설정 표): "관계 규칙: 표시할 항목이 없습니다." · 규칙 추천 승인: "승인됨 · 현재 룰 세트에 같은 조건의 규칙이 있습니다", "승인하면 룰 세트 새 버전으로 저장되고 되돌리기할 수 있습니다"
- 알림(5절): [이상 유지] 확인창 "<인터페이스> · <경로> 알림을 이상으로 유지합니다. 같은 경로는 다시 알리지 않으며 되돌릴 수 없습니다(모든 관리자에게 적용).", 실패 "이상 유지를 저장하지 못했습니다.", "정상 프로토콜 확인 · 처음 보는 경로|등록되지 않은 인터페이스", "인터페이스 <ID> · 경로 <채널 → …>|경로를 만들 수 없음 · 판정 N건", [정상 프로토콜에 추가]·[이상 유지](결정 11/13 버튼 이름), "이상 유지됨"·"정상 프로토콜에 추가됨"

## 5. 알림 [정상 프로토콜에 추가]·[이상 유지](notifications.md 프로토콜 항목)

- 원천: 알림 조회 범위(최근 5,000 이벤트) 안 최종 판정의 `evidence.protocolReview`(protocol-rules.md 5.3 — 프로토콜 방법이 T일 때만 있음). `version` 1, `kind` ∈ {`path_unmatched`, `interface_unregistered`}, `interfaceId` 비지 않은 문자열이 아니면 알림을 만들지 않는다
- 묶음 = (`interfaceId`, `pathKey`) — `routes[]` 중 `matched: false`인 경로마다(pathKey는 Detector 값 그대로, 쪼개 읽지 않음). `interface_unregistered`이고 `routes: []`면 인터페이스 단위 묶음(pathKey 없음). 항목 키 `protocol:` + sha256(`jsonb_build_array(interfaceId, pathKey 또는 '')::text`) 16진 64자
- 알림 시점: 묶음이 조회 범위 안에서 **처음 나온 판정 1번**(사유 `first_seen`). 같은 묶음이 다시 나와도 순번을 바꾸지 않고 판정 수만 는다. 사용자 알림 설정(판정 기준·위험도 하한)과 무관
- **처리 전 묶음의 범위 이동**: 첫 판정이 범위(최근 5,000 이벤트) 밖으로 밀리면 범위 안 가장 이른 판정이 새 순번이 된다 → 처리하지 않은 경로가 계속 나오면 다시 미확인이 된다(의도 — 아직 처리되지 않은 확인 요청). 처리한 묶음은 아래와 같이 다시 알리지 않는다
- **관리자만**: 프로토콜 항목은 관리자 목록·배지에만 있다(명령이 관리자 전용). 일반 사용자 목록·배지·읽음 요청에는 없다
- [정상 프로토콜에 추가](관리자, 처리 전): 그 인터페이스 프로토콜 상세를 **경로를 더한 초안**으로 연다(항목이 없으면 새 항목) → 일반 저장과 같은 확인창·동시 편집 규칙 → 룰 세트 새 버전. 재판정 없음, 그 뒤 판정부터(protocol-rules.md 5.1). 경로 없는 미등록 알림(`routes: []`)은 경로를 더하지 않고 상세만 연다(protocol-rules.md 5.3 [제안]). 알림에서 바로 저장하지 않는 이유: 확인창(변경 요약·꺼지는 이상 규칙 확인·경고)을 룰 설정과 같은 자리에서 보이기 위함
- [이상 유지](관리자, 처리 전): 되돌릴 수 없는 전역 기록이라 **확인창** 뒤에만 요청(제목·명령 "이상 유지", 문장 "<인터페이스> · <경로> 알림을 이상으로 유지합니다. 같은 경로는 다시 알리지 않으며 되돌릴 수 없습니다(모든 관리자에게 적용)." — 경로 없는 알림은 인터페이스만). 확인창은 알림 작은 창 밖에 연다. `POST /api/notifications/protocol-alerts/keep` 본문 `{interfaceId, pathKey | null}`(키 정확히 둘, 값 그대로 — `interfaceId` 1~512자, `pathKey` 비지 않은 문자열 또는 null). 룰 세트·판정은 바꾸지 않는다(판정은 이상 그대로, Detector는 이 기록을 읽지 않음). 지금 조회 범위에 그 묶음이 없으면 404 `protocol_alert_not_found`, 본문 오류 400 `invalid_protocol_keep_request`, user 403 `admin_required`. 기록 = 023 `ops_protocol_alert_keeps`(시스템 전역, 묶음당 1행, 추가만) `kept_through_seq` = 그 묶음의 현재 최대 순번. 두 번째 요청은 처음 기록 유지(`alreadyKept: true`). 누른 관리자에게는 그 항목 읽음. 응답 `{key, keptThroughSeq, keptAt, keptBy, alreadyKept, latestSeq, unreadCount}`
- **다시 알리지 않음**(protocol-rules.md 8절 사용자 결정): 이상 유지 뒤 항목 순번은 `kept_through_seq`를 넘지 않고, 그 순번이 조회 범위 밖(≤ 최대 이벤트 id − 범위)이면 **읽음으로 본다** — 범위 정리 DELETE로 그 항목 읽음 행이 지워져도 같은 묶음이 다시 나와 미확인이 되지 않는다. 목록에는 남고 "이상 유지됨" 표시(다시 보려면 프로토콜 설정에서 확인)
- 추가됨: 현재 룰 세트에 그 인터페이스 항목이 있고 그 경로가 허용 경로에 있으면(경로 없는 알림은 항목이 있으면) `resolution: "added"` — Detector는 옛 판정을 고치지 않으므로 화면이 현재 룰 세트와 대조(protocol-rules.md 5.3 권고)
- 항목 읽음: 023 `ops_user_protocol_alert_reads`(022 항목 읽음과 같은 규칙 — 022 표의 키 CHECK가 UUID 형식이라 따로 둠). "모두 읽음"은 022 순번 그대로 적용

## 6. 배포 순서·미검증

- 배포: DB 020(적용됨) → **Detector v3**(로더 + 관계·프로토콜·새 내장 판정, protocol-rules.md 10절) → DB 023 → **API v3 + 이 WebUI**(함께). API v3 저장은 Detector v3 배포 확인 뒤에만(rule-set.md v3.7). 첫 v3 행이 생긴 뒤에는 앞으로만 고친다
- 저장소 안 안전 장치: 백엔드 시험 `backend/tests/detector-hash-v3.test.mjs`가 같은 저장소 Detector의 `rule_set_content_hash(snapshot, 3)`를 Python으로 불러 기준값 (c)·(h)와 이 API가 저장하는 스냅샷 hash를 대조한다. Detector가 v3를 읽지 못하면(또는 Python이 없으면) 실패하고 건너뛰지 않는다 → Detector v3가 같은 브랜치에 들어오기 전에는 이 API 변경의 CI가 실패한다(의도)
- Trainer `protocolPathDrafts`가 없는 옛 결과는 초안 없음 상태로 보인다(정상). 새 후보 작업을 돌리면 초안이 생긴다(작업 요청 단추는 아직 없음 — rule-candidates-ui.md 9절)
- 미검증: 실제 Detector `evidence.protocolReview` 산출물(합성 값으로만 시험), 큰 결과 JSON 읽기 비용(규칙 추천과 같은 한계), 응답 코드 목록 하위 페이지(`hard:transaction-failed` 코드별 위험도 등록 — 지금은 기본 단계와 등록된 코드 단계만 편집)
