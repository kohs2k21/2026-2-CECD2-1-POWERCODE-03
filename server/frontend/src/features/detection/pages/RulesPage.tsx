import { ScreenTitle } from "@/components/layout/PageHeader";

/** 탐지 설정 › 룰 설정(골격: 숨긴 화면 제목만). 이상 규칙·이상 징후 규칙·변경 기록은 하위 Issue(룰 설정)에서 API와 함께. */
export const RulesPage = () => (
  <div className="grid gap-6">
    <ScreenTitle>룰 설정</ScreenTitle>
  </div>
);
