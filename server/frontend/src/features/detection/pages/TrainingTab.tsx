import { useSearchParams } from "react-router-dom";
import { Button } from "../../../components/ui/button";
import { EmptyState } from "../../../components/ui/feedback";
import { ServiceAction } from "../components/ServiceAction";
import {
  DetailMissing,
  jobStateLabels,
  valueText,
} from "../components/DetectionQueryBoundary";
import { featureCatalog } from "../data/catalog";
import {
  useCreateDraft,
  validateDataDraft,
  validateTrainingConfig,
} from "../data/createDraft";
import { validateFeatureEditor } from "../data/featureBuilder";
import type { DetectionData } from "../data/types";
export const TrainingTab = ({ data }: { data: DetectionData }) => {
  const { draft, update } = useCreateDraft();
  const [params, setParams] = useSearchParams();
  const jobId = params.get("job");
  const jobState = params.get("jobState") ?? "all";
  const jobQuery = params.get("jobQ") ?? "";
  const selectedJob = data.trainingJobs.find((job) => job.id === jobId);
  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: key === "jobQ" });
  };
  const config = draft.training;
  const model = data.models.find((item) => item.id === config.modelId);
  const snapshot = data.snapshots.find((item) => item.id === draft.snapshotId);
  const setConfig = (patch: Partial<typeof config>) => {
    const next = { ...config, ...patch };
    update({
      training: next,
      trainingByModel: { ...draft.trainingByModel, [config.modelId]: next },
    });
  };
  const errors = [
    ...validateDataDraft(draft),
    ...validateTrainingConfig(config),
  ];
  if (snapshot && snapshot.state !== "ready")
    errors.push("고정 데이터셋이 준비되지 않았습니다.");
  if (snapshot && (draft.start < snapshot.start || draft.end > snapshot.end))
    errors.push("학습 기간은 고정 데이터셋 범위 안에 있어야 합니다.");
  const notReady = config.featureIds.filter((id) => {
    const feature = featureCatalog.find((item) => item.id === id);
    const edit = draft.featureEdits[id];
    return feature
      ? feature.readiness !== "ready" ||
          feature.source === "transaction" ||
          !model?.supportedTypes.includes(feature.type)
      : !edit ||
          edit.operation === "trainMedian" ||
          validateFeatureEditor(edit).length > 0;
  });
  if (notReady.length)
    errors.push(
      `입력 지원·정의·fit 확인이 필요한 선택 피처 ${notReady.length}개를 검토해 주세요.`,
    );
  const jobs = data.trainingJobs.filter(
    (job) =>
      (jobState === "all" || job.state === jobState) &&
      `${job.name} ${job.id}`.toLowerCase().includes(jobQuery.toLowerCase()),
  );
  return (
    <div className="detection-stack">
      <section className="detection-card">
        <h2>모델 학습 설정</h2>
        <div className="detection-form-grid">
          <label className="detection-field">
            모델
            <select
              value={config.modelId}
              onChange={(event) => {
                const modelId = event.target.value;
                update({
                  trainingByModel: {
                    ...draft.trainingByModel,
                    [config.modelId]: config,
                  },
                  training: draft.trainingByModel[modelId] ?? {
                    ...config,
                    modelId,
                  },
                });
              }}
            >
              <option value="">선택</option>
              {data.models.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <div>
            <p className="detection-note">
              데이터셋: {snapshot?.name ?? "선택 필요"}
            </p>
            <p className="detection-note">
              기간: {draft.start || "—"} ~ {draft.end || "—"}
            </p>
            <p className="detection-note">{model?.description}</p>
          </div>
          <label className="detection-field">
            트리 수
            <input
              inputMode="numeric"
              value={config.trees}
              onChange={(event) => setConfig({ trees: event.target.value })}
            />
          </label>
          <label className="detection-field">
            이상 비율
            <input
              inputMode="decimal"
              value={config.contamination}
              onChange={(event) =>
                setConfig({ contamination: event.target.value })
              }
            />
          </label>
          <label className="detection-field">
            랜덤 시드
            <input
              inputMode="numeric"
              value={config.seed}
              onChange={(event) => setConfig({ seed: event.target.value })}
            />
          </label>
        </div>
        <h3 className="detection-feedback">이 모델의 입력 피처</h3>
        <p className="detection-note">
          선택 {config.featureIds.length}개 · 원천 ID/텍스트 및 fit 준비 상태를
          확인한 뒤 입력을 확정합니다.
        </p>
        <div className="detection-check-list">
          {featureCatalog.map((feature) => (
            <label key={feature.id}>
              <input
                type="checkbox"
                checked={config.featureIds.includes(feature.id)}
                disabled={feature.readiness === "deferred"}
                onChange={() =>
                  setConfig({
                    featureIds: config.featureIds.includes(feature.id)
                      ? config.featureIds.filter((id) => id !== feature.id)
                      : [...config.featureIds, feature.id],
                  })
                }
              />
              <span>
                {feature.source === "derived"
                  ? feature.name
                  : `${feature.source}.${feature.name}`}
                <br />
                <span className="detection-note">{feature.reason}</span>
              </span>
            </label>
          ))}
          {draft.customFeatureIds.map((id) => (
            <label key={id}>
              <input
                type="checkbox"
                checked={config.featureIds.includes(id)}
                onChange={() =>
                  setConfig({
                    featureIds: config.featureIds.includes(id)
                      ? config.featureIds.filter((item) => item !== id)
                      : [...config.featureIds, id],
                  })
                }
              />
              {draft.featureEdits[id]?.name || "이름 없는 파생변수"}
            </label>
          ))}
        </div>
        {errors.length > 0 && (
          <ul role="alert" className="detection-error">
            {errors.map((error) => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        )}
        <div className="detection-actions">
          <ServiceAction
            operation="train"
            label="학습 요청"
            available={data.capabilities.train}
            payload={{
              ...config,
              snapshotId: draft.snapshotId,
              start: draft.start,
              end: draft.end,
              featureDefinitions: draft.featureEdits,
            }}
            requestDisabled={errors.length > 0}
          >
            <p>
              모델: {model?.name ?? "선택 필요"} · 피처{" "}
              {config.featureIds.length}개
            </p>
            <p>학습 요청은 현재 운영 버전을 변경하지 않습니다.</p>
            {errors.map((error) => (
              <p className="detection-error" key={error}>
                {error}
              </p>
            ))}
          </ServiceAction>
        </div>
      </section>
      <section className="detection-card">
        <h2>학습 작업</h2>
        <div className="detection-form-grid">
          <label className="detection-field">
            작업 검색
            <input
              value={jobQuery}
              onChange={(event) => setParam("jobQ", event.target.value)}
            />
          </label>
          <label className="detection-field">
            학습 상태
            <select
              value={jobState}
              onChange={(event) => setParam("jobState", event.target.value)}
            >
              <option value="all">전체</option>
              {Object.entries(jobStateLabels).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="detection-table-wrap detection-feedback">
          <table className="detection-table">
            <thead>
              <tr>
                <th>작업</th>
                <th>모델</th>
                <th>학습 상태</th>
                <th>요청 시각</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <tr key={job.id}>
                  <td>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="detection-list-button"
                      onClick={() => setParam("job", job.id)}
                    >
                      {job.name}
                    </Button>
                  </td>
                  <td>
                    {data.models.find((model) => model.id === job.modelId)
                      ?.name ?? job.modelId}
                  </td>
                  <td>{jobStateLabels[job.state]}</td>
                  <td>{valueText(job.requestedAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {jobs.length === 0 && (
            <EmptyState>조건에 맞는 학습 작업이 없습니다.</EmptyState>
          )}
        </div>
      </section>
      {jobId && (
        <section className="detection-card">
          <div className="detection-page-header">
            <h2>학습 작업 상세</h2>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setParam("job", "")}
            >
              작업 목록으로
            </Button>
          </div>
          {selectedJob ? (
            <>
              <dl>
                <dt>작업 ID</dt>
                <dd>{selectedJob.id}</dd>
                <dt>학습 상태</dt>
                <dd>{jobStateLabels[selectedJob.state]}</dd>
                <dt>데이터셋</dt>
                <dd>{selectedJob.snapshotId}</dd>
                <dt>입력 피처</dt>
                <dd>
                  {selectedJob.featureIds
                    .map(
                      (id) =>
                        featureCatalog.find((feature) => feature.id === id)
                          ?.name ?? id,
                    )
                    .join(", ")}
                </dd>
                <dt>시작·완료 시각</dt>
                <dd>
                  {valueText(selectedJob.startedAt)} ·{" "}
                  {valueText(selectedJob.completedAt)}
                </dd>
                <dt>진행률</dt>
                <dd>
                  {selectedJob.progress === null
                    ? "—"
                    : `${selectedJob.progress}%`}
                </dd>
                <dt>생성 후보</dt>
                <dd>{valueText(selectedJob.candidateId)}</dd>
              </dl>
              {selectedJob.failure && (
                <p className="detection-error" role="alert">
                  {selectedJob.failure}
                </p>
              )}
            </>
          ) : (
            <DetailMissing />
          )}
        </section>
      )}
    </div>
  );
};
