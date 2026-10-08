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
| `/detection/create` | 모델·룰 만들기 | admin |
| `/detection/evaluation` | 성능 평가·비교 | admin |
| `/detection/versions` | 운영 버전 관리 | admin |
| `/detection/collection` | 데이터 수집 현황 | admin |

- 기본 경로 `/`: 운영 현황
- 탐지 관리 첫 진입: 모델·룰 만들기
- 만들기 `tab`: `data-features` 데이터·피처 / `training` 모델 학습 / `rules` 룰 설정
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
- 미저장 편집: 내부 탭 이동 시 유지; 편집 영역 이탈 시 유지/폐기 선택; 브라우저 새로고침·닫기 경고
- 초안 보호: 실제 데이터·피처·학습·룰 및 평가 설정 편집에 pathname별 초안 적용, 내부 선택 ID별 편집 값 구분
- 브라우저 재실행 후 편집 초안 복원: 현재 범위 제외

## 웹 서버 연결

- 프론트의 직접 URL 진입·새로고침: HTML 탐색 요청에 SPA `index.html` fallback 필요
- `/api/*` 및 정적 파일: SPA fallback 제외; 해당 API 응답·파일 404 유지
- 관리자 화면 숨김과 서버 API 권한 검사 각각 적용
- 개발 프론트: API 주소 `VITE_API_BASE_URL` 사용; 배포 환경은 동일 출처 `/api` 프록시 또는 별도 API 주소 설정
- CD/웹 서버 담당: 하위 경로 직접 진입·새로고침·API 401/403 유지 여부 확인

