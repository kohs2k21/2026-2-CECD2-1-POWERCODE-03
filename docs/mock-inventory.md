# 목업·임시 계산 교체 목록

현재 구현은 인증 API 사용, 미연결 화면은 목업 사용. 사용자 요청으로 UI의 MOCK·REAL API 출처 표시 제거; 구현 구분은 이 문서에서 관리. API 오류·빈 응답을 목업 성공으로 바꾸지 않는다. 아래는 현재 코드에서 확인한 위치와 교체 조건이며, 원시 샘플 행과 `server/backend/mock_logs.csv` 본문은 문서에 복사하지 않는다.

| 항목 | 현재 위치·출처 | 실제 교체 대상 | 제거 조건 |
|---|---|---|---|
| 로그인·세션 | `server/frontend/src/features/auth/AuthPage.tsx`, `server/frontend/src/services/api/auth.api.ts`, `server/frontend/src/services/auth/session.ts`, `server/frontend/src/app/App.tsx` | `server/contracts/auth.md`의 `/api/auth/login`·`/api/auth/me`와 Bearer 세션 | 로그인·복원·로그아웃과 401/403 오류 처리가 실제 backend 계약으로 유지되고, 공개 가입 UI/API가 다시 노출되지 않을 때 |
| 분석 목록·상세 | 인증 SSE 이벤트 → 기존 분석 목록·실시간 상세. 예시 데이터 혼합 없음 | 과거 조회·계층별 상세 API, 업무 상태 저장 API 미구현 | 현재 연결 수신분만 표시. 미제공 계층·LLM 분석 생성 금지 |
| 분석 스키마·응답코드 | `server/frontend/src/testing/mocks/mockFeatureSchemas.ts`, `mockRawSchemas.ts`, `mockResponseCodes.ts`; `server/frontend/src/services/mock/schema.mock.ts`와 `useFeatureSchema`가 사용 | 서버가 feature/raw-field/response-code 사전을 버전과 함께 제공하는 계약. 현재 HUJ route에는 해당 계약이 없음 | 실서버 사전이 화면의 필수 필드·코드 의미를 모두 반환할 때 |
| LLM 분석 | 기존 시뮬레이션 코드 보존, 실시간 분석 화면에서 사용하지 않음 | 실제 분석 worker/API의 비동기 상태·분석 결과 | 요청 ID·상태·결과 계약 확정 후 연결 |
| 홈·설정·관리자 화면 | `server/frontend/src/testing/mocks/mockWidgets.ts`, `mockTimeSeries.ts`, `server/frontend/src/services/mock/widget.mock.ts`, `settings.mock.ts`, `admin.mock.ts` | 인증된 widget/settings/admin API. 프런트 호출 모듈은 `server/frontend/src/services/api/widget.api.ts`, `settings.api.ts`, `admin.api.ts`에 있으나 HUJ route 구현은 별도 확인 대상 | 각 기능의 GET/PATCH 응답과 저장 범위가 확정되고 새로고침 후에도 실제 값이 유지될 때 |
| HUJ 위험도 임시 규칙 | HUJ `server/backend/src/services/anomalyService.ts`의 anomalyScore·responseCode·processTimeMs 가중 계산과 `server/backend/src/controllers/anomalyController.ts`의 호출 | `server/contracts/`의 risk 결과를 생산하는 detector/evaluator worker와 인증된 ingest 경계 | worker 결과 계약이 버전 관리되고 backend가 같은 점수를 다시 계산하지 않을 때 |
| 인증된 SSE 이벤트 | `useRealtimeAnomalies` → 기존 분석 목록·상세. 별도 이벤트 패널 제거 | `server/contracts/anomaly.md` raw+risk 계약 | 세션 수신 최대 50건, 영속화·유실 재생 없음. 실제 collector/detector 연결 별도 |
| sender fixture | HUJ `server/backend/sender.py`가 `mock_logs.csv`를 읽어 `POST /api/anomaly/logs`로 전송하며 `ANOMALY_API_TOKEN`을 요구 | 실제 collector 또는 명시적 테스트 replay가 admin Bearer 인증으로 공유 raw-log 계약에 ingest | collector ingest와 독립 fixture 테스트가 통과하고 sender가 운영 경로에 더 이상 필요 없을 때 |

## 기록 규칙

- 각 항목을 실제 API로 교체할 때 이 표의 위치·계약·제거 조건을 함께 갱신한다.
- 목업 사용 범위는 이 문서에 유지; API 실패의 목업 성공 대체 금지.
- `server/backend/sender.py`와 `server/backend/mock_logs.csv`는 개발용 경로로만 취급하며 실제 고객 원본 데이터와 혼동하지 않는다.

## 설정 화면 정합성

- 테마: 라이트 적용·선택 일치, 미지원 다크 비활성
- 프로필: 로그인 계정 이메일·역할·ID·생성일 표시
- 미구현 알림 정책·외부 연동·표시 옵션: 비활성 안내, 가짜 저장/테스트 성공 제거
- 실제 적용 중인 권한 메뉴·토스트: 상태 표시만, 변경 불가
- 화면 폭 전환: 기존 브라우저 저장 유지
