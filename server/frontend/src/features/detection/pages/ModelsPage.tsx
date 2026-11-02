import { ScreenTitle } from "@/components/layout/PageHeader";

/**
 * 탐지 설정 › 머신러닝 모델(골격: 숨긴 화면 제목만). 버전 목록(tab=active|all)과 같은 화면 새 학습(step=1..3)은
 * 하위 Issue(머신러닝 모델 + 새 학습)에서 API와 함께. 주소 규칙은 navigation.md, 도구는 hooks/useUrlState.
 */
export const ModelsPage = () => (
  <div className="grid gap-6">
    <ScreenTitle>머신러닝 모델</ScreenTitle>
  </div>
);
