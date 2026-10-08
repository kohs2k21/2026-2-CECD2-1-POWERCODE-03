Created: 2026-10-08T12:53:45+09:00
Updated: 2026-10-08T12:53:45+09:00
Author: frontend_lead
Status: current

# 공통 UI 사용

- 기본 부품: `frontend/src/components/ui/button.tsx`, `dialog.tsx`, `Modal.tsx`, `tooltip.tsx`, `feedback.tsx`
- 공통 토큰: `frontend/src/lib/tokens.css`; 기존 `--ink`, `--canvas` 등의 별칭 호환 유지
- 버튼: `variant="default|outline|ghost"`, `size="default|sm|icon|icon-sm"`; 기본 `type="button"`, 제출은 `type="submit"` 명시
- URL 탐색: `Button asChild` + React Router `Link/NavLink`; URL 탭에 로컬 Tabs ARIA 적용 금지
- 모달: `Modal`의 `isOpen/onOpenChange/title/description/size`; 확인창은 `Dialog`·`DialogContent`·`DialogTitle`·`DialogDescription` 조합. Radix 직접 import는 ui 내부만
- 닫기: Escape·외부 클릭은 기본 닫기; `onOpenChange(false)`를 취소로 처리. 기본 포커스 복귀, 미저장 보호의 폐기는 명시 버튼에서만 수행
- 조회 상태: `LoadingState`, `EmptyState`, `ErrorState title/onRetry/retryLabel`; 재조회 실패 시 이전 데이터 유지. 조회 오류는 인라인 표시·재시도, 중복 토스트 금지
- 아이콘 버튼: 접근 가능한 이름 필수. 전환: 앱 `MotionConfig` 및 개별 `useReducedMotion`; CSS는 reduced-motion 미디어쿼리
- 알림: `frontend/src/lib/notify.ts`; 앱 Toaster 1개. 성공 2.4초·오류 6초·로딩 명시 종료; 같은 원인은 같은 ID로 갱신

```tsx
<Button asChild variant="outline">
  <Link to="/analysis?q=delay">이상 분석</Link>
</Button>
<ErrorState title="조회에 실패했습니다" onRetry={refetch} />

notify.loading("저장 중", "save:settings");
// 실제 작업 성공 뒤 동일 ID로 교체
notify.success("저장 완료", "save:settings");
// 실패 시: notify.error("저장 실패", "save:settings");
```

- 세션 변경: AuthSessionProvider에서 `notify.clear()` 즉시 제거. 비동기 호출자는 취소·세션 검증 후 현재 계정의 결과만 알림; 이전 작업의 지연 완료 재발행 금지
- 현재 활성 알림: 동기 위젯 배치 조작만. 비활성 관리자·LLM 시뮬레이션 경로의 가짜 완료 처리는 재사용 대상 제외
- 차트: Recharts Tooltip 유지; 공통 일반 Tooltip과 구분. 신규 라이브러리 도입 없음
