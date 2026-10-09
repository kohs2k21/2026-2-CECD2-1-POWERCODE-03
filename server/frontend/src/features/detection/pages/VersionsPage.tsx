import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Button } from "../../../components/ui/button";
import { EmptyState } from "../../../components/ui/feedback";
import {
  DetectionQueryBoundary,
  DetailMissing,
  jobStateLabels,
  valueText,
} from "../components/DetectionQueryBoundary";
import { ServiceAction } from "../components/ServiceAction";
import type { DetectionData } from "../data/types";
import { expectedVersionReview } from "../data/performanceCriteria";
import { useDetectionQuery } from "../data/useDetectionQuery";
import { versionReadiness, versionLabels } from "./operationPresentation";
import { VersionReviewSummary } from "./VersionReviewSummary";
import { CompositionEditor } from "./CompositionEditor";
import {
  ApplicationDetails,
  BundleDetails,
  BundleDiff,
  BundleEvidence,
} from "./VersionDetails";
import {
  DetectionTabs,
  detectionTabHref,
  useDetectionTab,
} from "../components/DetectionTabs";

const versionTabs = [
  { id: "configuration", label: "운영 구성" },
  { id: "application", label: "적용·복원" },
  { id: "history", label: "버전 이력" },
] as const;

const VersionsWorkbench = ({
  data,
  activeTab,
}: {
  data: DetectionData;
  activeTab: string;
}) => {
  const [params, setParams] = useSearchParams();
  const href = (values: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    Object.entries(values).forEach(([key, value]) =>
      value ? next.set(key, value) : next.delete(key),
    );
    return `?${next.toString()}`;
  };
  const patch = (values: Record<string, string | null>) =>
    setParams(href(values).slice(1));
  const active = data.versions.find((item) => item.id === data.activeVersionId);
  const viewedId = params.get("version");
  const viewed =
    viewedId !== null
      ? data.versions.find((item) => item.id === viewedId)
      : active;
  const candidates = data.versions.filter(
    (item) => item.state === "candidate" || item.state === "draft",
  );
  const candidateId = params.get("candidate") ?? candidates[0]?.id ?? "";
  const candidate = candidates.find((item) => item.id === candidateId);
  const rollbackTargets = data.versions.filter(
    (item) => item.state === "archived" && item.id !== data.activeVersionId,
  );
  const rollbackId = params.get("rollback") ?? rollbackTargets[0]?.id ?? "";
  const rollback = rollbackTargets.find((item) => item.id === rollbackId);
  const historyId = params.get("history");
  const history = data.applications.find((item) => item.id === historyId);
  const [applyApproved, setApplyApproved] = useState(false);
  const [rollbackApproved, setRollbackApproved] = useState(false);
  // Approval belongs to the reviewed content and evidence, not only its ID.
  const reviewSignature = (target: typeof candidate) =>
    JSON.stringify({
      active,
      target,
      evaluations: data.evaluations,
      modelArtifacts: data.modelArtifacts,
      ruleVersions: data.ruleVersions,
      models: data.models,
      snapshots: data.snapshots,
      evaluationSets: data.evaluationSets,
      evaluationSpecs: data.evaluationSpecs,
      evaluationSplits: data.evaluationSplits,
    });
  const applySignature = reviewSignature(candidate);
  const rollbackSignature = reviewSignature(rollback);
  useEffect(() => setApplyApproved(false), [applySignature, activeTab]);
  useEffect(() => setRollbackApproved(false), [rollbackSignature, activeTab]);
  const reasons = candidate ? versionReadiness(candidate, data) : [];
  const rollbackReasons = rollback ? versionReadiness(rollback, data) : [];
  const applyReview = candidate ? expectedVersionReview(candidate, data) : null;
  const rollbackReview = rollback
    ? expectedVersionReview(rollback, data)
    : null;

  return (
    <div className="detection-stack">
      {activeTab === "configuration" && (
        <>
          <section
            className="detection-card"
            aria-labelledby="active-version-title"
          >
            <h2 id="active-version-title">
              {viewedId !== null ? "선택한 버전 구성" : "현재 운영 버전"}
            </h2>
            {viewed ? (
              <BundleDetails bundle={viewed} data={data} />
            ) : viewedId !== null ? (
              <DetailMissing />
            ) : (
              <EmptyState>현재 운영 버전이 확인되지 않았습니다.</EmptyState>
            )}
            <p className="detection-note">
              운영 구성의 변경에는 별도 적용 요청이 필요합니다. 후보 선택이나
              확인창 열기로 바뀌지 않습니다.
            </p>
          </section>
          <CompositionEditor data={data} mode="versions" />
        </>
      )}
      {activeTab === "application" && (
        <>
          <section
            className="detection-card"
            aria-labelledby="candidate-version-title"
          >
            <h2 id="candidate-version-title">적용 후보</h2>
            {candidates.length ? (
              <div className="detection-actions">
                {candidates.map((item) => (
                  <Button asChild key={item.id} variant="outline">
                    <Link
                      to={href({ tab: "application", candidate: item.id })}
                      aria-current={
                        item.id === candidateId ? "true" : undefined
                      }
                    >
                      {item.name} · {versionLabels[item.state]}
                    </Link>
                  </Button>
                ))}
              </div>
            ) : (
              <EmptyState>적용 후보가 없습니다.</EmptyState>
            )}
            {!candidate ? (
              <DetailMissing />
            ) : (
              <section className="detection-section">
                <h3>{candidate.name}</h3>
                <VersionReviewSummary
                  active={active}
                  target={candidate}
                  data={data}
                  approved={applyApproved}
                />
                <BundleDiff active={active} candidate={candidate} data={data} />
                <div className="detection-grid">
                  <section>
                    <h3>평가 근거</h3>
                    <BundleEvidence bundle={candidate} data={data} />
                    <Button asChild variant="outline">
                      <Link
                        to={`/detection/evaluation?candidate=${encodeURIComponent(candidate.id)}${candidate.evaluationId ? `&result=${encodeURIComponent(candidate.evaluationId)}` : ""}`}
                      >
                        평가 결과 확인
                      </Link>
                    </Button>
                  </section>
                  <section>
                    <h3>적용 준비 상태</h3>
                    {reasons.length ? (
                      <ul className="detection-check-list">
                        {reasons.map((reason) => (
                          <li key={reason}>{reason}</li>
                        ))}
                      </ul>
                    ) : (
                      <p>구성·호환성·평가 근거 확인됨</p>
                    )}
                    {!active && (
                      <p className="detection-error">
                        현재 운영 버전 확인이 필요합니다.
                      </p>
                    )}
                  </section>
                </div>
                <label className="detection-field">
                  <span>
                    <input
                      type="checkbox"
                      checked={applyApproved}
                      onChange={(event) =>
                        setApplyApproved(event.target.checked)
                      }
                    />{" "}
                    관리자로서 구성 차이와 평가 근거를 검토했습니다.
                  </span>
                </label>
                <div className="detection-actions">
                  <ServiceAction
                    key={`apply:${applySignature}`}
                    operation="apply"
                    label="운영 적용"
                    available={data.capabilities.apply}
                    disabled={!applyApproved}
                    requestDisabled={
                      !applyApproved ||
                      reasons.length > 0 ||
                      !active ||
                      !applyReview
                    }
                    payload={{
                      versionId: candidate.id,
                      expectedActiveVersionId: data.activeVersionId,
                      approved: applyApproved,
                      expectedReview: applyReview,
                    }}
                  >
                    <p>
                      현재 {active?.name ?? "—"} → {candidate.name}
                    </p>
                    <BundleDetails bundle={candidate} data={data} />
                    {reasons.length > 0 && (
                      <>
                        <p className="detection-error">
                          준비 조건이 충족되지 않아 실행할 수 없습니다.
                        </p>
                        <ul>
                          {reasons.map((reason) => (
                            <li key={reason}>{reason}</li>
                          ))}
                        </ul>
                      </>
                    )}
                    <p className="detection-note">
                      현재 운영 버전이 달라졌다면 다시 검토해야 합니다. 최종
                      적용 여부는 서버의 작업 결과에서 확인합니다.
                    </p>
                  </ServiceAction>
                </div>
              </section>
            )}
          </section>
          <section className="detection-card" aria-labelledby="rollback-title">
            <h2 id="rollback-title">이전 버전으로 롤백</h2>
            <label className="detection-field">
              롤백 대상
              <select
                value={rollbackId}
                onChange={(event) => patch({ rollback: event.target.value })}
              >
                <option value="">이전 버전을 선택해 주세요</option>
                {rollbackTargets.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} · {item.id}
                  </option>
                ))}
              </select>
            </label>
            {rollback ? (
              <>
                <VersionReviewSummary
                  active={active}
                  target={rollback}
                  data={data}
                  approved={rollbackApproved}
                />
                <BundleDetails bundle={rollback} data={data} />
                {rollbackReasons.length > 0 && (
                  <ul className="detection-check-list">
                    {rollbackReasons.map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                )}
                <label className="detection-field">
                  <span>
                    <input
                      type="checkbox"
                      checked={rollbackApproved}
                      onChange={(event) =>
                        setRollbackApproved(event.target.checked)
                      }
                    />{" "}
                    롤백 대상의 구성과 가용성을 검토했습니다.
                  </span>
                </label>
                <div className="detection-actions">
                  <ServiceAction
                    key={`rollback:${rollbackSignature}`}
                    operation="rollback"
                    label="롤백"
                    available={data.capabilities.rollback}
                    disabled={!rollbackApproved}
                    requestDisabled={
                      !rollbackApproved ||
                      rollbackReasons.length > 0 ||
                      !active ||
                      !rollbackReview
                    }
                    payload={{
                      versionId: rollback.id,
                      expectedActiveVersionId: data.activeVersionId,
                      approved: rollbackApproved,
                      expectedReview: rollbackReview,
                    }}
                  >
                    <p>
                      현재 {active?.name ?? "—"} → {rollback.name}
                    </p>
                    <VersionReviewSummary
                      active={active}
                      target={rollback}
                      data={data}
                      approved={rollbackApproved}
                    />
                    <BundleDetails bundle={rollback} data={data} />
                    {rollbackReasons.length > 0 && (
                      <>
                        <p className="detection-error">
                          롤백 준비 조건을 확인해야 합니다.
                        </p>
                        <ul>
                          {rollbackReasons.map((reason) => (
                            <li key={reason}>{reason}</li>
                          ))}
                        </ul>
                      </>
                    )}
                    <p className="detection-note">
                      롤백 요청 접수와 운영 버전 변경 완료는 별도로 확인합니다.
                    </p>
                  </ServiceAction>
                </div>
              </>
            ) : rollbackTargets.length ? (
              <DetailMissing />
            ) : (
              <EmptyState>롤백할 이전 버전이 없습니다.</EmptyState>
            )}
          </section>
        </>
      )}
      {activeTab === "history" && (
        <>
          <section
            className="detection-card"
            aria-labelledby="application-history-title"
          >
            <h2 id="application-history-title">적용·롤백 이력</h2>
            {data.applications.length ? (
              <ul className="detection-check-list">
                {data.applications.map((event) => (
                  <li key={event.id}>
                    <Button
                      asChild
                      variant="ghost"
                      className="detection-list-button"
                    >
                      <Link
                        to={href({ tab: "history", history: event.id })}
                        aria-current={
                          historyId === event.id ? "true" : undefined
                        }
                      >
                        <span>
                          {event.action === "apply" ? "적용" : "롤백"} ·{" "}
                          {data.versions.find(
                            (item) => item.id === event.versionId,
                          )?.name ?? event.versionId}
                        </span>
                        <span>
                          {jobStateLabels[event.state]} ·{" "}
                          {valueText(event.appliedAt)}
                        </span>
                      </Link>
                    </Button>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState>적용·롤백 이력이 없습니다.</EmptyState>
            )}
          </section>
          {historyId && (
            <>
              {history ? (
                <ApplicationDetails event={history} data={data} />
              ) : (
                <DetailMissing />
              )}
              <div className="detection-actions">
                <Button asChild variant="outline">
                  <Link to={href({ tab: "history", history: null })}>
                    버전 목록으로 돌아가기
                  </Link>
                </Button>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
};

export const VersionsPage = () => {
  const query = useDetectionQuery();
  const [params] = useSearchParams();
  const fallback = params.has("history")
    ? "history"
    : params.has("candidate") || params.has("rollback")
      ? "application"
      : "configuration";
  const activeTab = useDetectionTab(
    versionTabs.map((item) => item.id),
    fallback,
  );
  return (
    <section className="detection-page">
      <header className="detection-page-header">
        <h1>운영 버전 관리</h1>
        <Button
          variant="outline"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          상태 새로고침
        </Button>
      </header>
      <DetectionTabs
        items={versionTabs.map((item) => ({
          ...item,
          to: detectionTabHref(params, item.id),
        }))}
        activeId={activeTab}
        ariaLabel="운영 버전 내용"
      />
      <DetectionQueryBoundary query={query}>
        {(data) => <VersionsWorkbench data={data} activeTab={activeTab} />}
      </DetectionQueryBoundary>
    </section>
  );
};
