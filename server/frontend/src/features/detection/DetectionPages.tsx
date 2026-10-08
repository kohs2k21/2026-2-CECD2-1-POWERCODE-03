import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { createTabs } from "../../app/routePaths";
import { Button } from "../../components/ui/button";
import { Modal } from "../../components/ui/Modal";
import { useDetectionQuery } from "./data/useDetectionQuery";
import {
  useCreateDraft,
  validateDataDraft,
  validateTrainingConfig,
  validateRule,
} from "./data/createDraft";
import { validateFeatureEditor } from "./data/featureBuilder";
import { DetectionQueryBoundary } from "./components/DetectionQueryBoundary";
import { ServiceAction } from "./components/ServiceAction";
import { DataFeaturesTab } from "./pages/DataFeaturesTab";
import { TrainingTab } from "./pages/TrainingTab";
import { RulesTab } from "./pages/RulesTab";

export const DetectionPage = ({ title }: { title: string }) => (
  <section className="detection-page">
    <h1>{title}</h1>
  </section>
);
export const CreatePage = () => {
  const query = useDetectionQuery();
  const { draft, dirty, update, reset } = useCreateDraft();
  const [params, setParams] = useSearchParams();
  const [resetOpen, setResetOpen] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const selected = params.get("tab");
  const saveErrors = [
    ...validateDataDraft(draft),
    ...validateTrainingConfig(draft.training),
    ...Object.values(draft.featureEdits).flatMap((value) =>
      validateFeatureEditor(value),
    ),
    ...Object.values(draft.rules).flatMap(validateRule),
  ];
  const activeTab =
    createTabs.find((tab) => tab.id === selected) ?? createTabs[0];
  useEffect(() => {
    if (!createTabs.some((tab) => tab.id === selected)) {
      const next = new URLSearchParams(params);
      next.set("tab", "data-features");
      setParams(next, { replace: true });
    }
  }, [selected, params, setParams]);
  return (
    <section className="detection-page">
      <DetectionQueryBoundary query={query}>
        {(data) => (
          <>
            <div className="detection-page-header">
              <h1>모델·룰 만들기</h1>
              <ServiceAction
                operation="saveDraft"
                label="구성 저장"
                available={data.capabilities.saveDraft}
                payload={draft}
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
            </div>
            <div className="detection-form-grid detection-feedback">
              <label className="detection-field">
                구성 이름
                <input
                  value={draft.name}
                  maxLength={80}
                  onChange={(event) => update({ name: event.target.value })}
                />
              </label>
              <div className="detection-note" role="status">
                {dirty ? "편집 내용 유지 중" : "새 구성"}
              </div>
            </div>
            <nav className="create-tabs" aria-label="탐지 구성 편집 영역">
              {createTabs.map((tab) => {
                const next = new URLSearchParams(params);
                next.set("tab", tab.id);
                return (
                  <Link
                    className="ui-nav-link"
                    key={tab.id}
                    to={{ search: "?" + next.toString() }}
                    aria-current={activeTab.id === tab.id ? "page" : undefined}
                  >
                    {tab.label}
                  </Link>
                );
              })}
            </nav>
            <section
              className="creation-section"
              aria-labelledby="creation-section-title"
            >
              <h2 id="creation-section-title">{activeTab.label}</h2>
              {activeTab.id === "data-features" ? (
                <DataFeaturesTab data={data} />
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
              {activeTab.id === "data-features" && (
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
            <Modal
              isOpen={resetOpen}
              onOpenChange={setResetOpen}
              title="기본값 복원"
              description="현재 구성의 편집 내용을 버리고 기본 선택으로 돌아갑니다."
              size="sm"
            >
              <div className="detection-actions">
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
              </div>
            </Modal>
          </>
        )}
      </DetectionQueryBoundary>
    </section>
  );
};
