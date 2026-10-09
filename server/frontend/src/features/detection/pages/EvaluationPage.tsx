import { useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { Button } from "../../../components/ui/button";
import { EmptyState } from "../../../components/ui/feedback";
import {
  DetectionQueryBoundary,
  DetailMissing,
} from "../components/DetectionQueryBoundary";
import type {
  DetectionData,
  EvaluationCondition,
  VersionBundle,
} from "../data/types";
import { useDetectionQuery } from "../data/useDetectionQuery";
import { evaluationFor, versionLabels } from "./operationPresentation";
import { useEvaluationConditions } from "./useEvaluationConditions";
import { CompositionEditor } from "./CompositionEditor";
import { EvaluationConditionForm } from "./EvaluationConditionForm";
import { EvaluationRecords } from "./EvaluationRecords";
import {
  DetectionTabs,
  detectionTabHref,
  useDetectionTab,
} from "../components/DetectionTabs";

const evaluationTabs = [
  { id: "candidates", label: "생성된 후보 평가" },
  { id: "composition", label: "모델·룰 조합 검토" },
] as const;

const EvaluationWorkbench = ({
  data,
  activeTab,
}: {
  data: DetectionData;
  activeTab: string;
}) => {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const [preview, setPreview] = useState<VersionBundle | null>(null);
  const href = (values: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    Object.entries(values).forEach(([key, value]) =>
      value ? next.set(key, value) : next.delete(key),
    );
    return `?${next.toString()}`;
  };
  const patch = (values: Record<string, string | null>) =>
    setParams(href(values).slice(1), { state: location.state });
  const candidates = data.versions.filter(
    (item) => item.state === "candidate" || item.state === "draft",
  );
  const candidateId = params.get("candidate") ?? candidates[0]?.id ?? "";
  const candidate = candidates.find((item) => item.id === candidateId);
  const localPreview = activeTab === "composition";
  const target = localPreview ? (preview ?? undefined) : candidate;
  const targetKey = localPreview
    ? `preview:${preview?.configurationFingerprint ?? "unselected"}`
    : candidateId;
  const result = evaluationFor(target, data);
  const snapshot =
    data.snapshots.find((item) => item.id === target?.snapshotId) ??
    data.snapshots.find((item) => item.state === "ready");
  const initial: EvaluationCondition = result?.condition ?? {
    snapshotId: snapshot?.id ?? "",
    protocolId: "",
    splitVersion: snapshot?.splitVersion ?? "",
    scenario: "",
    purpose: "",
  };
  const { condition, change } = useEvaluationConditions(targetKey, initial);
  const search = params.get("q") ?? "";
  const state = params.get("state") ?? "all";
  const visible = candidates.filter(
    (item) =>
      (state === "all" || item.state === state) &&
      `${item.name} ${item.id}`
        .toLocaleLowerCase()
        .includes(search.toLocaleLowerCase()),
  );
  return (
    <div className="detection-stack">
      <div hidden={!localPreview}>
        <CompositionEditor
          data={data}
          mode="evaluation"
          onPreview={setPreview}
          interactive={localPreview}
        />
      </div>
      <div className={localPreview ? undefined : "detection-grid"}>
        {!localPreview && (
          <section
            className="detection-card"
            aria-labelledby="evaluation-candidate-title"
          >
            <h2 id="evaluation-candidate-title">생성된 평가 후보</h2>
            <div className="detection-form-grid">
              <label className="detection-field">
                후보 검색
                <input
                  value={search}
                  onChange={(event) => patch({ q: event.target.value })}
                  placeholder="이름·ID"
                />
              </label>
              <label className="detection-field">
                후보 상태
                <select
                  value={state}
                  onChange={(event) => patch({ state: event.target.value })}
                >
                  <option value="all">전체</option>
                  <option value="candidate">평가 후보</option>
                  <option value="draft">편집 중</option>
                </select>
              </label>
            </div>
            {visible.length ? (
              <ul className="detection-check-list">
                {visible.map((item) => (
                  <li key={item.id}>
                    <Button
                      asChild
                      variant="ghost"
                      className="detection-list-button"
                    >
                      <Link
                        to={href({
                          tab: "candidates",
                          candidate: item.id,
                          result: null,
                          selection: null,
                        })}
                        state={location.state}
                        aria-current={
                          item.id === candidateId && !localPreview
                            ? "true"
                            : undefined
                        }
                      >
                        <span>{item.name}</span>
                        <span className="detection-status">
                          {versionLabels[item.state]}
                        </span>
                      </Link>
                    </Button>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState>
                {candidates.length
                  ? "검색 조건에 맞는 후보가 없습니다."
                  : "평가 후보가 없습니다."}
              </EmptyState>
            )}
          </section>
        )}
        <section
          className="detection-card"
          aria-labelledby="evaluation-condition-title"
        >
          <h2 id="evaluation-condition-title">다음 평가 조건</h2>
          {target ? (
            <EvaluationConditionForm
              key={activeTab}
              data={data}
              target={target}
              localPreview={localPreview}
              condition={condition}
              change={change}
            />
          ) : localPreview ? (
            <EmptyState>
              모델·룰 조합을 확인해 구성 미리보기를 준비해 주세요.
            </EmptyState>
          ) : (
            <DetailMissing />
          )}
        </section>
      </div>
      {!localPreview && (
        <EvaluationRecords
          data={data}
          candidate={candidate}
          condition={condition}
        />
      )}
    </div>
  );
};

export const EvaluationPage = () => {
  const query = useDetectionQuery();
  const [params] = useSearchParams();
  const activeTab = useDetectionTab(
    evaluationTabs.map((item) => item.id),
    !params.has("result") && params.get("selection") === "composition"
      ? "composition"
      : "candidates",
  );
  return (
    <section className="detection-page">
      <header className="detection-page-header">
        <h1>성능 평가·비교</h1>
        <Button
          variant="outline"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          상태 새로고침
        </Button>
      </header>
      <DetectionTabs
        items={evaluationTabs.map((item) => ({
          ...item,
          to: detectionTabHref(params, item.id),
        }))}
        activeId={activeTab}
        ariaLabel="평가 내용"
      />
      <DetectionQueryBoundary query={query}>
        {(data) => <EvaluationWorkbench data={data} activeTab={activeTab} />}
      </DetectionQueryBoundary>
    </section>
  );
};
