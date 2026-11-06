import { ScreenTitle } from "@/components/layout/PageHeader";

/** 모델 교체(골격: 숨긴 화면 제목만). 단계(step=1..3) 흐름은 하위 Issue(운영 설정 + 교체)에서. 상단 뒤로가기는 DetectionLayout. */
export const ReplacePage = () => (
  <div className="grid gap-6">
    <ScreenTitle>모델 교체</ScreenTitle>
  </div>
);
