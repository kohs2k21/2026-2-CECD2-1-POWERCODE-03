import { featureCatalog } from "../data/catalog";
import type {
  ApplicationEvent,
  DetectionData,
  VersionBundle,
} from "../data/types";
import {
  jobStateLabels,
  valueText,
} from "../components/DetectionQueryBoundary";
import {
  evaluationFor,
  metricRows,
  metricText,
  versionLabels,
} from "./operationPresentation";

const configurationRows = (bundle: VersionBundle, data: DetectionData) => ({
  model:
    data.models.find((item) => item.id === bundle.modelId)?.name ??
    bundle.modelId,
  features:
    bundle.featureIds
      .map((id) => featureCatalog.find((item) => item.id === id)?.name ?? id)
      .join(", ") || "선택 없음",
  featureVersion: bundle.featureVersion,
  fit: bundle.fitVersion,
  preprocessing: bundle.preprocessingVersion,
  rules:
    bundle.ruleIds
      .map((id) => data.rules.find((item) => item.id === id)?.name ?? id)
      .join(", ") || "선택 없음",
  explanation: bundle.explanationVersion,
  snapshot:
    data.snapshots.find((item) => item.id === bundle.snapshotId)?.name ??
    bundle.snapshotId,
});
const configurationLabels = {
  model: "모델",
  features: "피처",
  featureVersion: "피처 버전",
  fit: "학습된 전처리 통계",
  preprocessing: "전처리 버전",
  rules: "룰",
  explanation: "설명 구성",
  snapshot: "학습 데이터셋",
} as const;

const configurationIdentity = (bundle: VersionBundle) => ({
  model: bundle.modelId,
  features: JSON.stringify(bundle.featureIds),
  featureVersion: bundle.featureVersion,
  fit: bundle.fitVersion,
  preprocessing: bundle.preprocessingVersion,
  rules: JSON.stringify(bundle.ruleIds),
  explanation: bundle.explanationVersion,
  snapshot: bundle.snapshotId,
});

export const BundleDetails = ({
  bundle,
  data,
}: {
  bundle: VersionBundle;
  data: DetectionData;
}) => (
  <dl className="detection-summary">
    <div>
      <dt>버전</dt>
      <dd>
        {bundle.name} · {bundle.id}
      </dd>
    </div>
    <div>
      <dt>상태</dt>
      <dd>{versionLabels[bundle.state]}</dd>
    </div>
    {Object.entries(configurationRows(bundle, data)).map(([key, value]) => (
      <div key={key}>
        <dt>{configurationLabels[key as keyof typeof configurationLabels]}</dt>
        <dd>{valueText(value)}</dd>
      </div>
    ))}
    <div>
      <dt>적용 시각</dt>
      <dd>{valueText(bundle.appliedAt)}</dd>
    </div>
    <div>
      <dt>호환성</dt>
      <dd>
        {bundle.compatible == null
          ? "—"
          : bundle.compatible
            ? "호환 확인"
            : "호환 불가"}
      </dd>
    </div>
    <div>
      <dt>산출물</dt>
      <dd>
        {bundle.artifactAvailable == null
          ? "—"
          : bundle.artifactAvailable
            ? "가용 확인"
            : "없음"}
      </dd>
    </div>
  </dl>
);

export const BundleDiff = ({
  active,
  candidate,
  data,
}: {
  active: VersionBundle | undefined;
  candidate: VersionBundle;
  data: DetectionData;
}) => {
  const left = active ? configurationRows(active, data) : null;
  const right = configurationRows(candidate, data);
  const previousIdentity = active ? configurationIdentity(active) : null;
  const nextIdentity = configurationIdentity(candidate);
  return (
    <div className="detection-table-wrap">
      <table className="detection-table">
        <caption>운영 구성과 선택한 후보의 구성 차이</caption>
        <thead>
          <tr>
            <th scope="col">구성</th>
            <th scope="col">운영</th>
            <th scope="col">후보</th>
            <th scope="col">차이</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(right).map(([key, value]) => {
            const previous = left?.[key as keyof typeof right];
            return (
              <tr key={key}>
                <th scope="row">
                  {configurationLabels[key as keyof typeof configurationLabels]}
                </th>
                <td>{valueText(previous)}</td>
                <td>{valueText(value)}</td>
                <td>
                  {previous == null || value == null
                    ? "미확인"
                    : previousIdentity?.[key as keyof typeof nextIdentity] ===
                        nextIdentity[key as keyof typeof nextIdentity]
                      ? "동일"
                      : "변경"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

export const BundleEvidence = ({
  bundle,
  data,
}: {
  bundle: VersionBundle;
  data: DetectionData;
}) => {
  const result = evaluationFor(bundle, data);
  if (!result)
    return <p className="detection-note">연결된 평가 근거가 없습니다.</p>;
  return (
    <>
      <dl className="detection-summary">
        <div>
          <dt>평가 ID</dt>
          <dd>{result.id}</dd>
        </div>
        <div>
          <dt>평가 상태</dt>
          <dd>{jobStateLabels[result.state]}</dd>
        </div>
        <div>
          <dt>데이터셋</dt>
          <dd>{result.condition.snapshotId}</dd>
        </div>
        <div>
          <dt>프로토콜·분할</dt>
          <dd>
            {result.condition.protocolId} · {result.condition.splitVersion}
          </dd>
        </div>
        <div>
          <dt>시나리오·목적</dt>
          <dd>
            {result.condition.scenario} · {result.condition.purpose}
          </dd>
        </div>
        {metricRows.map(({ key, label, unit }) => (
          <div key={key}>
            <dt>{label}</dt>
            <dd>{metricText(result.metrics[key], unit)}</dd>
          </div>
        ))}
      </dl>
      {result.failure && (
        <p className="detection-error" role="alert">
          {result.failure}
        </p>
      )}
    </>
  );
};

export const ApplicationDetails = ({
  event,
  data,
}: {
  event: ApplicationEvent;
  data: DetectionData;
}) => {
  const bundle = data.versions.find((item) => item.id === event.versionId);
  return (
    <section
      className="detection-card"
      aria-labelledby="application-detail-title"
    >
      <h2 id="application-detail-title">적용 이력 상세</h2>
      <dl className="detection-summary">
        <div>
          <dt>요청 ID</dt>
          <dd>{event.id}</dd>
        </div>
        <div>
          <dt>작업</dt>
          <dd>{event.action === "apply" ? "적용" : "롤백"}</dd>
        </div>
        <div>
          <dt>상태</dt>
          <dd>{jobStateLabels[event.state]}</dd>
        </div>
        <div>
          <dt>이전 버전</dt>
          <dd>{valueText(event.previousVersionId)}</dd>
        </div>
        <div>
          <dt>대상 버전</dt>
          <dd>{event.versionId}</dd>
        </div>
        <div>
          <dt>요청 관리자</dt>
          <dd>{event.actor}</dd>
        </div>
        <div>
          <dt>요청 시각</dt>
          <dd>{valueText(event.requestedAt)}</dd>
        </div>
        <div>
          <dt>적용 시각</dt>
          <dd>{valueText(event.appliedAt)}</dd>
        </div>
      </dl>
      {event.failure && (
        <p className="detection-error" role="alert">
          {event.failure}
        </p>
      )}
      {bundle && <BundleDetails bundle={bundle} data={data} />}
    </section>
  );
};
