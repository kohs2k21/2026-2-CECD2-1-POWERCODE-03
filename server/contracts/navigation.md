# WebUI 탐색 규약

- 관련 작업: #20 WebUI 레이아웃 구조 개편
- 인증·오류 응답: [auth.md](auth.md)
- 권한: `user` 일반 사용자 / `admin` 관리자

## 화면 경로

| 경로 | 화면 | 접근 |
|---|---|---|
| `/login` | 로그인 | 미인증 |
| `/operations` | 운영 현황 | user / admin |
| `/analysis` | 이상 분석 | user / admin |
| `/settings` | 설정 | user / admin |
| `/notifications` | 알림 전체 보기 | user / admin |
| `/detection/create` | 모델·룰 생성 | admin |
| `/detection/evaluation` | 성능 평가·비교 | admin |
| `/detection/versions` | 운영 버전 관리 | admin |
| `/detection/collection` | 데이터 수집 현황 | admin |

- 기본 경로 `/`: 운영 현황
- 탐지 관리 첫 진입: 모델·룰 생성
- 생성 `tab`: `dataset` 데이터셋 / `features` 피처 / `training` 모델 학습 / `rules` 룰 생성·편집
- 평가 `tab`: `candidates` 생성된 후보 평가 / `composition` 모델·룰 조합 검토
- 운영 버전 `tab`: `configuration` 운영 구성 / `application` 적용·복원 / `history` 버전 이력
- 수집 `tab`: `status` 수집 상태 / `fields` 원본 필드 / `history` 수집 이력 / `storage` 저장 공간
- 알림 `filter`: all / operations / tasks; 권한·데이터 경계: [notifications.md](notifications.md)
- 공통 내부 탐색: 밑줄형 Link·`aria-current=page`; 제목 아래 간격 32px, 선택한 내용만 표시
- 명시한 유효 `tab` 우선; 미지정·잘못된 tab은 해당 화면의 기본·기존 상세 URL에 맞게 replace 정규화, 기타 쿼리·전달 state 보존
- 기존 상세 URL: 평가 result→candidates / selection=composition→composition; 운영 history→history, candidate·rollback→application; 수집 history→history, field→fields
- 이전 `data-features` URL: feature/kind/featureQ 존재 시 features, 그 외 dataset으로 replace 전환; 나머지 필터 유지
- 새 관리자 화면: 목록·상세·입력·검증·확인 흐름 포함; 실제 미연결 작업은 detection-ui.md의 capability 계약으로 구분
- 설정: 독립 페이지; 종료 시 진입 전 경로·탭·필터 복귀
- 로그인 복귀 주소: 앱 내부의 허용 경로만 사용; 외부 URL·프로토콜 상대 URL 금지

## 상태 책임

- TanStack Query: 현재 사용자 등 서버 조회·캐시
- URL: 화면·탭·확정 필터·페이지
- Zustand: 화면 간 공유가 필요한 편집 초안
- 지역 상태: 입력 중 값·팝업·메뉴 열림
- URL 제외: 토큰·비밀번호·BODY·편집 초안
- 로그아웃·인증 만료·계정 변경: 사용자별 캐시·공유 초안 정리
- 다른 탭의 로그인·로그아웃: 저장소 변경 감지 후 현재 사용자 재확인·이전 조회 취소
- 미저장 편집: 내부 탭 이동 시 유지; 편집 영역 이탈 시 `변경 사항 저장 후 이동` / `버리고 이동` 두 버튼; Escape·외부 클릭은 이동 취소; 브라우저 새로고침·닫기 경고
- 저장 후 이동: 현재 세션의 메모리 초안 보존, 서버 영구 저장·운영 적용과 별개; dirty 및 beforeunload 보호 유지.
- 화면 이탈 버튼: 저장 후 이동 primary(검은 배경), 버리고 이동 outline
- 운영 적용·복원 탭 이탈: 확인창 종료·승인 초기화; 재진입 시 승인 재확인. 실제 구성·평가 근거 변경 시에도 승인 초기화
- 초안 보존 이동: 같은 세션의 다른 페이지로 이동해도 pathname별 초안 dirty 유지. 원래 화면 복귀 시 편집 값 복원, 다른 화면에 머무는 동안도 전체 초안의 beforeunload 보호 유지
- 로그아웃·계정 변경: 초안 보존 이동과 별도. 로그아웃 확인에는 보존 선택 없음, 세션 종료 시 전체 초안/캐시 정리
- 운영 조합 미리보기→평가: Link state의 CompositionInput 전달, 평가 화면에서 조회 산출물/룰 버전과 재검증. 기존 평가 초안이 있으면 명시적 전달 구성 사용으로 기존 편집 보호
- 초안 보호: 실제 데이터·피처·학습·룰 및 평가 설정 편집에 pathname별 초안 적용, 내부 선택 ID별 편집 값 구분
- 브라우저 재실행 후 편집 초안 복원: 현재 범위 제외

## 웹 서버 연결

- 프론트의 직접 URL 진입·새로고침: HTML 탐색 요청에 SPA `index.html` fallback 필요
- `/api/*` 및 정적 파일: SPA fallback 제외; 해당 API 응답·파일 404 유지
- 관리자 화면 숨김과 서버 API 권한 검사 각각 적용
- 개발 프론트: API 주소 `VITE_API_BASE_URL` 사용; 배포 환경은 동일 출처 `/api` 프록시 또는 별도 API 주소 설정
- CD/웹 서버 담당: 하위 경로 직접 진입·새로고침·API 401/403 유지 여부 확인
