Created: 2026-10-09T23:47:02.2964769+09:00
Updated: 2026-10-09T23:57:30.2192421+09:00
Author: frontend_operations_builder
Status: current

# 운영 관측 · 이상 근거 조회 UI 계약

- 범위: `/operations` user/admin 공용 운영 조회; 관리자 탐지 작업 조회 모델과 별도
- 연결 경계: 아래 신규 API의 백엔드 구현 미연결; 404/503의 정상·성공 대체 없음
- 인증: 기존 Bearer·401 세션 정리·403 권한 부족; 서버의 사용자 권한 검사 필수
- 데이터: 운영 집계·구성 버전·판정 근거만 허용; 원본 BODY·고객 로그·인증값 반환 제외

## GET /api/operations/overview

- 허용: user/admin; 읽기 전용
- 필수: `observedAt` ISO 시각, `maxAgeMs` 서버 신선도 정책의 양수 ms
- 선택: `activeConfigurationVersion`, `endToEndLatencyMs`, `collectorEps`, `cpuPercent`, `memoryPercent`, `diskPercent`, `alertDeliverySuccessPercent`
- 수치: 유한·0 이상, Percent 항목 100 이하
- `endToEndLatencyMs`: ESB 로그 발생 → 판정 저장 실측; API 수신 시각 기준 재기산 금지
- `stages` 선택 키: `collector`, `redaction`, `observationQueue`, `detector`, `judgmentStore`
- 단계 선택 필드: `state` healthy/degraded/stopped/unknown, `pendingCount` 0 이상 정수, `oldestWaitMs` 0 이상 ms, `lastProcessedAt` ISO 시각
- 단계 의미: Collector 수집 → 민감 필드 제거 → 관측 저장·ID 큐 등록 → Detector 조회·추론 → 판정 저장
- 누락: — 확인 불가; 0 대기량은 실제 제공 시에만 0 표시
- 신선도: 현재 시각 - observedAt > maxAgeMs 시 오래됨; 미래 관측은 확인 불가; 조회 실패·오래됨에 정상 배지 금지
- 이전값: 재조회 실패·오래됨 시 최근 관측 시각과 상태를 함께 보존
- 조회: 계정별 전용 query key·AbortSignal·세션 변경 지연 응답 제외; 자동 폴링·404 재시도 없음, 수동 새로고침
- SSE: 기존 RealtimeAnomalyProvider 공유; 연결됨은 이벤트 연결 상태, Collector/큐/Detector health 증거 아님
- 위젯: 기존 배치/추가/드래그 유지; 미제공 집계의 고정값·성공률·미리보기 가짜 운영 상태 제거

## GET /api/anomaly/events/:eventId/detail

- 허용: user/admin; 별도 읽기 모델, 기존 SSE v1 필수 필드 변경 없음
- 필수: 요청과 일치하는 `eventId`; 다른 이벤트 응답의 상세 표시 금지
- 선택: 한국어 `summary`, 당시 `configurationVersion`, 실제 구성 ID `configurationId`, 실제 평가 ID `evaluationId`
- 선택: `decisionState` complete/partial/unavailable, 업무 `workflowStatus` Open/Resolved
- Rule/ML: 선택 객체 `rule`, `ml`; `execution` completed/partial/unavailable/not-run, 선택 `version`, `reason`
- 근거: `measurements`의 `name`, 선택 `value`, `threshold`, `unit`; 측정값·기준은 유한 숫자 또는 문자열
- 원인 가설: 별도 `hypotheses` 문자열 배열; 사실·정답·확정 원인으로 표시 금지
- 미제공/잘못된 선택 메타데이터: 확인 불가; 없는 Rule/ML 실행·임계값·구성·workflow 추정 금지
- 원본 BODY·거래 스냅샷·민감 필드 반환 제외; 프론트 모델은 명시한 필드만 복사
- 오류: 404/503 근거 미연결, 기타 조회 실패; 수신 SSE 기본 이벤트는 유지, 이전 상세 존재 시 이전값 문구 표시
- 조회: 계정+eventId 전용 key·AbortSignal·세션 지연 응답 제외; 자동 404 폴링 없음
- 관리자 링크: 실제 `configurationId` → `/detection/versions?tab=configuration&version=<id>`; 없으면 운영 구성 목록
- 관리자 링크: 실제 `evaluationId` → `/detection/evaluation?tab=candidates&result=<id>`; 미제공 ID 임의 생성 금지

## 업무 분류와 SSE 선택 메타데이터

- SSE v1 추가 선택 필드: `workflowStatus` Open/Resolved; 원천 `status`와 별도
- 기존 필수 검증·metadata 없는 유효 v1 이벤트 호환 유지
- 잘못된 workflowStatus는 기본 이벤트 수신 유지·workflow 해석 제외
- 목록: 제공된 workflowStatus만 분류; 미제공 시 보류/완료 0건 배지 금지·확인 불가
- 일부 제공: 분류함 제목의 상태 제공 건수 기준·나머지 이벤트 업무 상태 미확인
- 상세 GET workflow는 해당 이벤트에만 표시; 전체 목록 상태로 임의 전파 없음

## POST /api/anomaly/events/:eventId/feedback

- 허용: admin; 기존 서버 인증·관리자 검사 필수, user 쓰기 403
- 가용성: 상세 `capabilities.saveFeedback === true`인 관리자만 제출; 미제공·false·조회 실패 시 저장 비활성
- 요청: `kind` action/suspected-false-positive/insufficient-evidence, `reason` trim 후 1~2000자
- 성공: `eventId` 요청 일치, 실제 저장 `feedbackId`, 실제 `savedAt` 시각 필수
- 실패/미연결: 초안 유지·서버 저장 완료 표시 금지; 비일치/불완전 성공 응답도 미확인 처리
- 저장 의미: 조사·조치 기록; 업무 상태 전환·확정 라벨·학습 입력 승인과 별도
- 초안: `/analysis` 세션 draft의 이벤트별 사유/유형, 일반 사용자 편집 UI 없음
- 보호: 기존 페이지 이탈/새로고침 초안 보호·계정 변경 초기화; 요청 unmount/계정 변경 abort·지연 완료 제외
- 구현 경계: 신규 상세/피드백 API 서버 미연결; 로컬 초안의 서버 저장 성공 가장 없음
