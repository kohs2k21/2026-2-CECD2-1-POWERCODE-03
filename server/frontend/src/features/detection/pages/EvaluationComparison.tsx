import { Link, useLocation, useSearchParams } from "react-router-dom";
import { useDraftStore } from "../../../stores/draftStore";
import { ServiceAction } from "../components/ServiceAction";
import {
  jobStateLabels,
  valueText,
} from "../components/DetectionQueryBoundary";
import { currentConfigurationFingerprint } from "../data/configuration";
import {
  comparisonResult,
  comparisonTargetErrors,
} from "../data/evaluationComparison";
import {
  conditionFromEvaluationSet,
  evaluationSetErrors,
} from "../data/evaluationPreparation";
import type { DetectionData, EvaluationCondition } from "../data/types";
import { metricRows, metricText, versionLabels } from "./operationPresentation";

const path = "/detection/evaluation";
export const EvaluationComparison = ({
  data,
  condition,
  primaryId,
}: {
  data: DetectionData;
  condition: EvaluationCondition;
  primaryId: string;
}) => {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const stored = useDraftStore((state) => state.drafts[path]?.value) as
    { comparison?: { targetIds: string[] } } | undefined;
  const selected = params.has("compare")
    ? params.get("compare")!.split(",").filter(Boolean)
    : (stored?.comparison?.targetIds ?? (primaryId ? [primaryId] : []));
  const targetIds = [
    ...new Set([
      ...(data.activeVersionId ? [data.activeVersionId] : []),
      ...selected,
    ]),
  ];
  const targets = targetIds.flatMap((id) => {
    const found = data.versions.find((item) => item.id === id);
    return found ? [found] : [];
  });
  const set = data.evaluationSets?.find(
    (item) =>
      item.id === condition.evaluationSetId &&
      item.revision === condition.evaluationSetRevision,
  );
  const errors = [
    ...evaluationSetErrors(set, data),
    ...(!data.activeVersionId ? ["현재 운영 버전을 확인할 수 없습니다."] : []),
    ...(targets.length < 2
      ? ["현재 운영 버전과 비교할 후보를 선택해 주세요."]
      : []),
    ...targetIds
      .filter((id) => !data.versions.some((item) => item.id === id))
      .map((id) => `선택 대상을 찾을 수 없습니다: ${id}`),
    ...targets.flatMap((item) =>
      comparisonTargetErrors(item, data).map(
        (error) => `${item.name}: ${error}`,
      ),
    ),
  ];
  const select = (id: string, checked: boolean) => {
    const ids = checked
      ? [...new Set([...selected, id])]
      : selected.filter((item) => item !== id);
    const latest = useDraftStore.getState().drafts[path]?.value;
    const siblings = latest && typeof latest === "object" ? latest : {};
    useDraftStore
      .getState()
      .edit(path, {
        ...siblings,
        editor: "evaluation",
        comparison: { targetIds: ids },
      });
    const next = new URLSearchParams(params);
    next.set("compare", ids.join(","));
    setParams(next, { state: location.state });
  };
  const href = (resultId: string, candidateId: string) => {
    const next = new URLSearchParams(params);
    next.set("tab", "candidates");
    next.set("result", resultId);
    next.set("candidate", candidateId);
    return `?${next}`;
  };
  return (
    <section
      className="detection-card"
      aria-labelledby="evaluation-comparison-title"
    >
      <h2 id="evaluation-comparison-title">같은 조건으로 후보 비교</h2>
      <fieldset className="detection-options">
        <legend>비교 대상 · 현재 운영 버전 포함</legend>
        {data.versions.map((item) => (
          <label key={item.id} className="detection-checkbox">
            <input
              type="checkbox"
              aria-label={`${item.name} 비교 대상`}
              checked={targetIds.includes(item.id)}
              disabled={
                item.id === data.activeVersionId || item.state === "draft"
              }
              onChange={(event) => select(item.id, event.target.checked)}
            />
            {item.name} · {versionLabels[item.state]}
          </label>
        ))}
      </fieldset>
      <p className="detection-note">
        선택한 고정 평가 세트의 동일 revision·목적과 각 대상의 현재 구성에
        일치하는 기록만 비교합니다.
      </p>
      {errors.length > 0 && (
        <ul className="detection-error" role="status">
          {errors.map((error) => (
            <li key={error}>{error}</li>
          ))}
        </ul>
      )}
      <div className="detection-table-wrap">
        <table className="detection-table">
          <caption>동일 조건의 기록된 평가 지표 · 미측정값은 —</caption>
          <thead>
            <tr>
              <th>대상</th>
              <th>평가 처리 상태</th>
              {metricRows.map((item) => (
                <th key={item.key}>{item.label}</th>
              ))}
              <th>기록 상세</th>
            </tr>
          </thead>
          <tbody>
            {targets.map((item) => {
              const result = comparisonResult(item, set, data);
              return (
                <tr key={item.id}>
                  <th scope="row">{item.name}</th>
                  <td>
                    {result
                      ? jobStateLabels[result.state]
                      : data.evaluations.some(
                            (entry) => entry.candidateId === item.id,
                          )
                        ? "조건·구성 일치 결과 없음"
                        : "평가 기록 없음"}
                  </td>
                  {metricRows.map((metric) => (
                    <td key={metric.key}>
                      {metricText(
                        result?.state === "succeeded"
                          ? result.metrics[metric.key]
                          : null,
                        metric.unit,
                      )}
                    </td>
                  ))}
                  <td>
                    {result ? (
                      <Link
                        to={href(result.id, item.id)}
                        state={location.state}
                      >
                        {result.id} · {valueText(result.completedAt)}
                      </Link>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <ServiceAction
        operation="evaluate"
        label="비교 평가 실행"
        available={data.capabilities.evaluate}
        disabled={errors.length > 0}
        payload={{
          targets: targets.map((item) => ({
            candidateId: item.id,
            configurationFingerprint: currentConfigurationFingerprint(
              item,
              data,
            ),
          })),
          evaluationSetId: set?.id,
          evaluationSetRevision: set?.revision,
          condition: set ? conditionFromEvaluationSet(set) : null,
        }}
      >
        <p>
          {targets.map((item) => item.name).join(" / ")} · {set?.name} ·{" "}
          {set?.purpose}
        </p>
        <p>
          각 대상에 동일한 고정 세트로 평가를 요청합니다. 접수는 평가 완료를
          뜻하지 않습니다.
        </p>
      </ServiceAction>
    </section>
  );
};
