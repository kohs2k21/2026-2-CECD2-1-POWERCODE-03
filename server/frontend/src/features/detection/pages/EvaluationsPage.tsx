import { ScreenTitle } from "@/components/layout/PageHeader";
import { CompareArea } from "../evaluations/CompareArea";
import { ComparisonsCard } from "../evaluations/EvaluationList";

/** 탐지 설정 › 평가 결과: 비교 평가 목록(tab=current|all) + 같은 화면 새 비교 평가(step=1..3). 주소 규칙은 navigation.md. */
export const EvaluationsPage = () => (
  <div className="grid gap-6">
    <ScreenTitle>평가 결과</ScreenTitle>
    <ComparisonsCard />
    <CompareArea />
  </div>
);
