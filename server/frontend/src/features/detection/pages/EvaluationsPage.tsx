import { ScreenTitle } from "@/components/layout/PageHeader";

/**
 * 탐지 설정 › 평가 결과(골격: 숨긴 화면 제목만). 비교 평가 목록(tab=current|all)과 같은 화면 새 비교 평가(step=1..3)는
 * 하위 Issue(평가 결과 + 새 비교 평가)에서 API와 함께.
 */
export const EvaluationsPage = () => (
  <div className="grid gap-6">
    <ScreenTitle>평가 결과</ScreenTitle>
  </div>
);
