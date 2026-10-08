Created: 2026-10-08T14:00:47+09:00
Updated: 2026-10-08T14:23:56+09:00
Author: frontend_lead
Status: current

# 탐지 관리 상세 UI 계약

- 현재: 관리자 4페이지의 조회·선택·입력·검증·확인 UI, 인증·권한은 auth.md 유지
- 원천: T15/P15 metadata, F01~F24·A00~A14 후보 보존. 새 구성 기본선택 68개, ErrorTime A14 보류; 선택과 계산/모델 입력 준비 상태 분리
- 개발 조회: `import.meta.env.DEV`에서만 명시 read fixtures, Query 비동기 어댑터. 프로덕션 조회는 service_unavailable; API 실패 뒤 fixture fallback 없음
- 작업 기능: 개발/프로덕션 기본 capabilities=false. 저장·snapshot 생성·학습·평가·운영 적용·복원·추천 요청 성공 시뮬레이션 없음
- 로컬 초안: Zustand pathname별 실제 editor 값, 내부 feature/rule/candidate ID별 map. URL에는 탭·필터·선택 ID만, 편집 내용은 제외. 새로고침/닫기 경고, 경로 이탈 유지/폐기, 세션 변경 reset
- 미연결: 확인창에서 실제 기능 사용 불가 표시·실행 버튼 비활성. 로컬 편집 내용 유지, 서버 영속 저장/학습 완료/적용 완료로 표시 금지
- 요청 접수와 완료: 서버 연결 이후 receipt.requestId/state=accepted만 접수, 작업 조회의 succeeded만 해당 작업 완료. 적용 성공은 activeVersionId 재조회 확인 필요
- 조회 실패: 영역 오류+retry, 기존 데이터 있는 재조회 실패는 이전 내용과 오류 함께 표시. null 측정값은 —, fit 통계 없으면 정상 실측 미리보기 금지
- 측정: 저장소 실제 volume·권한/quota 기준, used% OR absolute available 경고, unknown/stale/failed 구분. ESB 로그 발생부터 전체 지연, 원천 시각/시계동기화 없으면 미측정, 화면전달/XAI 별도

## 코드·외부 API 제안

- 코드 단일 read model: `frontend/src/features/detection/data/types.ts`의 DetectionData, 정적 후보 metadata: catalog.ts
- Query key: `[detection-workbench, userId]`; signal 취소·계정 변경 cache/draft 정리
- 외부 조회 제안(현재 미구현): `GET /api/detection/workbench` → DetectionData (capabilities 포함)
- 외부 작업 제안(현재 미구현): 초안 저장/snapshot/학습/평가/적용/복원/룰 추천 각각 관리자 POST API → ActionReceipt
- 요청 공통: snapshot/version/feature 정의·fit/평가 조건 명시; 적용/복원 expectedActiveVersionId로 경쟁 검사, 승인 요청과 active 상태 별개
- 실제 미연결: detection 조회 API·영속 초안·snapshot 생성·피처 서버 preview/fit·학습/평가 worker·추천 sLLM·적용/복원·수집/용량 관측. 기존 Express auth 및 이상 SSE만 별도 실제 연결

## 화면·편집 계약

| 화면 | 목록·상세·입력 | URL 선택·필터 |
|---|---|---|
| 만들기: 데이터·피처 | 고정 데이터셋/기간, T/P 원천·파생 목록, 정의 상세, 허용 연산/결측/시간/단위 입력·검증 미리보기 | tab, kind, featureQ, feature |
| 만들기: 모델 학습 | 모델별 피처/설정, 학습 요청 확인, 작업 상태·실패 사유 상세 | tab, jobState, jobQ, job |
| 만들기: 룰 설정 | 룰 생성·편집·기본값 복원, 추천 상세·선택/제외 | tab, ruleQ, rule, recommendation |
| 평가 | 후보·공통 snapshot/규약/분할 조건, 비교·결과 상세 | candidate, q, state, result |
| 운영 버전 | 현행/선택 후보 구성 차이·준비 조건, 관리자 승인·적용/복원 확인, 이력 상세 | candidate, rollback, history |
| 수집 | T/P/M/B 상태, 기간·원천·속성 조회, 저장소·지연 관측, 수집 이력 상세 | from, to, source, field, q, history |

- 기존 파생변수 편집: 원래 수식을 지원하는 연산만 정확한 기본 입력으로 편집. 복합/미확인/fit 수식은 정의 상세 보존; 새 파생변수 빌더와 구분
- 검증 미리보기: 사용자가 입력한 값의 계산 결과; 원천 실측·fit 결과 아님. 양측 의존성의 fit/미확인 상태 검사, 시간 offset·음수·분모 0·NULL 검증
- 학습: 기본 전체 선택과 유효성 별개. 기존 ID의 편집 정의도 이름·허용 연산·입력 타입·fit 조건 검사; 잘못된 입력은 요청 차단
- 추천 룰: 초안 선택 시 활성화, 제외 시 비활성화. 재선택은 수동 변경 내용 보존. 운영 버전에는 영향 없음
- 조회 갱신: 실패 시 이전 내용·오류 함께 표시, 초안 유지. 서버 정상 판정·서비스 성공으로 대체 금지
- 확인창: 변경 대상/현재 버전/평가 근거 표시, ID가 같아도 구성·연결 평가·Rule·모델·snapshot 내용 변경 시 승인/확인창 초기화. 미연결 capability=false는 실행 버튼 비활성
- 시험 어댑터: pending/empty/failed/retry/stale 경로·잘못된 입력·초안 이탈·미연결 요청 0회 검증. 제품 화면에 시험 제어 입력 없음
