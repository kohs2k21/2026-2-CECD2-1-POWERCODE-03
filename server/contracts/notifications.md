# 알림·사용자별 설정 규약

- 관련 작업: #20 WebUI 레이아웃 구조 개편, #100 프론트 기반(알림 전체 페이지 폐지), #101 하위 8 알림·설정 + 사용자별 저장 API
- 진입: 우상단 알림 버튼(배지) → 알림 패널. 알림 전체 페이지 `/notifications`는 #100에서 폐지, 옛 주소는 `/analysis`로 넘김(navigation.md)
- 알림 = 신규 알림 목록 + 해당 화면 바로가기(와이어프레임 #93 bell). 외부 채널(메일·메신저)·작업(학습·평가) 알림은 이번 범위 밖
- 저장: 마이그레이션 `022_user_settings_notifications.sql`(API 역할만, `infra/database/README.md` 022 절)

## 알림 단위(사용자 11/13 "사건 단위 + 묶이지 않은 판정")

- 알림 항목은 저장하지 않고 API가 읽을 때 만든다. 원천: `run_decision_events`(최종 판정·중간 판정 기록 이벤트, 006·012) + 판정·중간 기록 스냅샷 + 사건(019)
- 알림 순번 = `run_decision_events.id`(커밋 순서). 조회 범위 = 최근 5,000개 이벤트(`window.events`). 범위 밖으로 밀려난 항목은 패널에서 사라지고 `window.truncated=true`
- 이벤트 판단(사용자 알림 설정, Detector 위험도 틀 Q-N1·Q-N1-1 ②): 이상 ∧ 판정 기준 충족 ∧ [위험도 ≥ 하한 ∨ (미정 포함 ∧ 단계 없는 근거 있음)]
  - 이상: 최종 판정 = `status<>unavailable` ∧ `evidence.anomalous=true`(analysis-ui 이상 신호, 이상 징후 포함). 중간 기록 `first_anomaly`·`evidence_added` = 이상
  - 판정 기준 `decisionBasis`: `interim` 중간 판정부터(중간 기록·최종) / `final_any` 최종(부분 포함) / `final_complete` 최종 완료만(`status=complete`)
  - 위험도: 저장된 v4 `composite.risk`만(재계산 없음, D7). v3·형식 오류 = 위험도 미정. 단계 없는 근거 = `status<>rated` 또는 `unratedMethods`가 비어 있지 않음
  - 정상·정상 여부 미확인은 알림 아님(D8)
- 묶이지 않은 판정 항목: 키 `job:<detect 작업 ID>`(작업 없는 이전 판정은 `decision:<판정 ID>`). 그 작업의 최종 판정이 사건 구성원이면 이 항목은 없고 사건 항목에 든다
  - 알림(순번 갱신) 사유 `reason`: `opened` 처음 통과, `risk_raised` 앞서 통과한 이벤트보다 위험도 상승, `withdrawn` 앞서 통과한 뒤 최종 없음(`no_final`) 또는 이상이 아닌 최종(재처리 대체 결과)
  - 근거 추가·최종 도착만으로는 순번이 바뀌지 않는다(D8: 같은 알림 내용만 갱신). 항목 단계 `stage` = 마지막 이벤트: `interim`·`updated`·`final`·`superseded`·`no_final`·`cleared`
- 사건 항목: 키 `incident:<흐름 첫 사건 ID>`. 병합(`merged_into`)·앞 구간(`continues_from`)을 끝까지 따라간 사건 중 `opened_at`이 가장 이른 사건(500단계까지). 어긋난 연결(incidents-ui flags)은 따로 거르지 않음
  - 구성원 판정(최종)마다 판단: 첫 통과 구성원 `opened`, 위험도 상승 `risk_raised`, 그 밖 `grew`. 사건 위험도 = 통과한 구성원 위험도 최고값, `memberDecisions` = 조회 범위 안 통과 구성원 수
- 미확인: 항목 순번 > max(모두 읽음 순번, 그 항목 읽음 순번). 묶인 판정의 순번은 판정 이벤트 순번이므로 "모두 읽음" 뒤에 사건으로 묶여도 다시 미확인이 되지 않는다. 새 구성원이 추가되면(순번 증가) 다시 미확인

## API(인증 필수, user·admin 모두 자기 계정만, 오류 `{code}`)

| 요청 | 응답·동작 |
|---|---|
| `GET /api/notifications?view=unread\|all&limit=1~50&cursor=` | 기본 `view=unread`·`limit=20`. `{observedAt, latestSeq, readThrough, window:{events,fromSeq,truncated}, settings, unreadCount, total, nextCursor, items[]}`. 순번 내림차순 → 항목 키 내림차순 키셋, 커서 = 응답 `nextCursor` 그대로 |
| `GET /api/notifications/unread-count` | `{latestSeq, unreadCount}`(배지) |
| `POST /api/notifications/read` | 본문 `{"through":"<순번>"}`(모두 읽음) 또는 `{"items":[{"key","seq"}]}`(1~50개, 키 중복 금지). 순번은 현재 최대 이벤트 id로 줄이고 되돌리지 않음(GREATEST). 응답 `{readThrough, latestSeq, unreadCount}` |
| `GET /api/me/settings` | `{notifications:{decisionBasis,minRiskLevel,includeUnrated,isDefault,updatedAt}, widgetLayout:{layout,updatedAt}}`. 저장값 없으면 기본값 `interim`·`info`·`true`(`isDefault=true`), 배치 `null` |
| `PUT /api/me/settings` | 본문 `notifications`·`widgetLayout` 중 하나 이상(보낸 항목만 바꿈, 마지막 저장이 이김). 응답 = GET과 같은 전체 설정 |

- 항목 공통: `{key, type:"decision"|"incident", seq, reason, notifiedAt, unread}`. 순번은 문자열(bigint)
- `decision`: `{detailId, decisionId, jobId, stage, completeness, grade, riskStatus:"rated"|"unrated", riskLevel, hasUnratedEvidence, processId, transactionId}`. 바로가기 `/analysis/d/<detailId>`(판정 ID, 최종 전이면 detect 작업 ID)
- `incident`: `{id, openedAt, criteria:{kind,value,longValue}, memberDecisions, riskStatus, riskLevel, hasUnratedEvidence}`. 바로가기 `/analysis/incidents/<id>`(상세가 어느 사건 ID든 흐름으로 풂)
- 위젯 배치 `widgetLayout`: `{version:1~99, items:[{i,x,y,w,h}]}`, 0~32개, `i` 영문 소문자 시작 1~32자(중복 금지), x·y 0~999, w·h 1~99 정수. 위젯 이름·크기 제한은 화면이 읽을 때 다시 적용(`parseStoredLayout`)
- 400 code: `invalid_view`·`invalid_limit`·`invalid_cursor`·`unknown_notification_filter`(알 수 없는 쿼리)·`invalid_read_request`·`invalid_settings_request`·`invalid_notification_settings`·`invalid_widget_layout`·`invalid_json`. 413 `request_too_large`(본문 16KB). 401 인증(auth.md). 저장소 오류 503 `notification_storage_unavailable`(성공·빈 목록 대체 없음)
- 비용: 요청마다 범위 안 이벤트(최대 5,000행)를 PK 범위로 읽고 evidence·payload를 행마다 한 번 푼다. 사건 머리는 범위 안 사건만 재귀로 따라간다. 별도 쓰기 작업자 없음

## 화면

- 배지: 서버 `unreadCount`(99 초과 `99+`), 버튼 이름 `알림 · 읽지 않음 N건`. 조회 실패는 `?` 배지·`알림 · 읽지 않은 수 확인 불가`(0으로 바꾸지 않음)
- 배지 다시 조회: 화면 이동, 패널 열기, 읽음 표시 응답, 새 판정 이벤트(SSE `/api/pipeline/events?include=interims`를 `latestSeq` 다음부터, 이벤트가 있으면 10초에 한 번까지). 주기 조회 없음. 사건 묶음은 이벤트가 없어 다음 판정 이벤트·이동·패널 열기 때 반영
- 패널: `읽지 않음`(기본)/`전체` 보기, `모두 읽음`(응답 `latestSeq`까지), 항목 `읽음 처리`·`이동`(이동하면 그 항목 읽음), `더 보기`(커서), 빈 목록 `읽지 않은 알림이 없습니다.`/`알림이 없습니다.`, 조회 실패는 오류 표시
- 항목 문구: 판정 `이상|이상 징후 · 중간 판정|근거 추가|최종|이전 시도 대체됨[ · 위험도 상승]`, 철회 `철회 · 판정 실패(최종 없음)`·`철회 · 최종 판정 이상 아님`, 대상 `거래 <ID> · 프로세스 <ID>`. 사건 `사건 · <기준> · 새 사건|판정 추가|위험도 상승`, `묶인 판정 N건`. 위험도 배지 위험/주의/참고/위험도 미정(철회는 생략)
- 설정 › 알림 설정 › 알림 대상: 알릴 판정 기준(중간 판정부터·최종(부분 포함)·최종 완료만), 위험도 하한(위험·주의 이상·참고 이상), 위험도 미정 포함. 바꾸면 바로 저장해 목록·배지에 적용(`관리자|일반 사용자 계정 알림 설정 반영 · 알림 목록·배지에 바로 적용`), 저장 중 입력 잠금, 실패하면 저장값 유지·오류 표시
- 설정 › 화면/위젯 › 운영 현황 위젯 배치: 저장 여부·위젯 수·저장 시각, 운영 현황에서 편집(링크), 기본 배치로 되돌리기(기본 배치 저장)
- 운영 현황 개요 위젯 배치: 계정별 서버 저장. 불러오기 전 위젯 미표시, 불러오기 실패는 기본 배치 + 안내·편집 막음, 저장 실패는 편집 중 배치 유지·다시 저장
- 로그아웃·계정 변경: 계정별 Query 캐시·SSE 정리(AppLayout 계정 키로 다시 마운트)

## 데이터·상태 책임

- 운영 알림의 옛 데모 이상 SSE(`/api/anomaly/realtime-stream`, RealtimeAnomalyProvider)는 패널에서 쓰지 않는다(앱에 연결하지 않음, 백엔드 경로·훅 파일은 유지)
- TanStack Query 계정별 key(`useAccountQuery`): `notifications-unread`·`notifications-list`·`me-settings`. 서버 변경은 Mutation, 실패를 성공으로 바꾸지 않음
- 바로가기 상세를 직접 열어도(패널 밖) 알림 읽음은 바뀌지 않는다(이상 분석 목록 미확인 점 `run_decision_reads`와 별개)

## 공통 표현

- 작은 창: 공통 Popover, Escape·바깥 클릭·이동 시 닫힘
- UI 흐름 검증과 실제 학습·평가·운영 판정 연결 완료 구분
