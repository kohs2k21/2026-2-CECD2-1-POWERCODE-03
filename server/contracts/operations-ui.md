Created: 2026-10-09T23:47:02.2964769+09:00
Updated: 2026-10-09T23:47:02.2964769+09:00
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
