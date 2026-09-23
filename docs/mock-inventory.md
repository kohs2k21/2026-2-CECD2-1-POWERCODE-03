# 목업·임시 계산 교체 목록

Issue #1의 API 우선 기준에 따라, 실제 endpoint가 없는 기능만 기능별 목업으로 남긴다. API 오류·빈 응답을 목업 성공으로 바꾸지 않는다. 아래는 현재 코드에서 확인한 위치와 교체 조건이며, 원시 샘플 행과 `server/backend/mock_logs.csv` 본문은 문서에 복사하지 않는다.

| 항목 | 현재 위치·출처 | 실제 교체 대상 | 제거 조건 |
|---|---|---|---|
| 분석 목록·상세·상태 | `server/frontend/src/testing/mocks/mockAnalysis.ts`의 `mockAnomalyDetails`와 `server/frontend/src/services/mock/anomaly.mock.ts`; `useAnomalyDetails`·`useAnalysisWorkspace`가 사용 | 목록/상세/상태 계약이 확정된 인증 API. 현재 `server/frontend/src/services/api/anomaly.api.ts`의 `/api/process-anomalies*`는 HUJ 서버 route에 없음 | 실제 목록·상세·상태 응답이 동일한 화면 형상을 제공하고 오류를 목업으로 대체하지 않을 때 |
| 분석 스키마·응답코드 | `server/frontend/src/testing/mocks/mockFeatureSchemas.ts`, `mockRawSchemas.ts`, `mockResponseCodes.ts`; `server/frontend/src/services/mock/schema.mock.ts`와 `useFeatureSchema`가 사용 | 서버가 feature/raw-field/response-code 사전을 버전과 함께 제공하는 계약. 현재 HUJ route에는 해당 계약이 없음 | 실서버 사전이 화면의 필수 필드·코드 의미를 모두 반환할 때 |
| LLM 분석 표시 | `server/frontend/src/features/analysis/hooks/useLlmAnalysisSimulation.ts`의 단계·동적 리포트와 `AnalysisMock.tsx` 연결 | 실제 분석 worker/API의 비동기 상태와 `summary`·`suspectedCause`·`recommendedAction` 결과 | 실제 요청 ID·상태·결과가 연결되고 시뮬레이션 타이머가 제거될 때 |
| 홈·설정·관리자 화면 | `server/frontend/src/testing/mocks/mockWidgets.ts`, `mockTimeSeries.ts`, `server/frontend/src/services/mock/widget.mock.ts`, `settings.mock.ts`, `admin.mock.ts` | 인증된 widget/settings/admin API. 프런트 호출 모듈은 `server/frontend/src/services/api/widget.api.ts`, `settings.api.ts`, `admin.api.ts`에 있으나 HUJ route 구현은 별도 확인 대상 | 각 기능의 GET/PATCH 응답과 저장 범위가 확정되고 새로고침 후에도 실제 값이 유지될 때 |
| HUJ 위험도 임시 규칙 | HUJ `server/backend/src/services/anomalyService.ts`의 anomalyScore·responseCode·processTimeMs 가중 계산과 `server/backend/src/controllers/anomalyController.ts`의 호출 | `server/contracts/`의 risk 결과를 생산하는 detector/evaluator worker와 인증된 ingest 경계 | worker 결과 계약이 버전 관리되고 backend가 같은 점수를 다시 계산하지 않을 때 |
| HUJ SSE 보강 | HUJ `server/backend/src/controllers/anomalyController.ts`의 `enrichedLog`/SSE broadcast, `server/backend/src/routes/anomalyRoutes.ts`의 `/realtime-stream`·`/logs` | collector→worker→server의 공유 이벤트 계약과 인증된 SSE. UI는 제공된 필드만 표시 | 실제 이벤트가 식별자·재연결 정책·필수 상세 필드를 제공하고 임시 보강 객체가 없어질 때 |
| sender fixture | HUJ `server/backend/sender.py`가 `MOCK_CSV_DATA`/경로의 `mock_logs.csv`를 읽어 `POST /api/anomaly/logs`로 전송 | 실제 collector 또는 명시적 테스트 replay가 공유 raw-log 계약으로 ingest | collector ingest와 독립 fixture 테스트가 통과하고 sender가 운영 경로에 더 이상 필요 없을 때 |

## 기록 규칙

- 각 항목을 실제 API로 교체할 때 이 표의 위치·계약·제거 조건을 함께 갱신한다.
- 목업이 남아 있는 기능은 UI에 목업 상태를 표시하고, API 실패를 목업 성공으로 숨기지 않는다.
- `server/backend/sender.py`와 `server/backend/mock_logs.csv`는 개발용 경로로만 취급하며 실제 고객 원본 데이터와 혼동하지 않는다.
