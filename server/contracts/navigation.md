# WebUI 탐색 규약

- 관련 작업: #20 WebUI 레이아웃 구조 개편, #100 프론트 기반(와이어프레임 #93 구조로 경로 개편, 사용자 11/03 승인), #105 이상 분석 판정 목록·상세, 사건 보기([incidents-ui.md](incidents-ui.md))
- 화면 기준: `research/wireframes/mlops-workflow.html`(#93)
- 인증·오류 응답: [auth.md](auth.md)
- 권한: `user` 일반 사용자 / `admin` 관리자
- 코드 기준: `frontend/src/app/routes.ts`(경로·메뉴·옛 주소 표), `frontend/src/app/appRoutes.tsx`(라우트 표)

## 화면 경로

| 경로 | 화면 | 접근 |
|---|---|---|
| `/login` | 로그인 | 미인증 |
| `/operations` | 운영 현황 | user / admin |
| `/analysis` | 이상 분석 › 판정 목록 | user / admin |
| `/analysis/d/:decisionId` | 이상 분석 › PROCESS 판정 상세(판정 ID 또는 최종 판정 전 detect 작업 ID, [analysis-ui.md](analysis-ui.md)) | user / admin |
| `/analysis/incidents` | 이상 분석 › 사건 보기(사이드바 별도 구역 "사건", 실험 기능 층 C, [incidents-ui.md](incidents-ui.md)) | user / admin |
| `/analysis/incidents/:incidentId` | 사건 상세(아무 사건 ID, 병합·뒤 구간 ID로 열면 흐름 ID 주소로 replace) | user / admin |
| `/settings` | 설정 | user / admin |
| `/detection/operations` | 탐지 설정 › 운영 설정 | admin |
| `/detection/operations/jobs` | 운영 설정 › 작업 목록([operations-ui.md](operations-ui.md) 작업 목록·작업 상세) | admin |
| `/detection/operations/jobs/:jobId` | 작업 상세 | admin |
| `/detection/operations/replace` | 모델 교체(전체 페이지 단계 흐름) | admin |
| `/detection/rules` | 탐지 설정 › 룰 설정([rules-ui.md](rules-ui.md)) | admin |
| `/detection/rules/candidates` | 룰 설정 › 규칙 추천 목록(쿼리 `interface`, [rule-candidates-ui.md](rule-candidates-ui.md)) | admin |
| `/detection/rules/candidates/:candidateId` | 규칙 추천 후보 상세 | admin |
| `/detection/rules/protocols` | 룰 설정 › 프로토콜 설정 목록(정상 프로토콜 = 인터페이스별 허용 경로, [protocol-settings-ui.md](protocol-settings-ui.md)) | admin |
| `/detection/rules/protocols/:interfaceId` | 프로토콜 설정 인터페이스 상세 | admin |
| `/detection/rules/response-codes` | 룰 설정 › 응답 코드 목록(사전 메시지 + 거래 실패 규칙 코드별 위험도, [response-codes-ui.md](response-codes-ui.md)) | admin |
| `/detection/rules/response-codes/:code` | 응답 코드 상세(코드는 퍼센트 인코딩) | admin |
| `/detection/models` | 탐지 설정 › 머신러닝 모델(버전 목록 + 같은 화면 새 학습, [models-ui.md](models-ui.md)) | admin |
| `/detection/models/:versionId` | 모델 버전 상세([models-ui.md](models-ui.md)) | admin |
| `/detection/evaluations` | 탐지 설정 › 평가 결과(비교 평가 목록 + 같은 화면 새 비교 평가) | admin |
| `/detection/evaluations/:evaluationId` | 평가 상세 | admin |

- 상단 메뉴: 운영 현황 / 이상 분석 / 탐지 설정(admin만 표시). 우측 알림 패널(미확인 배지)·설정·로그아웃
- 기본 경로 `/`: 운영 현황. 탐지 설정 첫 진입 `/detection`: 운영 설정
- 탐지 설정 사이드바 4메뉴: 운영 설정 · 룰 설정 · 머신러닝 모델 · 평가 결과. 상세·모델 교체 화면도 해당 메뉴를 현재 메뉴로 표시
- 탐지 설정 하위 화면(상세·모델 교체) 상단: 뒤로가기 아이콘(이름 `뒤로 · <메뉴>`). 탐지 설정 안에서 넘겨받은 `state.returnTo`가 있으면 그 주소, 없거나 탐지 설정 밖·외부·자기 자신이면 해당 메뉴 첫 화면
- 일반 사용자의 탐지 설정 직접 진입: 관리자 권한 안내(메뉴 숨김과 서버 API 권한 검사는 각각 적용)

## 주소에 반영하는 선택값

| 화면 | 쿼리 | 값(첫 값이 기본) |
|---|---|---|
| 운영 현황 | `tab` | `overview` 개요 / `collect` 수집 |
| 설정 | `section` | `notifications` / `integrations` / `display` / `profile`(알림 설정 · 연동 관리 · 화면/위젯 · 사용자 환경). `notifications`·`display`는 계정별 서버 저장(바꾸면 바로 저장, [notifications.md](notifications.md)), 그 밖은 `저장 미지원` |
| 머신러닝 모델 | `tab` | `active` 현재 운영 버전 / `all` 전체 버전 |
| 머신러닝 모델 | `step` | 새 학습 `1` 기간 / `2` 입력 변수 / `3` 모델·파라미터 |
| 머신러닝 모델 | `job` | 접수한 학습 작업 ID(없으면 생략). 영역 맨 위에 진행 상태를 표시 |
| 작업 목록 | `status` | `all` 전체 / `running` 진행 중(대기·실행 중) / `done` 완료 / `failed` 실패 |
| 작업 목록 | `kind` | `all` 전체 종류 / `train` 학습 / `evaluate` 평가 / `evaluation_set` 평가 세트 / `compare` 비교 평가 / `recommend_thresholds` AI 규칙 추천 / `recommend_relations` 규칙 추천 |
| 평가 결과 | `tab` | `current` 현재 운영 모델 기준 / `all` 전체 평가 |
| 평가 결과 | `step` | 새 비교 평가 `1` 평가 세트 / `2` 비교 모델 / `3` 요청 |
| 모델 교체 | `step` | `1` 버전 선택 / `2` 비교 확인 / `3` 승인·요청 |
| 이상 분석 | `category` | `all` / `anomaly` / `anomaly_sign` / `normal` / `in_progress` / `undetermined` / `failed_replaced`(판정 결과별 분류: 전체 / 이상 / 이상 징후 / 정상 / 판정 중·중간 판정 / 정상 여부 미확인 / 판정 실패·대체) |
| 이상 분석 | `q`·`signal`·`completeness`·`sort`·`page`·`pageSize` | 검색어 / 이상 신호 `all`·`yes`·`no`·`unknown` / 검사 완료 `all`·`complete`·`partial`·`unavailable` / `newest`·`oldest` / 1~ / `10`·`5`·`20`. 판정 상세 주소도 같은 쿼리 유지(목록 복귀) |
| 사건 보기 | `period`·`all`·`cursor` | `all` 전체 / `24h` / `7d` / `30d`(최근 24시간·7일·30일) · `all=1` 모두 보기(없으면 거래 2건 이상만) · 직전 응답 커서(없으면 첫 쪽). 사건 상세 주소도 같은 쿼리 유지(목록 복귀) |

- 탐지 설정 화면은 모두 내용이 있다(작업 목록·작업 상세 포함, [operations-ui.md](operations-ui.md)). 운영 현황·이상 분석·탐지 설정은 보이는 제목 줄 없음(ui.md 화면 제목). 목록 탭·작업 단계 화면은 각 하위 Issue에서 이 주소 규칙대로 붙임(도구 `hooks/useUrlState.ts`)
- 미지정 값은 기본값(주소 그대로), 잘못된 값은 기본값·가까운 유효 단계로 replace 정규화. 기타 쿼리·전달 state 보존. 앞 단계가 끝나지 않은 단계 주소도 가까운 유효 단계로 정규화(새로고침하면 메모리 초안이 없어 1단계)
- 목록 아래 같은 화면 작업 영역: 단계 이동(이전·다음, 새 학습 단계 표시)은 Link push → 브라우저 뒤로·앞으로 가기가 단계와 일치. `step`이 주소에 있으면 작업 영역을 화면에 보이게 스크롤, 목록 첫 진입은 스크롤 없음
- URL 탭: 밑줄형 Link·`aria-current=page`(로컬 Tabs ARIA 금지). 사이드바 항목도 Link·`aria-current=page`

## 옛 주소 넘김(replace)

| 옛 주소 | 새 주소 |
|---|---|
| `/notifications`(모든 쿼리) | `/analysis` |
| `/analysis/process-decisions`(모든 쿼리) | `/analysis`(저장된 탐지 판정 화면을 이상 분석에 합침, #105) |
| `/detection`, `/detection/pipeline`, `/detection/versions` | `/detection/operations` |
| `/detection/create?tab=rules` | `/detection/rules` |
| `/detection/create`(그 외 tab) | `/detection/models` |
| `/detection/evaluation` | `/detection/evaluations` |
| `/detection/collection` | `/operations?tab=collect` |

- 폐지 화면: `/notifications` 알림 전체 페이지(알림은 상단 패널), `/detection/pipeline` 학습·탐지 운영, 탐지 관리 실험·운영 그룹(생성·평가·운영 버전·수집), `/analysis/process-decisions` 저장된 탐지 판정(#105)
- 로그인 복귀 주소의 옛 주소도 같은 표로 바꿔 허용

## 상태 책임

- TanStack Query: 서버 조회·캐시. 공통 훅 `useAccountQuery`(`frontend/src/lib/query.ts`)
  - key에 계정 ID 포함, queryFn에 AbortSignal 전달, 응답 시점에 토큰이 바뀌었으면 결과 폐기
  - 자동 재시도 없음(404 포함), 창 포커스·재연결·주기 재조회 없음
  - 주기 조회는 진행 중 작업만 5초(`RUNNING_POLL_MS`, 예: 입력 실행 running, 설명 작업 queued·running). 끝나면 멈춤
  - 실시간 SSE(이상 이벤트·저장 판정)는 스트림 하나로 받고 캐시에 병합 또는 해당 조회 무효화, 끊기면 재연결(주기 조회 아님). 이상 분석은 목록 응답 커서 다음부터 받아 이벤트가 올 때만 목록·건수를 묶어 다시 조회(analysis-ui.md)
  - 서버 변경은 Mutation. 실패를 성공으로 바꾸지 않고 최신 상태를 다시 조회
- URL: 화면·탭·확정 필터·페이지·작업 단계
- Zustand: 화면 간 공유가 필요한 편집 초안만(`stores/draftStore.ts`, pathname별)
- 지역 상태: 입력 중 값·팝업·메뉴 열림
- 브라우저 저장소: 넓게 보기 같은 화면 편의 설정만. 서버 상태·초안·토큰 이외 비밀 금지
- 계정별 서버 저장(`/api/me/settings`): 알림 설정·운영 현황 위젯 배치. 알림 읽음 상태는 `/api/notifications/read`(notifications.md)
- URL 제외: 토큰·비밀번호·BODY·편집 초안
- 로그아웃·인증 만료·계정 변경: 사용자별 캐시·공유 초안·알림 정리
- 다른 탭의 로그인·로그아웃: 저장소 변경 감지 후 현재 사용자 재확인·이전 조회 취소
- 미저장 편집: 내부 탭·단계 이동 시 유지; 편집 영역 이탈 시 `변경 사항 저장 후 이동`(primary) / `버리고 이동`(outline) 두 버튼; Escape·외부 클릭은 이동 취소; 브라우저 새로고침·닫기 경고
- 저장 후 이동: 현재 세션의 메모리 초안 보존, 서버 영구 저장·운영 적용과 별개; dirty 및 beforeunload 보호 유지
- 초안 보존 이동: 같은 세션의 다른 페이지로 이동해도 pathname별 초안 dirty 유지. 원래 화면 복귀 시 편집 값 복원, 다른 화면에 머무는 동안도 전체 초안의 beforeunload 보호 유지
- 로그아웃·계정 변경: 초안 보존 이동과 별도. 로그아웃 확인에는 보존 선택 없음, 세션 종료 시 전체 초안/캐시 정리
- 모델 교체·복원 흐름 이탈: 확인창 종료·승인 초기화; 재진입 시 승인 재확인. 실제 구성·평가 근거 변경 시에도 승인 초기화(기존 운영 적용·복원 규칙 승계)
- 고정 평가 세트: 서버 ID·revision 기준 선택; 로컬 준비 초안과 실행 가능 세트 구분, 조건 자유 입력으로 서버 세트 대체 금지(기존 평가 규칙 승계)
- 설정: 독립 페이지; 복귀 버튼 없이 상단 메뉴로 이동(사용자 11/05)
- 로그인 복귀 주소: 앱 내부의 허용 경로만 사용; 외부 URL·프로토콜 상대 URL 금지
- 브라우저 재실행 후 편집 초안 복원: 현재 범위 제외

## 웹 서버 연결

- 프론트의 직접 URL 진입·새로고침: HTML 탐색 요청에 SPA `index.html` fallback 필요
- `/api/*` 및 정적 파일: SPA fallback 제외; 해당 API 응답·파일 404 유지
- 관리자 화면 숨김과 서버 API 권한 검사 각각 적용
- 개발 프론트: API 주소 `VITE_API_BASE_URL` 사용; 배포 환경은 동일 출처 `/api` 프록시 또는 별도 API 주소 설정
- CD/웹 서버 담당: 하위 경로 직접 진입·새로고침·API 401/403 유지 여부 확인
