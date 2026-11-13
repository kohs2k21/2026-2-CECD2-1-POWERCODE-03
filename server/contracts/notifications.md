# 알림·사용자별 설정 규약

- 관련 작업: #20 WebUI 레이아웃 구조 개편, #100 프론트 기반(알림 전체 페이지 폐지), #101 하위 8 알림·설정 + 사용자별 저장 API
- 진입: 우상단 알림 버튼(배지) → 알림 패널. 알림 전체 페이지 `/notifications`는 #100에서 폐지, 옛 주소는 `/analysis`로 넘김(navigation.md)
- 알림 = 신규 알림 목록 + 해당 화면 바로가기(와이어프레임 #93 bell). 외부 채널(메일·메신저)·작업(학습·평가) 알림은 이번 범위 밖
- 저장: 마이그레이션 `022_user_settings_notifications.sql`(API 역할만, `infra/database/README.md` 022 절), 프로토콜 알림 처리 기록 `023_protocol_alert_actions.sql`(023 절), 알림 조회 기간 열 `026_notification_window_days.sql`(026 절)

## 알림 단위(사용자 11/13 "사건 단위 + 묶이지 않은 판정")

- 알림 항목은 저장하지 않고 API가 읽을 때 만든다. 원천: `run_decision_events`(최종 판정·중간 판정 기록 이벤트, 006·012) + 판정·중간 기록 스냅샷 + 사건(019)
- 알림 순번 = `run_decision_events.id`(커밋 순서). 조회 범위 = 최근 5,000개 이벤트(`window.events`, 요청 비용 상한) 중 사용자 알림 조회 기간(`window.days` = 설정 `windowDays`일) 안의 이벤트. 범위 밖으로 밀려난 항목은 패널에서 사라지고 `window.truncated=true`
  - 기간은 순번 경계로 쓴다: 범위 = 순번 > max(최대 순번 − 5,000, 개수 범위 안에서 "지금 − `windowDays`일"보다 이른 마지막 이벤트 순번). 이벤트 시각(`created_at`)은 순번을 붙이는 같은 잠금 아래 기록돼 순번 순서와 같다
  - `window.fromSeq` = 범위 첫 순번, `window.truncatedBy` = 범위를 정한 쪽: `days`(기간보다 이른 이벤트가 있음) / `events`(기간 안 이벤트가 5,000개를 넘음) / `null`(잘림 없음)
  - 미확인 수·읽음 요청(항목)·프로토콜 [이상 유지] 뒤 읽음 판단("범위 밖이면 읽음")도 같은 범위. 항목 읽음 행 정리는 개수 범위로만 한다(기간을 늘리면 다시 보이는 항목이 읽음을 잃지 않게). [이상 유지] 요청은 개수 범위에서 묶음을 찾는다(관리자 명령, 기간과 무관)
- 이벤트 판단(사용자 알림 설정, Detector 위험도 틀 Q-N1·Q-N1-1 ②): 이상 ∧ 판정 기준 충족 ∧ [위험도 ≥ 하한 ∨ (미정 포함 ∧ 단계 없는 근거 있음)]
  - 이상: 최종 판정 = `status<>unavailable` ∧ `evidence.anomalous=true`(analysis-ui 이상 신호, 이상 징후 포함). 중간 기록 `first_anomaly`·`evidence_added` = 이상
  - 판정 기준 `decisionBasis`: `interim` 중간 판정부터(중간 기록·최종) / `final_any` 최종(부분 포함) / `final_complete` 최종 완료만(`status=complete`)
  - 위험도: 저장된 v4 `composite.risk`만(재계산 없음, D7). v3·형식 오류 = 위험도 미정. 단계 없는 근거 = `status<>rated` 또는 `unratedMethods`가 비어 있지 않음
  - 정상·정상 여부 미확인은 알림 아님(D8)
- 묶이지 않은 판정 항목: 키 `job:<detect 작업 ID>`(작업 없는 이전 판정은 `decision:<판정 ID>`). 그 작업의 최종 판정이 사건 구성원이면 이 항목은 없고 사건 항목에 든다
  - 알림(순번 갱신) 사유 `reason`: `opened` 처음 통과, `risk_raised` 앞서 통과한 이벤트보다 위험도 상승, `withdrawn` 앞서 통과한 뒤 최종 없음(`no_final`) 또는 이상이 아닌 최종(재처리 대체 결과)
  - 근거 추가·최종 도착만으로는 순번이 바뀌지 않는다(D8: 같은 알림 내용만 갱신)
  - 항목 단계 `stage`: 최종 판정이 있으면 `final`(철회면 `cleared` = 이상이 아닌 최종). 재시도 대체 때 012 트리거가 새 시도 최종 뒤에 이전 시도 `superseded`를 기록하므로 마지막 이벤트 종류로 정하지 않는다. 최종이 없으면 철회는 `no_final`, 그 밖은 마지막 이벤트 `interim`·`updated`·`superseded`
- 사건 항목: 키 `incident:<흐름 id>`. 흐름 = 구성원 사건에서 병합(양방향)·이어짐(양방향)으로 닿는 사건 전부(사건 상세 `resolveFlow`와 같은 걸음). 흐름 id = 닿은 사건 중 (`opened_at`, 병합 뿌리 id)가 가장 작은 사건의 병합 뿌리 = 사건 보기 화면의 흐름 id(첫 구간). 병합 뿌리 = `merged_into`를 끝까지 따라간 사건. 어긋난 연결(incidents-ui flags)은 거르지 않아 그런 흐름은 화면 id와 다를 수 있음
  - 구성원 판정(최종)마다 판단: 첫 통과 구성원 `opened`, 위험도 상승 `risk_raised`, 그 밖 `grew`. 사건 위험도 = 통과한 구성원 위험도 최고값, `memberDecisions` = 조회 범위 안 통과 구성원 수
- 프로토콜 항목(contracts/protocol-settings-ui.md 5절): 키 `protocol:<sha256 64 hex>`, 묶음 (인터페이스, pathKey) — 최종 판정 `evidence.protocolReview`의 일치하지 않은 경로마다(경로 없는 미등록 인터페이스는 인터페이스 단위). **관리자 목록·배지에만**(일반 사용자 목록·배지·읽음 요청에서 빠짐). 범위 안에서 처음 나온 판정 1번만 알림(`first_seen`, 처리 전 묶음은 첫 판정이 범위 밖으로 밀리면 범위 안 가장 이른 판정으로 다시 알림), 사용자 알림 설정과 무관. [이상 유지] 뒤 순번은 유지 시점 순번을 넘지 않고 그 순번이 범위 밖이면 읽음(다시 알리지 않음). 항목 읽음은 023 `ops_user_protocol_alert_reads`
- 미확인: 항목 순번 > max(모두 읽음 순번, 그 항목 읽음 순번). 묶인 판정의 순번은 판정 이벤트 순번이므로 "모두 읽음" 뒤에 사건으로 묶여도 다시 미확인이 되지 않는다. 새 구성원이 추가되면(순번 증가) 다시 미확인

## API(인증 필수, user·admin 모두 자기 계정만, 오류 `{code}`)

| 요청 | 응답·동작 |
|---|---|
| `GET /api/notifications?view=unread\|all&limit=1~50&cursor=` | 기본 `view=unread`·`limit=20`. `{observedAt, latestSeq, readThrough, window:{events,days,fromSeq,truncated,truncatedBy}, settings, unreadCount, total, nextCursor, items[]}`(`settings` = 아래 알림 설정 4값). 순번 내림차순 → 항목 키 내림차순 키셋, 커서 = 응답 `nextCursor` 그대로 |
| `GET /api/notifications/unread-count` | `{latestSeq, unreadCount}`(배지) |
| `POST /api/notifications/protocol-alerts/keep`(관리자) | 본문 `{interfaceId, pathKey \| null}`. 화면은 확인창 뒤에만 요청. [이상 유지] 기록(protocol-settings-ui.md 5절). 응답 `{key, keptThroughSeq, keptAt, keptBy, alreadyKept, latestSeq, unreadCount}`. 400 `invalid_protocol_keep_request`, 403 `admin_required`, 404 `protocol_alert_not_found` |
| `POST /api/notifications/read` | 본문 `{"through":"<순번>"}`(모두 읽음) 또는 `{"items":[{"key","seq"}]}`(1~50개, 키 중복 금지). 모두 읽음 순번은 현재 최대 이벤트 id로 줄임. 항목은 요청자 설정 기준 지금 조회 범위에 있는 키만 저장(없는 키는 무시)하고 순번은 그 항목의 현재 알림 순번으로 줄임. 저장값은 되돌리지 않음(GREATEST). 응답 `{readThrough, latestSeq, unreadCount}` |
| `GET /api/me/settings` | `{notifications:{decisionBasis,minRiskLevel,includeUnrated,windowDays,isDefault,updatedAt}, widgetLayout:{layout,updatedAt}}`. 저장값 없으면 기본값 `interim`·`info`·`true`·`7`(`isDefault=true`), 배치 `null` |
| `PUT /api/me/settings` | 본문 `notifications`·`widgetLayout` 중 하나 이상(보낸 항목만 바꿈, 마지막 저장이 이김). `notifications` = `{decisionBasis, minRiskLevel, includeUnrated, windowDays?}`: `windowDays` 알림 조회 기간(일) 1~30 정수, 생략하면 저장값 유지(저장값이 없으면 7 — 026 이전 화면 호환), 그 밖 값은 400 `invalid_notification_settings`. 응답 = GET과 같은 전체 설정 |

- 항목 공통: `{key, type:"decision"|"incident"|"protocol", seq, reason, notifiedAt, unread}`. 순번은 문자열(bigint). 읽음 요청 `items[].key`는 세 형식 모두
- `decision`: `{detailId, decisionId, jobId, stage, completeness, finalSignal:"yes"|"no"|"unknown"|null, grade, riskStatus:"rated"|"unrated", riskLevel, hasUnratedEvidence, processId, transactionId}`. `finalSignal` = 마지막 최종 판정의 이상 신호(analysis-ui, 최종 없으면 null). 바로가기 `/analysis/d/<detailId>`(판정 ID, 최종 전이면 detect 작업 ID)
- `incident`: `{id, openedAt, criteria:{kind,value,longValue}, memberDecisions, riskStatus, riskLevel, hasUnratedEvidence}`. 바로가기 `/analysis/incidents/<id>`(상세가 어느 사건 ID든 흐름으로 풂)
- `protocol`: `{kind:"path_unmatched"|"interface_unregistered", interfaceId, pathKey|null, channels|null, routesReason, protocolRuleId, decisionId(최근 판정), decisions(판정 수), resolution:"kept"|"added"|null, keptAt, keptBy, processId, transactionId}`. 바로가기 `/analysis/d/<decisionId>`, 명령(관리자, 처리 전) [정상 프로토콜에 추가](프로토콜 상세를 경로를 더한 초안으로)·[이상 유지]
- 위젯 배치 `widgetLayout`: `{version:1~99, items:[{i,x,y,w,h}]}`, 0~32개, `i` 영문 소문자 시작 1~32자(중복 금지), x·y 0~999, w·h 1~99 정수. 위젯 이름·크기 제한은 화면이 읽을 때 다시 적용(`parseStoredLayout`)
- 400 code: `invalid_view`·`invalid_limit`·`invalid_cursor`·`unknown_notification_filter`(알 수 없는 쿼리)·`invalid_read_request`·`invalid_settings_request`·`invalid_notification_settings`·`invalid_widget_layout`·`invalid_json`. 413 `request_too_large`(본문 16KB). 401 인증(auth.md). 저장소 오류 503 `notification_storage_unavailable`(성공·빈 목록 대체 없음)
- 비용: 요청마다 범위 안 이벤트(최대 5,000행)를 PK 범위로 읽고 evidence·payload를 행마다 한 번 푼다. 기간 경계는 개수 범위 안을 PK 역순으로 읽어 정한다(`created_at` 색인 없음, 최대 5,000행). 사건 머리는 범위 안 사건만 재귀로 따라간다. 별도 쓰기 작업자 없음

## 화면

- 배지: 서버 `unreadCount`(99 초과 `99+`), 버튼 이름 `알림 · 읽지 않음 N건`. 조회 실패는 `?` 배지·`알림 · 읽지 않은 수 확인 불가`(0으로 바꾸지 않음)
- 배지 다시 조회: 화면 이동, 패널 열기, 읽음 표시 응답(그 전에 시작한 배지 조회는 취소해 읽기 전 값이 덮지 않음), 새 판정 이벤트(SSE `/api/pipeline/events?include=interims`를 `latestSeq` 다음부터, 이벤트가 있으면 10초에 한 번까지). 주기 조회 없음. 사건 묶음은 이벤트가 없어 다음 판정 이벤트·이동·패널 열기 때 반영
- 패널: `읽지 않음`(기본)/`전체` 보기, `모두 읽음`(응답 `latestSeq`까지), 항목 `읽음 처리`·`이동`(이동하면 그 항목 읽음), `더 보기`(커서), 빈 목록 `읽지 않은 알림이 없습니다.`/`알림이 없습니다.`, 조회 실패는 오류 표시
- 항목 문구: 판정 `이상|이상 징후 · 중간 판정|근거 추가|최종|이전 시도 대체됨[ · 위험도 상승]`, 철회 `철회 · 판정 실패(최종 없음)`·`철회 · 최종 판정불가|이상 신호 없음|정상 여부 미확인`(이상 분석 결과 문구), 대상 `거래 <ID> · 프로세스 <ID>`. 사건 `사건 · <기준> · 새 사건|판정 추가|위험도 상승`, `묶인 판정 N건`. 위험도 배지 위험/주의/참고/위험도 미정(철회는 생략)
- 설정 › 알림 설정 › 알림 대상: 알릴 판정 기준(중간 판정부터·최종(부분 포함)·최종 완료만), 위험도 하한(위험·주의 이상·참고 이상), 위험도 미정 포함. 바꾸면 바로 저장해 목록·배지에 적용(`관리자|일반 사용자 계정 알림 설정 반영 · 알림 목록·배지에 바로 적용`), 저장 중 입력 잠금, 실패하면 저장값 유지·오류 표시
  - `알림 조회 기간` 숫자 입력 1개(단위 `일`, 1~30 정수, 기본 7) + `저장` 버튼. 범위 밖·정수 아님은 입력 `aria-invalid` 표시·저장 버튼 비활성, 저장값과 같으면 비활성. 저장하면 알림 설정 4값을 보내고 목록·배지 다시 조회. 설명 문장 없음
- 패널 범위 안내: `window.truncatedBy=events`일 때만 기존 문구 `최근 판정 이벤트 N건 안의 알림만 보입니다.`(기간이 범위를 정할 때는 표시 없음)
- 설정 › 화면/위젯 › 운영 현황 위젯 배치: 저장 여부·위젯 수·저장 시각, 운영 현황에서 편집(링크), 기본 배치로 되돌리기(기본 배치 저장)
- 운영 현황 개요 위젯 배치: 계정별 서버 저장. 불러오기 전 위젯 미표시, 불러오기 실패는 기본 배치 + 안내·편집 막음, 저장 실패는 편집 중 배치 유지·다시 저장
- 옛 브라우저 저장 배치(`esb_operations_widgets_<계정 ID>`, 한 번만 옮김): 서버에 배치가 없고 옛 값이 유효하면 그 배치를 보이고 서버에 저장한 뒤 옛 키를 지움(저장 실패면 남겨 다음에 다시). 서버에 배치가 있으면 옛 키만 지움
- 로그아웃·계정 변경: 계정별 Query 캐시·SSE 정리(AppLayout 계정 키로 다시 마운트)

## 데이터·상태 책임

- 운영 알림의 옛 데모 이상 SSE(`/api/anomaly/realtime-stream`, RealtimeAnomalyProvider)는 패널에서 쓰지 않는다(앱에 연결하지 않음, 백엔드 경로·훅 파일은 유지)
- TanStack Query 계정별 key(`useAccountQuery`): `notifications-unread`·`notifications-list`·`me-settings`. 서버 변경은 Mutation, 실패를 성공으로 바꾸지 않음
- 바로가기 상세를 직접 열어도(패널 밖) 알림 읽음은 바뀌지 않는다(이상 분석 목록 미확인 점 `run_decision_reads`와 별개)

## 공통 표현

- 작은 창: 공통 Popover, Escape·바깥 클릭·이동 시 닫힘
- UI 흐름 검증과 실제 학습·평가·운영 판정 연결 완료 구분
