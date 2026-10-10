import type {
  DetectionData,
  EvaluationCondition,
  EvaluationResult,
  EvaluationSet,
  VersionBundle,
} from "./types";
import {
  conditionFromEvaluationSet,
  evaluationSetErrors,
} from "./evaluationPreparation";
import {
  currentConfigurationFingerprint,
  evaluationMatchesConfiguration,
} from "./configuration";

export const exactSetResult = (
  result: EvaluationResult,
  bundle: VersionBundle,
  set: EvaluationSet,
  data: DetectionData,
) => {
  const expected = conditionFromEvaluationSet(set);
  return (
    evaluationMatchesConfiguration(result, bundle, data) &&
    result.condition.evaluationSetId === set.id &&
    result.condition.evaluationSetRevision === set.revision &&
    (result.evaluationSetId == null || result.evaluationSetId === set.id) &&
    (result.evaluationSetRevision == null ||
      result.evaluationSetRevision === set.revision) &&
    Object.entries(expected).every(
      ([key, value]) =>
        result.condition[key as keyof EvaluationCondition] === value,
    )
  );
};
export const comparisonResult = (
  bundle: VersionBundle,
  set: EvaluationSet | undefined,
  data: DetectionData,
) => {
  if (!set || evaluationSetErrors(set, data).length) return undefined;
  return data.evaluations
    .filter(
      (result) =>
        result.state === "succeeded" &&
        exactSetResult(result, bundle, set, data),
    )
    .sort((left, right) => {
      const time = (result: EvaluationResult) => {
        const parsed = Date.parse(
          result.requestedAt ?? result.completedAt ?? "",
        );
        return Number.isFinite(parsed) ? parsed : -Infinity;
      };
      return time(right) - time(left) || left.id.localeCompare(right.id);
    })[0];
};
export const comparisonTargetErrors = (
  bundle: VersionBundle,
  data: DetectionData,
) => {
  if (bundle.state === "draft") return ["생성 완료한 고정 후보가 필요합니다."];
  const fingerprint = currentConfigurationFingerprint(bundle, data);
  return !fingerprint || fingerprint !== bundle.configurationFingerprint
    ? ["현재 구성 내용과 고정 fingerprint가 일치하지 않습니다."]
    : [];
};
