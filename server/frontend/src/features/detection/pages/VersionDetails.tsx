import { useState } from "react";
import { featureCatalog } from "../data/catalog";
import { evaluationMatchesConfiguration } from "../data/configuration";
import type {
  ApplicationEvent,
  DetectionData,
  RuleVersion,
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

const ruleText = (rule: RuleVersion["rules"][number]) => {
  const operators = {
    gt: ">",
    gte: "≥",
    isMissing: "결측",
    mismatch: "합계 불일치",
  };
  const condition =
    rule.operator === "gt" || rule.operator === "gte"
      ? `${rule.field} ${operators[rule.operator]} ${valueText(rule.threshold)} ${rule.unit}`
      : `${rule.field} ${operators[rule.operator]}`;
  return `${rule.name} · ${condition} · ${rule.description}`;
};

export const configurationRows = (
  bundle: VersionBundle,
  data: DetectionData,
) => ({
  artifact:
    data.modelArtifacts.find((item) => item.id === bundle.modelArtifactId)
      ?.name ?? bundle.modelArtifactId,
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
    bundle.ruleVersionIds
      .map((id) => {
        const version = data.ruleVersions.find((item) => item.id === id);
        return version
          ? `${version.name} (${version.version}): ${version.rules.map(ruleText).join("; ")}`
          : `${id} · 미확인`;
      })
      .join(", ") || "선택 없음",
  explanation: bundle.explanationVersion,
  snapshot:
    data.snapshots.find((item) => item.id === bundle.snapshotId)?.name ??
    bundle.snapshotId,
});
const configurationLabels = {
  artifact: "모델 산출물",
  model: "모델",
  features: "피처",
  featureVersion: "피처 버전",
  fit: "학습된 전처리 통계",
  preprocessing: "전처리 버전",
  rules: "룰",
  explanation: "설명 구성",
  snapshot: "학습 데이터셋",
} as const;

const configurationIdentity = (bundle: VersionBundle, data: DetectionData) => ({
  artifact: JSON.stringify({
    id: bundle.modelArtifactId,
    metadata:
      data.modelArtifacts.find((item) => item.id === bundle.modelArtifactId) ??
      null,
  }),
  model: bundle.modelId,
  features: JSON.stringify(bundle.featureIds),
  featureVersion: bundle.featureVersion,
  fit: bundle.fitVersion,
  preprocessing: bundle.preprocessingVersion,
  rules: JSON.stringify(
    bundle.ruleVersionIds.map((id) => {
      const version = data.ruleVersions.find((item) => item.id === id);
      return version
        ? { id: version.id, version: version.version, rules: version.rules }
        : { id, missing: true };
    }),
  ),
  explanation: bundle.explanationVersion,
  snapshot: bundle.snapshotId,
});

export const configurationChangeCount = (
  active: VersionBundle | undefined,
  target: VersionBundle,
  data: DetectionData,
) => {
  if (!active) return null;
  const left = configurationIdentity(active, data),
    right = configurationIdentity(target, data);
  return Object.keys(right).filter(
    (key) =>
      left[key as keyof typeof left] !== right[key as keyof typeof right],
  ).length;
};
const configurationValue = (key: string, value: unknown) =>
  key === "features" || key === "rules" ? (
    <details className="version-long-content">
      <summary>{key === "features" ? "피처 상세" : "룰 상세"}</summary>
      <p>{valueText(typeof value === "string" ? value : null)}</p>
    </details>
  ) : (
    valueText(typeof value === "string" ? value : null)
  );

export const BundleDetails = ({
  bundle,
  data,
}: {
  bundle: VersionBundle;
  data: DetectionData;
}) => (
  <dl className="detection-summary">
    <div>
      <dt>{bundle.id === "composition-preview" ? "구성" : "버전"}</dt>
      <dd>
        {bundle.name}
        {bundle.id !== "composition-preview" && ` · ${bundle.id}`}
      </dd>
    </div>
    <div>
      <dt>상태</dt>
      <dd>
        {bundle.id === "composition-preview"
          ? "로컬 미리보기"
          : versionLabels[bundle.state]}
      </dd>
    </div>
    {Object.entries(configurationRows(bundle, data)).map(([key, value]) => (
      <div key={key}>
        <dt>{configurationLabels[key as keyof typeof configurationLabels]}</dt>
        <dd>{configurationValue(key, value)}</dd>
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
  const [changedOnly, setChangedOnly] = useState(false);
  const left = active ? configurationRows(active, data) : null;
  const right = configurationRows(candidate, data);
  const previousIdentity = active ? configurationIdentity(active, data) : null;
  const nextIdentity = configurationIdentity(candidate, data);
  return (
    <div>
      <label className="detection-checkbox">
        <input
          type="checkbox"
          checked={changedOnly}
          onChange={(event) => setChangedOnly(event.target.checked)}
        />
        변경 항목만 보기
      </label>
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
            {Object.entries(right)
              .filter(
                ([key]) =>
                  !changedOnly ||
                  previousIdentity?.[key as keyof typeof nextIdentity] !==
                    nextIdentity[key as keyof typeof nextIdentity],
              )
              .map(([key, value]) => {
                const previous = left?.[key as keyof typeof right];
                return (
                  <tr key={key}>
                    <th scope="row">
                      {
                        configurationLabels[
                          key as keyof typeof configurationLabels
                        ]
                      }
                    </th>
                    <td>{configurationValue(key, previous)}</td>
                    <td>{configurationValue(key, value)}</td>
                    <td>
                      {previous == null || value == null
                        ? "미확인"
                        : previousIdentity?.[
                              key as keyof typeof nextIdentity
                            ] === nextIdentity[key as keyof typeof nextIdentity]
                          ? "동일"
                          : "변경"}
                    </td>
                  </tr>
                );
              })}
          </tbody>
        </table>
      </div>
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
      <p
        className={
          evaluationMatchesConfiguration(result, bundle, data)
            ? "detection-status"
            : "detection-error"
        }
        role="status"
      >
        {evaluationMatchesConfiguration(result, bundle, data)
          ? "기록된 평가와 현재 구성 일치"
          : "재평가 필요: 기록된 평가와 현재 모델·룰 구성 불일치"}
      </p>
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
          <dt>기록된 평가 완료 시각</dt>
          <dd>{valueText(result.completedAt)}</dd>
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
