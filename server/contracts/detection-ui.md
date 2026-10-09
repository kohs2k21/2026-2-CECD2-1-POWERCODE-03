Created: 2026-10-08T14:00:47+09:00
Updated: 2026-10-09T17:29:43+09:00
Author: frontend_lead
Status: current

# 탐지 관리 상세 UI 계약

- 현재: 관리자 4페이지의 조회·선택·입력·검증·확인 UI, 인증·권한은 auth.md 유지
- 원본: T15/P15 metadata, F01~F24·A00~A14 후보 보존. 새 구성 기본선택 68개, ErrorTime A14 보류; 선택과 계산/모델 입력 준비 상태 분리
- 개발 조회: `import.meta.env.DEV`에서만 명시 read fixtures, Query 비동기 어댑터. 프로덕션 조회는 service_unavailable; API 실패 뒤 fixture fallback 없음
- 작업 기능: 개발/프로덕션 기본 capabilities=false. 저장·snapshot 생성·학습·평가·운영 적용·복원·추천 요청 성공 시뮬레이션 없음
- 로컬 초안: Zustand pathname별 실제 editor 값, 내부 feature/rule/candidate ID별 map. URL에는 탭·필터·선택 ID만, 편집 내용은 제외. 새로고침/닫기 경고, 경로 이탈 유지/폐기, 세션 변경 reset
- 미연결: 확인창에서 실제 기능 사용 불가 표시·실행 버튼 비활성. 로컬 편집 내용 유지, 서버 영속 저장/학습 완료/적용 완료로 표시 금지
- 요청 접수와 완료: 서버 연결 이후 receipt.requestId/state=accepted만 접수, 작업 조회의 succeeded만 해당 작업 완료. 적용 성공은 activeVersionId 재조회 확인 필요
- 조회 실패: 영역 오류+retry, 기존 데이터 있는 재조회 실패는 이전 내용과 오류 함께 표시. null 측정값은 —, fit 통계 없으면 정상 실측 미리보기 금지
- 측정: 저장소 실제 volume·권한/quota 기준, used% OR absolute available 경고, unknown/stale/failed 구분. ESB 로그 발생부터 전체 지연, 원본 시각/시계동기화 없으면 미측정, 화면전달/XAI 별도

## 코드·외부 API 제안

- 코드 단일 read model: `frontend/src/features/detection/data/types.ts`의 DetectionData, 정적 후보 metadata: catalog.ts
- Query key: `[detection-workbench, userId]`; signal 취소·계정 변경 cache/draft 정리
- 외부 조회 제안(현재 미구현): `GET /api/detection/workbench` → DetectionData (capabilities 포함)
- 외부 작업 제안(현재 미구현): 초안 저장/snapshot/학습/평가/적용/복원/룰 추천 각각 관리자 POST API → ActionReceipt
- 요청 공통: snapshot/version/feature 정의·fit/평가 조건 명시; 적용/복원 expectedActiveVersionId로 경쟁 검사, 승인 요청과 active 상태 별개
- 실제 미연결: detection 조회 API·영속 초안·snapshot 생성·피처 서버 preview/fit·학습/평가 worker·추천 sLLM·적용/복원·수집/용량 관측. 기존 Express auth 및 이상 SSE만 별도 실제 연결

## 생성물·평가 식별 계약

- 룰 생성: 선택한 룰 정의만 검증·요청. 학습 데이터셋·기간·모델 설정과 독립; 활성 선택 없이 초안 생성/검토
- createRule/createCandidate: 요청 receipt는 accepted 접수이며 생성 완료 아님. 미연결 기본 capability=false, 로컬 편집/구성 미리보기는 서버 생성 성공으로 표시 금지
- 모델 산출물: ModelArtifact의 불변 id/version 및 피처·전처리·fit·학습 snapshot 묶음. 알고리즘 목록이나 실행 대기 작업을 완료 산출물로 선택 금지
- 룰 버전: RuleVersion의 불변 id/version·정의 묶음; 동일 논리 rule.id의 여러 버전 중복 조합 금지
- 구성 fingerprint: artifact와 룰 버전 정의·설명 기준의 정확 JSON 내용 식별값, 보안 서명 아님. VersionBundle 및 EvaluationResult 바인딩 필드 사용
- 동일 ID의 수식/임계값/fit/전처리 내용이 변경된 응답도 과거 평가를 새 구성 근거로 재사용 금지. API의 불변 revision 보장 및 서버 최종 재검증 필요
- 생성/저장 receipt만으로 다른 데이터·피처·학습 초안의 dirty 상태 해제 금지
- 모델·피처 초안 저장 payload: rules/ruleThresholdInputs/recommendationDecisions 제외. 룰 생성 payload는 선택한 유효 룰 정의만 사용
- 운영 조합: 완료 모델 산출물과 완료 룰 버전 선택 → 로컬 구성 미리보기 → 실제 새 후보 생성 요청 → 완료 후보 선택·평가. 로컬 미리보기를 생성 완료 후보나 운영 버전으로 추가 금지
- 설명 기준: 조회한 버전 상속·읽기 표시, 임의 ID 입력 없음. 미확인이어도 로컬 구성 검토 허용; 실제 생성/적용에 필요한 근거 확인은 별도
- 다음 평가 조건: 입력 중 조건, 평가 요청 payload만 변경. 기록된 평가 결과·조건·완료 시각은 보존; 구성 또는 입력 조건 불일치 시 현재 조건의 결과 없음/재평가 필요 표시
- 평가 근거: 결과 fingerprint와 후보의 현재 내용·고정 fingerprint 모두 exact 일치해야 적용 근거. ID 동일한 정의 변경도 비교 불가·승인/확인 초기화·적용 차단
- 확인 요청: 열 때 payload snapshot 캡처. 입력·capability/검증·계정 변경 시 이전 요청 수신 취소와 확인 상태 초기화; Dialog 닫기는 서버 작업 취소 성공을 뜻하지 않음

## 화면·편집 계약

| 화면 | 목록·상세·입력 | URL 선택·필터 |
|---|---|---|
| 생성: 데이터셋 | 고정 데이터셋·학습 기간 선택·추출 기준·스키마 버전 | tab |
| 생성: 피처 | TRANSACTION/PROCESS 원본·파생 목록, 정의 상세·검색·수식 입력·미리보기 | tab, kind, featureQ, feature |
| 생성: 모델 학습 | 모델별 피처/설정, 학습 요청 확인, 작업 상태·실패 사유 상세 | tab, jobState, jobQ, job |
| 생성: 룰 생성·편집 | 룰 단독 생성/입력 확인·기본값 복원, 추천 초안 가져오기·완료 룰 버전 | tab, ruleQ, rule, recommendation |
| 평가 | 후보·모델/룰 조합 검토·다음 평가 조건, 기록 조건/결과 비교·상세 | candidate, q, state, result, selection |
| 운영 버전 | 현행/선택 후보 구성 차이·준비 조건, 관리자 승인·적용/복원 확인, 이력 상세 | candidate, rollback, history |
| 수집 | T/P/M/B 상태, 기간·원본·속성 조회, 저장소·지연 관측, 수집 이력 상세 | from, to, source, field, q, history |

- 피처 목록: 원본/파생 유형 열; 필터·검색은 사용자 파생변수 포함, 선택과 계산 준비 상태 분리
- 카탈로그 파생변수: 고정 원본 상세 읽기 전용·삭제 없음. 정확한 수식을 지원하는 항목은 사용자 파생변수로 복제 후 편집; 복합/미확인/fit 수식 임의 변환 없음. 기존 ID의 과거 편집 초안은 자동 삭제 없음
- 사용자 파생변수: 상세 수정·입력 검증·삭제 확인/취소. 삭제 시 목록·전체 선택·현재/모델별 featureIds에서 제외, 의존 입력/룰 속성은 재선택 상태로 유지. 삭제 ID 재사용 금지·검증 실패/취소 시 초안 보존; 서버 저장 성공과 구분
- 검증 미리보기: 사용자가 입력한 값의 계산 결과; 원본 실측·fit 결과 아님. 양측 의존성의 fit/미확인 상태 검사, 시간 offset·음수·분모 0·NULL 검증
- 학습: 기본 전체 선택과 유효성 별개. 기존 ID의 편집 정의도 이름·허용 연산·입력 타입·fit 조건 검사; 잘못된 입력은 요청 차단
- 추천 룰: 생성 초안으로 가져오기·추천 검토 제외. 가져온 정의의 수동 편집 보존, 실험 화면에서 활성 선택 없음. 생성 요청에는 legacy enabled 값 제외; 운영 버전에는 영향 없음
- 조회 갱신: 실패 시 이전 내용·오류 함께 표시, 초안 유지. 서버 정상 판정·서비스 성공으로 대체 금지
- 확인창: 변경 대상/현재 버전/평가 근거 표시, ID가 같아도 구성·연결 평가·Rule·모델·snapshot 내용 변경 시 승인/확인창 초기화. 미연결 capability=false는 실행 버튼 비활성
- 시험 어댑터: pending/empty/failed/retry/stale 경로·잘못된 입력·초안 이탈·미연결 요청 0회 검증. 제품 화면에 시험 제어 입력 없음


## 파생변수 수식 계약

- 입력: 검색한 속성·함수 삽입 또는 직접 수식 입력; 키보드 자동완성·기존 괄호/입력 보존
- 참조: `[process.END_TIME]`, `[transaction.STATUS]`, `[F05]`, `[custom-1]`; ID 대소문자 정규화, 표시명과 식별자 분리
- 문법: 숫자·문자열·boolean·NULL 상수, 사칙연산·비교·괄호·허용 함수; 임의 JavaScript/Python 실행 금지
- 함수: if / log1p / is_missing / duration_ms / duration_s / hour / month / weekday / weekend / train_median / frequency_encode / robust_z
- 타입·단위: 수식으로 자동 추론; 시각 차이는 duration_ms/s 함수, boolean 결과의 모델 지원 별도 검사
- 검증: 알 수 없는 속성·삭제 참조·타입/단위 불일치·순환·32단계 중첩·2048자/512토큰 제한; 오류 시 초안 보존·요청 차단
- 시각: offset/Z 포함 ISO 입력; 시간·월·요일 추출의 UTC/Asia/Seoul 선택, 유효 달력·음수 처리시간 검사
- 미리보기: 사용자 입력만 사용, 분모 0/비유한 수치/음수 log1p 계산 보류, NULL 유지/필수 입력 결측 보류
- 학습 통계 함수 및 전이 의존성: fitRequired 표시·fit 미제공 시 계산/학습 보류; 검증 입력을 학습 통계로 간주 금지
- payload: 기존 featureEdits 보존 + expression + compiled(version=formula-v1, AST, outputType, unit, inputIds, fitRequired); 실제 서버 연결 전 제안 계약
- 서버 구현 시 수식 원문 재검증·AST 재컴파일 필수; 브라우저 전달 AST/유효성만 신뢰 금지
- 삭제: 수식 안의 삭제 참조는 그대로 보존하여 오류 표시·재선택 요구; 다른 수식의 자동 재작성 금지
