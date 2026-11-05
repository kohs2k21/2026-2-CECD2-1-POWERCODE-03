import { ScreenTitle } from "@/components/layout/PageHeader";
import { TrainArea } from "../models/TrainArea";
import { VersionsCard } from "../models/VersionList";

/** 탐지 설정 › 머신러닝 모델: 버전 목록(tab=active|all) + 같은 화면 새 학습(step=1..3). 주소 규칙은 navigation.md, 화면 계약은 models-ui.md. */
export const ModelsPage = () => (
  <div className="grid gap-6">
    <ScreenTitle>머신러닝 모델</ScreenTitle>
    <VersionsCard />
    <TrainArea />
  </div>
);
