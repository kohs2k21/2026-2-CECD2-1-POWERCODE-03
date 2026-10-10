# 알림·진행 작업 UI 규약

- 관련 작업: #20 WebUI 레이아웃 구조 개편
- 진입: 우상단 알림 버튼 → 작은 확인 창 → 전체 보기 `/notifications`
- 분류 `filter`: `all` 전체 / `operations` 운영 / `tasks` 작업; 일반 사용자 tasks 진입은 operations로 정규화
- 일반 사용자: 운영 이벤트 조회; 관리자: 운영 이벤트 + 학습·평가 작업 조회
- 작은 창: 운영/작업 분리, 영역별 최근 최대5건, 목록 내부 스크롤·닫기·전체 보기; 동일 데이터를 전체 페이지와 공유
- 상세 이동: 운영 이벤트 `/analysis?category=<severity>&event=<eventId>`; 학습 `/detection/create?tab=training&job=<id>`; 평가 `/detection/evaluation?tab=candidates&candidate=<id>&result=<id>`
- 직접 URL·필터·설정 복귀 지원; 초안 편집 영역 이탈 시 기존 저장/폐기 확인 적용

## 데이터·상태 책임

- 운영 알림: 인증된 앱 공통 RealtimeAnomalyProvider의 기존 이상 SSE 이벤트 사용, 분석/팝업/전체 페이지별 중복 구독 금지
- 이벤트 보관: 현재 세션의 최근50건, 기존 중복 이벤트 제거·재연결·취소 정책 유지; 영속 이력·재생 API 별도
- 페이지 이동: 수신 이벤트 유지; 로그아웃·계정/역할·인증 변경: 이전 이벤트·캐시·연결 정리, 늦은 응답 표시 금지
- 작업 조회: TanStack Query 계정별 `[detection-workbench, userId]`; 관리자에서만 실행
- 갱신: 알림 공통 observer의 accepted/queued/running 작업 존재 시5초 polling, 실행 작업 없으면 정지; 수동 다시 조회 지원
- 접수·대기·실행·성공·실패 구분; 큐 접수·관측 저장만으로 판정 완료 표시 금지
- 진행률: TrainingJob.progress의 실제0~100 값만 사용, NULL/범위오류/미제공은 미확인; 평가 진행률 임의 추가·시간 보간 금지
- 오류: 조회 실패·연결 끊김·이벤트 형식 오류 표시, 마지막 조회 내용은 과거 조회임을 표시; 실패의 정상/빈 성공 대체 금지
- 현재 API: 기존 인증 및 이상 SSE 연결; 학습/평가 조회·요청은 기존 detection-ui.md 미연결 계약 유지
- 개발 데이터: 기존 DEV 전용 detection read fixture 재사용, 프로덕션 fixture fallback·자동 학습·가짜 이벤트 생성 없음
- 이번 제외: 영속 읽음 상태·보관/삭제·외부 채널·작업 취소/재시도·새 이벤트/작업 서버 API
- 읽음/미확인 배지: 서버/읽음 상태 계약 없이 임의 unread 표시 금지

## 공통 표현

- 작은 창: 공통 Modal·DialogFooter, Escape/외부 클릭/닫기 및 원래 버튼 포커스 복귀
- 화면 이동 시 작은 창 닫힘; 전체 페이지 내부 필터는 밑줄형 URL Link
- 새 백엔드 연결 시: 관측 ID·판정 ID·구성 버전·발생/처리 시각·완료/부분/판정불가 계약 별도 확정
- UI 흐름 검증과 실제 학습·평가·운영 판정 연결 완료 구분
