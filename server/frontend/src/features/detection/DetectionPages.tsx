import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { createTabs } from "../../app/routePaths";
import { Button } from "../../components/ui/button";
import { Modal } from "../../components/ui/Modal";
import { DialogFooter } from "../../components/ui/dialog";
import { useDetectionQuery } from "./data/useDetectionQuery";
import {
  useCreateDraft,
  validateDataDraft,
  validateTrainingConfig,
} from "./data/createDraft";
import { featureOptions, featureDefinitionPayload } from "./data/formula";
import { validateFeatureEditor } from "./data/featureBuilder";
import { DetectionQueryBoundary } from "./components/DetectionQueryBoundary";
import { DetectionTabs } from "./components/DetectionTabs";
import { ServiceAction } from "./components/ServiceAction";
import { DatasetTab } from "./pages/DatasetTab";
import { FeaturesTab } from "./pages/FeaturesTab";
import { TrainingTab } from "./pages/TrainingTab";
import { RulesTab } from "./pages/RulesTab";

export const CreatePage = () => {
  const query = useDetectionQuery();
  const { draft, dirty, update, reset } = useCreateDraft();
  const [params, setParams] = useSearchParams();
  const [resetOpen, setResetOpen] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const selected = params.get("tab");
  const canonicalTab =
    selected === "data-features"
      ? ["feature", "kind", "featureQ"].some((key) => params.has(key))
        ? "features"
        : "dataset"
      : selected;
  const activeTab =
    createTabs.find((tab) => tab.id === canonicalTab) ?? createTabs[0];
  const definitions = featureOptions(draft);
  const modelFeaturePayload = {
    ...Object.fromEntries(
      Object.entries(draft).filter(
        ([key]) =>
          !["rules", "ruleThresholdInputs", "recommendationDecisions"].includes(
            key,
          ),
      ),
    ),
    featureEdits: featureDefinitionPayload(draft),
  };
  const saveErrors = [
    ...validateDataDraft(draft),
    ...validateTrainingConfig(draft.training),
    ...Object.values(draft.featureEdits).flatMap((value) =>
      validateFeatureEditor(value, definitions),
    ),
  ];
  useEffect(() => {
    if (
      selected === "data-features" ||
      !createTabs.some((tab) => tab.id === selected)
    ) {
      const next = new URLSearchParams(params);
      next.set(
        "tab",
        canonicalTab === "features" || params.has("feature")
          ? "features"
          : "dataset",
      );
      setParams(next, { replace: true });
    }
  }, [selected, canonicalTab, params, setParams]);
  return (
    <section className="detection-page">
      <DetectionQueryBoundary query={query}>
        {(data) => (
          <>
            <div className="detection-page-header">
              <h1>모델·룰 생성</h1>
              {activeTab.id !== "rules" && (
                <ServiceAction
                  operation="saveDraft"
                  label="모델·피처 초안 저장"
                  available={data.capabilities.saveDraft}
                  payload={modelFeaturePayload}
                  requestDisabled={saveErrors.length > 0}
                >
                  <p>
                    {draft.name || "이름 없음"} · 피처{" "}
                    {draft.selectedFeatureIds.length}개
                  </p>
                  {Array.from(new Set(saveErrors)).map((error) => (
                    <p className="detection-error" key={error}>
                      {error}
                    </p>
                  ))}
                </ServiceAction>
              )}
            </div>
            <DetectionTabs
              ariaLabel="탐지 구성 편집 영역"
              activeId={activeTab.id}
              items={createTabs.map((tab) => {
                const next = new URLSearchParams(params);
                next.set("tab", tab.id);
                return { ...tab, to: "?" + next.toString() };
              })}
            />
            <div className="detection-form-grid detection-feedback">
              {activeTab.id !== "rules" && (
                <label className="detection-field">
                  구성 이름
                  <input
                    value={draft.name}
                    maxLength={80}
                    onChange={(event) => update({ name: event.target.value })}
                  />
                </label>
              )}
              <div className="detection-note" role="status">
                {dirty ? "편집 내용 유지 중" : "새 구성"}
              </div>
            </div>
            <section
              className="creation-section"
              aria-labelledby="creation-section-title"
            >
              <h2 id="creation-section-title">{activeTab.label}</h2>
              {activeTab.id === "dataset" ? (
                <DatasetTab data={data} />
              ) : activeTab.id === "features" ? (
                <FeaturesTab />
              ) : activeTab.id === "training" ? (
                <TrainingTab data={data} />
              ) : (
                <RulesTab data={data} />
              )}
            </section>
            {errors.length > 0 && (
              <ul role="alert" className="detection-error">
                {errors.map((error) => (
                  <li key={error}>{error}</li>
                ))}
              </ul>
            )}
            {activeTab.id !== "rules" && (
              <div className="detection-actions">
                <Button variant="outline" onClick={() => setResetOpen(true)}>
                  기본값 복원
                </Button>
                <Button
                  variant="outline"
                  onClick={() => setErrors(validateDataDraft(draft))}
                >
                  구성 입력 확인
                </Button>
                {activeTab.id === "features" && (
                  <Button asChild>
                    <Link
                      to={{
                        search:
                          "?" +
                          new URLSearchParams({
                            ...Object.fromEntries(params),
                            tab: "training",
                          }).toString(),
                      }}
                    >
                      학습 설정으로
                    </Link>
                  </Button>
                )}
              </div>
            )}
            <Modal
              isOpen={resetOpen}
              onOpenChange={setResetOpen}
              title="기본값 복원"
              description="현재 구성의 편집 내용을 버리고 기본 선택으로 돌아갑니다."
              size="sm"
            >
              <DialogFooter>
                <Button variant="outline" onClick={() => setResetOpen(false)}>
                  취소
                </Button>
                <Button
                  onClick={() => {
                    reset();
                    setErrors([]);
                    setResetOpen(false);
                  }}
                >
                  기본값 복원
                </Button>
              </DialogFooter>
            </Modal>
          </>
        )}
      </DetectionQueryBoundary>
    </section>
  );
};
