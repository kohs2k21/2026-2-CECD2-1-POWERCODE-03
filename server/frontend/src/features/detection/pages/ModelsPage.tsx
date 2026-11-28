import { ScreenTitle } from "@/components/layout/PageHeader";
import { modelFrames } from "../models/modelFrames";
import { TrainArea } from "../models/TrainArea";
import { useCapabilities } from "../models/useModels";
import { FrameVersionsMore, FrameVersionsSection, hasUnframedVersions, useFrameVersions } from "../models/VersionList";

/**
 * 탐지 설정 › 머신러닝 모델: 틀 구역 2개(행 단위 모델 → 시계열 모델, 구역마다 현재 운영·버전 목록·[새 학습]) + 카탈로그에 없는 키의 버전이 있으면 `틀 미확인` 묶음
 * + 전체 버전 `더 보기` 하나 + 같은 화면 새 학습(frame·step=1..3). 주소 규칙은 navigation.md, 화면 계약은 models-ui.md.
 */
export const ModelsPage = () => {
  const versions = useFrameVersions();
  const catalog = useCapabilities().data?.methodCatalog.methods;
  return (
    <div className="grid gap-6">
      <ScreenTitle>머신러닝 모델</ScreenTitle>
      {modelFrames.map((frame) => <FrameVersionsSection key={frame} frame={frame} versions={versions} />)}
      {hasUnframedVersions(catalog, versions) && <FrameVersionsSection frame={null} versions={versions} />}
      <FrameVersionsMore versions={versions} />
      <TrainArea />
    </div>
  );
};
