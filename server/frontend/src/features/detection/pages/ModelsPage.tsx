import { ScreenTitle } from "@/components/layout/PageHeader";
import { modelFrames } from "../models/modelFrames";
import { TrainArea } from "../models/TrainArea";
import { FrameVersionsSection, useFrameVersions } from "../models/VersionList";

/**
 * 탐지 설정 › 머신러닝 모델: 틀 구역 2개(행 단위 모델 → 시계열 모델, 구역마다 현재 운영·버전 목록·[새 학습]) + 같은 화면 새 학습(frame·step=1..3).
 * 주소 규칙은 navigation.md, 화면 계약은 models-ui.md.
 */
export const ModelsPage = () => {
  const versions = useFrameVersions();
  return (
    <div className="grid gap-6">
      <ScreenTitle>머신러닝 모델</ScreenTitle>
      {modelFrames.map((frame) => <FrameVersionsSection key={frame} frame={frame} versions={versions} />)}
      <TrainArea />
    </div>
  );
};
