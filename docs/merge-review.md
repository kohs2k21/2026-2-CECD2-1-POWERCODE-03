# KSJ/HUJ 충돌 검토

요약: 현재 `chore/repository-layout`은 KSJ 기준의 폴더 구조 브랜치다. HUJ는 별도 fork/ref로 보존되어 있으며 현재 브랜치에 병합되었다고 주장하지 않는다. 아래는 충돌 위치와 계약 경계만 기록한 것으로, 코드 수정·시크릿/원시 샘플 열람·E2E 검증은 포함하지 않는다.

## 기준 SHA

- Merge base: `66c87c2ab1b96c61e63f03be37967e26dc3eabca`
- KSJ: `b1c890c64fc7e2ab8875f66e00b9b239d1e82157`
- HUJ: `5987bc4f4d03b7a230948b4ed523af353c22ff89`

## 문자 충돌 2건

| 파일 | 충돌 내용 | 근거 |
|---|---|---|
| `.gitignore` | KSJ는 `frontend/src/**/data/` 예외를 유지하고, HUJ는 `feature_rule/`을 추가한다. | `origin/KSJ:.gitignore`, `origin/HUJ:.gitignore` |
| `frontend/src/features/analysis/AnalysisMock.tsx` | KSJ는 `AnalysisDetailView`·`AnalysisInboxView`·추출 훅 조합이고, HUJ는 인라인 화면·상태/필터·SSE 처리를 포함한 대형 구현이다. | `origin/KSJ:frontend/src/features/analysis/AnalysisMock.tsx`, `origin/HUJ:frontend/src/features/analysis/AnalysisMock.tsx` |

## 의미 충돌 6건

| 항목 | 관찰된 차이 | 근거 |
|---|---|---|
| Mock/API와 fallback | HUJ는 fixture 로그를 `/api/anomaly/evaluate-risk`로 보내고 실패 시 프런트 계산으로 대체한다. KSJ 분석 훅은 프런트 mock 상세 형상을 직접 소비한다. | `origin/HUJ:frontend/src/services/mock/anomaly.mock.ts`, `origin/KSJ:frontend/src/testing/mocks/mockAnalysis.ts` |
| 엔드포인트/스키마 | 프런트 API 모듈의 `/api/process-anomalies*` 계열은 HUJ 백엔드에 없고, 백엔드는 `/api/anomaly/evaluate-risk`, `/realtime-stream`, `/logs`만 제공한다. evaluator의 얇은 입력/출력과 프런트의 풍부한 `AnomalyDetail` 형상도 다르다. | `origin/HUJ:frontend/src/services/api/anomaly.api.ts`, `origin/HUJ:backend/src/routes/anomalyRoutes.ts`, `origin/HUJ:backend/src/controllers/anomalyController.ts`, `origin/HUJ:frontend/src/types/domain.ts` |
| SSE/훅 소유권 | HUJ 화면은 localhost SSE URL을 직접 하드코딩하고 HTTP client는 `VITE_API_BASE_URL`을 별도로 사용한다. KSJ는 추출 훅이 분석 상태를 소유한다. | `origin/HUJ:frontend/src/features/analysis/AnalysisMock.tsx`, `origin/HUJ:frontend/src/hooks/useAnomalyDetails.ts`, `origin/HUJ:frontend/src/services/api/client.ts` |
| 인증/역할/토큰 | KSJ는 클라이언트 역할 선택, HUJ는 JWT 로그인·`/api/auth/me`·localStorage 복원이다. HUJ 회원가입 요청에는 역할이 포함되고 JWT 설정에는 secret 부재 시 fallback이 있다. | `origin/KSJ:frontend/src/app/App.tsx`, `origin/KSJ:frontend/src/features/role_select/RoleSelector.tsx`, `origin/HUJ:frontend/src/app/App.tsx`, `origin/HUJ:frontend/src/features/auth/AuthPage.tsx`, `origin/HUJ:backend/src/controllers/authController.ts`, `origin/HUJ:backend/src/config/jwt.ts` |
| 백엔드 타입/영속성 | HUJ 컨트롤러·middleware·repository가 참조하는 `backend/src/models/user.ts`가 없어 빌드 한계가 있다. 반면 `UserRepository`가 `data/users.json` 디렉터리/파일을 실행 중 만드는 것은 런타임 영속성 설계이며, 누락 파일 결함으로 단정하지 않는다. | `origin/HUJ:backend/src/controllers/authController.ts`, `origin/HUJ:backend/src/controllers/userController.ts`, `origin/HUJ:backend/src/middleware/auth.ts`, `origin/HUJ:backend/src/repository/userRepository.ts` |
| SMTP 인증 동작 | SMTP 설정 부재/전송 실패 시 HUJ는 인증 코드를 콘솔에 출력하고 가입 흐름을 계속한다. 시작 시 SMTP 설정 상태도 출력한다. | `origin/HUJ:backend/src/services/emailService.ts`, `origin/HUJ:backend/src/controllers/authController.ts` |

HUJ의 auth/API/SSE 및 mock 정책은 이 문서에서 해결하지 않았으며 별도 fork/ref 검토 대상으로 남긴다.
