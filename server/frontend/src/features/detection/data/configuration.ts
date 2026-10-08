import type {
  DetectionData,
  EvaluationResult,
  ModelArtifact,
  RuleVersion,
  VersionBundle,
} from "./types";
import { validateRule } from "./createDraft";

export type CompositionInput = {
  name: string;
  modelArtifactId: string;
  ruleVersionIds: string[];
  explanationVersion: string;
};
// Exact content identity, rather than a lossy hash or mutable algorithm/rule ID.
export const configurationFingerprint = (
  artifact: ModelArtifact,
  ruleVersions: RuleVersion[],
  explanationVersion: string | null,
) =>
  JSON.stringify({
    artifact: {
      id: artifact.id,
      version: artifact.version,
      modelId: artifact.modelId,
      featureIds: artifact.featureIds,
      featureVersion: artifact.featureVersion,
      preprocessingVersion: artifact.preprocessingVersion,
      fitVersion: artifact.fitVersion,
      snapshotId: artifact.snapshotId,
    },
    ruleVersions: [...ruleVersions]
      .sort((a, b) => a.id.localeCompare(b.id))
      .map((rule) => ({
        id: rule.id,
        version: rule.version,
        rules: [...rule.rules].sort((a, b) => a.id.localeCompare(b.id)),
      })),
    explanationVersion,
  });
export const compositionErrors = (
  input: CompositionInput,
  data: DetectionData,
) => {
  const errors: string[] = [];
  if (!input.name.trim() || input.name.trim().length > 80)
    errors.push("구성 이름은 1~80자로 입력해 주세요.");
  const artifact = data.modelArtifacts.find(
    (item) => item.id === input.modelArtifactId,
  );
  if (!artifact || artifact.state !== "succeeded")
    errors.push("생성 완료한 모델 산출물을 선택해 주세요.");
  if (
    new Set(input.ruleVersionIds).size !== input.ruleVersionIds.length ||
    input.ruleVersionIds.some(
      (id) =>
        !data.ruleVersions.some(
          (rule) => rule.id === id && rule.state === "succeeded",
        ),
    )
  )
    errors.push("생성 완료한 룰 버전을 선택해 주세요.");
  const ruleIds = input.ruleVersionIds.flatMap(
    (id) =>
      data.ruleVersions
        .find((version) => version.id === id)
        ?.rules.map((rule) => rule.id) ?? [],
  );
  if (new Set(ruleIds).size !== ruleIds.length)
    errors.push("같은 룰의 여러 버전은 한 구성에 함께 선택할 수 없습니다.");
  if (
    input.ruleVersionIds.some((id) =>
      data.ruleVersions
        .find((version) => version.id === id)
        ?.rules.some(
          (rule) => validateRule({ ...rule, enabled: false }).length > 0,
        ),
    )
  )
    errors.push("선택한 룰 버전의 정의가 유효하지 않습니다.");
  return errors;
};
export const composePreview = (
  input: CompositionInput,
  data: DetectionData,
): VersionBundle | null => {
  if (compositionErrors(input, data).length) return null;
  const artifact = data.modelArtifacts.find(
    (item) => item.id === input.modelArtifactId,
  )!;
  const versions = input.ruleVersionIds.map((id) =>
    data.ruleVersions.find((rule) => rule.id === id)!,
  );
  return {
    id: "composition-preview",
    name: input.name.trim(),
    state: "draft",
    modelId: artifact.modelId,
    modelArtifactId: artifact.id,
    featureIds: [...artifact.featureIds],
    featureVersion: artifact.featureVersion,
    preprocessingVersion: artifact.preprocessingVersion,
    fitVersion: artifact.fitVersion,
    snapshotId: artifact.snapshotId,
    ruleVersionIds: [...input.ruleVersionIds],
    ruleIds: versions.flatMap((rule) =>
      rule.rules.map((definition) => definition.id),
    ),
    explanationVersion: input.explanationVersion.trim() || null,
    trained: true,
    compatible: artifact.compatible,
    artifactAvailable: artifact.available,
    evaluationId: null,
    appliedAt: null,
    configurationFingerprint: configurationFingerprint(
      artifact,
      versions,
      input.explanationVersion.trim() || null,
    ),
  };
};
export const currentConfigurationFingerprint = (
  bundle: VersionBundle,
  data: DetectionData,
): string | null => {
  const artifact = data.modelArtifacts.find(
    (item) => item.id === bundle.modelArtifactId && item.state === "succeeded",
  );
  const rules = bundle.ruleVersionIds.map((id) =>
    data.ruleVersions.find(
      (item) => item.id === id && item.state === "succeeded",
    ),
  );
  if (
    !artifact ||
    rules.some((item) => !item) ||
    new Set(bundle.ruleVersionIds).size !== bundle.ruleVersionIds.length
  )
    return null;
  const definitions = rules.flatMap((item) => item!.rules);
  if (
    new Set(definitions.map((rule) => rule.id)).size !== definitions.length ||
    definitions.some(
      (rule) => validateRule({ ...rule, enabled: false }).length > 0,
    )
  )
    return null;
  if (
    bundle.modelId !== artifact.modelId ||
    JSON.stringify(bundle.featureIds) !== JSON.stringify(artifact.featureIds) ||
    bundle.featureVersion !== artifact.featureVersion ||
    bundle.fitVersion !== artifact.fitVersion ||
    bundle.preprocessingVersion !== artifact.preprocessingVersion ||
    bundle.snapshotId !== artifact.snapshotId ||
    JSON.stringify([...bundle.ruleIds].sort()) !==
      JSON.stringify(
        rules.flatMap((item) => item!.rules.map((rule) => rule.id)).sort(),
      )
  )
    return null;
  return configurationFingerprint(
    artifact,
    rules as RuleVersion[],
    bundle.explanationVersion,
  );
};
export const evaluationMatchesConfiguration = (
  result: EvaluationResult | undefined,
  bundle: VersionBundle,
  data: DetectionData,
) => {
  const current = currentConfigurationFingerprint(bundle, data);
  return Boolean(
    current &&
    bundle.configurationFingerprint === current &&
    result &&
    result.candidateId === bundle.id &&
    result.configurationFingerprint === current,
  );
};
