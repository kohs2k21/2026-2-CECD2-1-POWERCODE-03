Created: 2026-10-08T14:00:47+09:00
Updated: 2026-10-08T14:00:47+09:00
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
