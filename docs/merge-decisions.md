# KSJ·HUJ 병합 결정서

현재 브랜치: `import/legacy-layout` — KSJ 최신 코드와 폴더 정리 완료, HUJ 미병합.
Git 충돌 3곳과 동작상 선택 사항을 확인했다. 아직 애플리케이션 코드 수정·병합·커밋·push는 하지 않았다.

## 답변 작성

아래에 선택지 문자와 추가 요구를 적으면 된다. 추천은 제안일 뿐 확정된 결정이 아니다.

- Q1 분석 화면 구조: KSJ가 맞다. 필요기능만 가져다 연결해야함.
- Q2 목업/API 사용 방식: 목업은 결국 모두 제거해야한다. 아직 부족한 부분이 많지만 API 기반으로 하는게 맞다. API기반으로 연결하고 부족한 부분만 목업으로 대체한다. 목업으로 대체한 부분은 나중에 까먹지 않게 따로 기록해서 모두 실제 데이터 기반으로 연결되게 수정해야함.
- Q3 미구현 API·실시간 데이터: 일단 있는것만 가져다 쓴다.
- Q4 로그인·권한: 로그인 필수로하고, 테스트계정 미리 생성해놓고 그것만 쓸것. 회원가입은 일단 금지
- Q5 백엔드 빌드·사용자 저장: 추천대로
- Q6 인증메일: 추천대로.

## 이미 정해진 구조에 따라 처리할 것

| Git 충돌 | 확인 내용 | 처리 방향 |
|---|---|---|
| `.gitignore` | 현재 서버 경로의 fixture 예외와 HUJ의 `feature_rule/` 규칙이 충돌 | `server/` 경로 기준으로 규칙 정리 |
| `README.md` | 새 학교 저장소 설명과 HUJ의 옛 설명 충돌 | 2026-2 제목과 현재 폴더 설명 유지 |
| `frontend/src/features/analysis/AnalysisMock.tsx` | 현재는 옛 경로가 삭제됐고 HUJ는 그 파일을 수정함(modify/delete) | 파일은 `server/frontend/`에 배치, 화면 구현은 Q1에 따라 결정 |

자동 병합만 하면 루트 `backend/` 18개 파일, `frontend/`의 분석·로그인 파일, 루트 `package.json`과 workspace 파일이 생긴다. 합의한 server-only 배포 구조에 맞춰 옮기고 경로를 수정한다. 루트와 `server/`에 같은 앱을 두 벌로 유지하지 않는다.

## Q1. 분석 화면은 어느 쪽을 기준으로 할까?

KSJ는 화면을 컴포넌트·훅으로 분리했고, HUJ는 큰 단일 분석 화면에 실시간 연결을 추가했다. 단순히 한쪽 파일을 선택하면 다른 쪽 기능이 빠질 수 있다.

- **A · 추천:** KSJ 화면 구조를 유지하고 HUJ의 필요한 연결 기능만 옮긴다.
- B: HUJ 화면을 기준으로 삼고 KSJ의 화면 개선을 다시 반영한다.
- C: 분석 화면 통합은 보류하고 나머지부터 합친다.

근거: 현재 `server/frontend/src/features/analysis/AnalysisMock.tsx`, HUJ의 같은 파일 옛 경로.

## Q2. 목업과 실제 API를 어떻게 사용할까?

현재 KSJ 분석은 목업 서비스를 사용한다. HUJ는 목업에서 가져온 값을 백엔드로 보내 점수를 계산하고, 실패하면 프론트 계산으로 대체한다. API 실패가 정상 결과처럼 보일 수 있다.

- **A · 추천:** 기본은 목업 모드. API 모드를 별도로 선택하고 API 실패는 오류로 표시한다.
- B: 기본은 API 모드. 목업은 개발자가 명시적으로 켤 때만 사용한다.
- C: 이번에는 기존 목업 화면만 유지하고 API 연결을 보류한다.

근거: `server/frontend/src/hooks/useAnomalyDetails.ts`, 양쪽 `services/mock/anomaly.mock.ts`.

## Q3. 미구현 API와 실시간 데이터는 어디까지 연결할까?

프론트의 `/api/process-anomalies*` 목록·상세·상태 변경 API는 HUJ 백엔드에 없다. HUJ는 `/api/anomaly/evaluate-risk`, `/api/anomaly/realtime-stream`, `/api/anomaly/logs`를 제공한다. 실시간 응답은 상세 화면이 요구하는 정보를 모두 담고 있지 않다.

- **A · 추천:** 있는 API만 연결한다. 실시간 항목은 제공된 정보만 표시하고 미제공 정보는 빈값/미지원으로 명시한다. 목업 상세로 채우지 않는다.
- B: 이번 통합에서 목록·상세·상태 변경 API와 데이터 계약까지 새로 구현한다. 작업 범위가 커진다.
- C: API 코드는 보존하되 화면 연결과 실시간 수신은 보류한다.

A/B 선택 시 SSE URL을 공통 설정으로 관리하고, 구독·해제·중복 처리는 한 훅에서 맡긴다. 목업 모드에서는 실시간 연결을 열지 않는다.
근거: `server/frontend/src/services/api/anomaly.api.ts`, HUJ `backend/src/routes/anomalyRoutes.ts`, `backend/src/controllers/anomalyController.ts`.

## Q4. 로그인과 역할 선택은 어떻게 할까?

KSJ는 로그인 없이 역할을 선택하는 데모이고, HUJ는 JWT 로그인·회원가입·로그인 복원을 사용한다. HUJ 회원가입은 요청에서 역할을 받으므로 관리자 부여 정책도 정해야 한다.

- **A · 추천:** 목업 모드는 기존 데모, API 모드는 로그인 필수. 신규 가입자는 일반 사용자로 만들고 관리자는 별도 지정한다.
- B: 모든 화면에서 로그인을 필수로 한다. 신규 가입자는 일반 사용자로 만든다.
- C: 인증 코드는 보존하되 이번에는 기존 데모 진입만 유지한다.
- 관리자 지정 방식이나 공개 회원가입 필요 여부에 대한 추가 의견:

근거: 양쪽 `app/App.tsx`, HUJ `features/auth/AuthPage.tsx`, `backend/src/controllers/authController.ts`.

## Q5. 백엔드 실행과 사용자 저장은 어디까지 정리할까?

HUJ의 여러 파일이 참조하는 `backend/src/models/user.ts`가 저장소에 없다. 한편 `data/users.json`을 실행 시 만드는 것은 기존 저장 방식이며, 파일 누락 오류와는 구분한다.

- **A · 추천:** 기존 코드 사용에 맞게 누락 타입을 보완해 빌드 가능하게 한다. 이번에는 JSON 저장을 유지하고 배포 시 저장 경로를 보존한다.
- B: 누락 타입 보완과 함께 사용자 저장을 DB로 교체한다. DB 종류·스키마 결정이 추가로 필요하다.
- C: 백엔드는 코드만 이관하고 실행 가능한 통합은 보류한다.

근거: HUJ `backend/src/repository/userRepository.ts`, 인증 controller·middleware의 모델 import.

## Q6. 인증메일을 실제로 사용할까?

HUJ는 SMTP 미설정 또는 전송 실패 시 인증 코드를 콘솔에 출력하고 가입 흐름을 이어간다. 실제 메일 인증과 테스트 동작을 구분할 필요가 있다.

- **A · 추천:** 실제 인증 모드에서는 메일 전송 실패를 오류로 처리한다. 테스트 방식은 별도로 명시하고 운영에서는 사용하지 않는다.
- B: SMTP 준비 전까지 메일 인증을 사용하는 신규 가입을 비활성화한다. 테스트 계정 준비 방식은 별도로 정한다.
- C: Q4에서 인증을 보류했으므로 이번에는 메일 연결도 보류한다.

근거: HUJ `backend/src/services/emailService.ts`, `backend/src/controllers/authController.ts`.

## 검토 기준

- 현재 HEAD: `2be5a51ca5f74e8208ea0ea1e9e75bb6f5babec1`
- 포함된 KSJ: `b1c890c64fc7e2ab8875f66e00b9b239d1e82157`
- 병합 대상 HUJ: `5987bc4f4d03b7a230948b4ed523af353c22ff89`
- 검증: `git merge-tree --write-tree HEAD origin/legacy/HUJ`로 실제 작업 파일을 바꾸지 않고 충돌 확인.
- 답변 확정 후 코드 병합·빌드·동작 검증을 진행한다. 이 문서는 병합 완료 보고서가 아니다.

## 확정 보충사항 (Issue #1 실행 기준)

- 기본 실행은 API 우선으로 한다. 실제 endpoint가 있는 기능만 API에 연결하고, 없는 기능에 한해서만 기능별 목업을 명시적으로 사용한다.
- API 오류·빈 응답을 목업 성공으로 바꾸지 않는다. 기능별 목업과 실제 데이터 교체 조건은 docs/mock-inventory.md에서 추적한다.
- 모든 화면은 로그인 필수로 한다. 사전 생성된 테스트 계정만 사용하고, 회원가입 및 가입용 인증코드 API는 비활성화한다.
- 가입용 SMTP·메일 인증은 이번 통합에서 보류한다. 사용자 저장은 기존 JSON 방식을 유지한다.
- Issue: [#1](https://github.com/kohs2k21/2026-2-CECD2-1-POWERCODE-03/issues/1), 작업 브랜치: feat/1-ksj-huj-integration.
