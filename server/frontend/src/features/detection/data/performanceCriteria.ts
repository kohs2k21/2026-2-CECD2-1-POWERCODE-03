import { exactSetResult } from "./evaluationComparison";
import { evaluationSetErrors } from "./evaluationPreparation";
import type { DetectionData, VersionBundle } from "./types";

export const performanceAssessment = (
  bundle: VersionBundle,
  data: DetectionData,
) => {
  const result = data.evaluations.find(
    (item) => item.id === bundle.evaluationId,
  );
  const set = data.evaluationSets?.find(
    (item) =>
      item.id === result?.condition.evaluationSetId &&
      item.revision === result?.condition.evaluationSetRevision,
  );
  const spec = data.evaluationSpecs?.find(
    (item) => item.id === set?.specId && item.revision === set?.specRevision,
  );
  const criteria = spec?.acceptanceCriteria;
  const confirmed = Boolean(
    criteria?.length &&
    spec &&
    criteria.every(
      (item) =>
        spec.metrics.includes(item.metric) &&
        ["gte", "lte"].includes(item.operator) &&
        typeof item.source === "string" &&
        item.source.trim() &&
        Number.isFinite(item.value) &&
        item.value >= 0 &&
        (item.metric === "latencyMs" || item.value <= 1),
    ),
  );
  const reasons: string[] = [];
  if (!confirmed) reasons.push("합의된 성능기준이 미확정입니다.");
  if (
    !set ||
    evaluationSetErrors(set, data).length ||
    !result ||
    !exactSetResult(result, bundle, set, data)
  )
    reasons.push(
      "고정 평가 세트와 현재 구성에 일치하는 평가 근거가 필요합니다.",
    );
  if (result?.state !== "succeeded")
    reasons.push("평가 처리 성공이 확인되지 않았습니다.");
  if (confirmed && result) {
    for (const criterion of criteria!) {
      const value = result.metrics[criterion.metric];
      const valid =
        typeof value === "number" &&
        Number.isFinite(value) &&
        value >= 0 &&
        (criterion.metric === "latencyMs" || value <= 1);
      if (!valid)
        reasons.push(`${criterion.metric}: 기준을 판단할 측정값이 없습니다.`);
      else if (
        criterion.operator === "gte"
          ? value < criterion.value
          : value > criterion.value
      )
        reasons.push(
          `${criterion.metric}: 확정 성능기준을 충족하지 않았습니다.`,
        );
    }
  }
  if (set && set.purpose !== "test")
    reasons.push("test 최종 확인 평가가 필요합니다.");
  return {
    result,
    set,
    spec,
    confirmed,
    passed: reasons.length === 0,
    reasons,
  };
};

// Proposed server precondition, distinct from a security signature.
export const expectedVersionReview = (
  bundle: VersionBundle,
  data: DetectionData,
) => {
  const assessment = performanceAssessment(bundle, data);
  const { result, set, spec } = assessment;
  const split = data.evaluationSplits?.find(
    (item) =>
      item.id === set?.splitManifestId && item.revision === set?.splitRevision,
  );
  if (
    !assessment.passed ||
    !result ||
    !set ||
    !spec ||
    !split ||
    !bundle.configurationFingerprint
  )
    return null;
  return {
    evaluationId: result.id,
    configurationFingerprint: bundle.configurationFingerprint,
    evaluationSetId: set.id,
    evaluationSetRevision: set.revision,
    specId: spec.id,
    specRevision: spec.revision,
    splitManifestId: split.id,
    splitRevision: split.revision,
    reviewFingerprint: JSON.stringify({
      evaluation: result,
      evaluationSet: set,
      spec,
      split,
    }),
  };
};
