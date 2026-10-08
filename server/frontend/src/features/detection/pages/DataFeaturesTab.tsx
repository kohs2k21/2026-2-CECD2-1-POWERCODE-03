import { useSearchParams } from "react-router-dom";
import { Button } from "../../../components/ui/button";
import { EmptyState } from "../../../components/ui/feedback";
import { featureCatalog } from "../data/catalog";
import { defaultFeatureEditor } from "../data/featureBuilder";
import { useCreateDraft } from "../data/createDraft";
import { FeatureEditor } from "./FeatureEditor";
import type { DetectionData } from "../data/types";
import { ServiceAction } from "../components/ServiceAction";
const readinessLabels = {
  ready: "입력 검증 필요",
  "fit-required": "fit 필요",
  unconfirmed: "정의 확인 필요",
  unsupported: "직접 입력 미지원",
  deferred: "보류",
};
export const DataFeaturesTab = ({ data }: { data: DetectionData }) => {
  const { draft, update } = useCreateDraft();
  const [params, setParams] = useSearchParams();
  const query = params.get("featureQ") ?? "";
  const kind = params.get("kind") ?? "all";
  const selectedId = params.get("feature");
  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: key === "featureQ" });
  };
  const listed = featureCatalog.filter(
    (feature) =>
      (kind === "all" ||
        (kind === "raw" && feature.source !== "derived") ||
        (kind === "derived" && feature.source === "derived")) &&
      `${feature.name} ${feature.source} ${feature.id} ${feature.aliases.join(" ")}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const toggle = (id: string) => {
    const selected = draft.selectedFeatureIds.includes(id)
      ? draft.selectedFeatureIds.filter((item) => item !== id)
      : [...draft.selectedFeatureIds, id];
    update({ selectedFeatureIds: selected });
  };
  const addFeature = () => {
    const id = `custom-${draft.customFeatureIds.length + 1}`;
    update({
      customFeatureIds: [...draft.customFeatureIds, id],
      featureEdits: { ...draft.featureEdits, [id]: defaultFeatureEditor() },
      selectedFeatureIds: [...draft.selectedFeatureIds, id],
    });
    setParam("feature", id);
  };
  return (
    <div className="detection-stack">
      <section className="detection-card">
        <h2>학습 데이터</h2>
        <div className="detection-form-grid">
          <label className="detection-field">
            고정 데이터셋
            <select
              value={draft.snapshotId}
              onChange={(event) => {
                const snapshot = data.snapshots.find(
                  (item) => item.id === event.target.value,
                );
                update({
                  snapshotId: event.target.value,
                  start: snapshot?.start ?? "",
                  end: snapshot?.end ?? "",
                });
              }}
            >
              <option value="">선택</option>
              {data.snapshots.map((snapshot) => (
                <option key={snapshot.id} value={snapshot.id}>
                  {snapshot.name} ·{" "}
                  {snapshot.state === "ready"
                    ? "고정 범위"
                    : snapshot.state === "pending"
                      ? "준비 대기"
                      : "실패"}
                </option>
              ))}
            </select>
          </label>
          <label className="detection-field">
            학습 시작일
            <input
              type="date"
              value={draft.start}
              onChange={(event) => update({ start: event.target.value })}
            />
          </label>
          <label className="detection-field">
            학습 종료일
            <input
              type="date"
              value={draft.end}
              onChange={(event) => update({ end: event.target.value })}
            />
          </label>
          <div>
            <p className="detection-note">선택된 데이터 수: —</p>
            <p className="detection-note">요일·공휴일 자동 제외 없음</p>
          </div>
        </div>
        {draft.snapshotId && (
          <dl>
            <dt>원천·추출 기준</dt>
            <dd>
              {data.snapshots.find((item) => item.id === draft.snapshotId)
                ?.description ?? "선택 데이터셋을 찾을 수 없습니다."}
            </dd>
            <dt>스키마·분할 버전</dt>
            <dd>
              {data.snapshots.find((item) => item.id === draft.snapshotId)
                ?.schemaVersion ?? "—"}{" "}
              ·{" "}
              {data.snapshots.find((item) => item.id === draft.snapshotId)
                ?.splitVersion ?? "—"}
            </dd>
          </dl>
        )}
        <div className="detection-actions">
          <ServiceAction
            operation="createSnapshot"
            label="고정 데이터셋 생성"
            available={data.capabilities.createSnapshot}
            payload={{ start: draft.start, end: draft.end }}
            disabled={!draft.start || !draft.end || draft.start > draft.end}
          >
            <p>
              범위: {draft.start} ~ {draft.end}
            </p>
            <p>원본을 고정 데이터셋으로 확정하는 요청입니다.</p>
          </ServiceAction>
        </div>
      </section>
      <div className="detection-grid">
        <section className="detection-card">
          <div className="detection-page-header">
            <h2>원천·파생 피처 목록</h2>
            <span className="detection-status">
              선택 {draft.selectedFeatureIds.length}개
            </span>
          </div>
          <label className="detection-field">
            속성 검색
            <input
              value={query}
              onChange={(event) => setParam("featureQ", event.target.value)}
            />
          </label>
          <label className="detection-field detection-feedback">
            유형 필터
            <select
              value={kind}
              onChange={(event) => setParam("kind", event.target.value)}
            >
              <option value="all">전체</option>
              <option value="raw">원천</option>
              <option value="derived">파생</option>
            </select>
          </label>
          <div className="detection-table-wrap detection-catalog detection-feedback">
            <table className="detection-table">
              <thead>
                <tr>
                  <th>선택</th>
                  <th>속성</th>
                  <th>타입</th>
                  <th>준비 상태</th>
                </tr>
              </thead>
              <tbody>
                {listed.map((feature) => (
                  <tr
                    key={feature.id}
                    className={
                      selectedId === feature.id
                        ? "detection-selection"
                        : undefined
                    }
                  >
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`${feature.source === "derived" ? feature.name : `${feature.source}.${feature.name}`} 학습 피처 선택`}
                        checked={draft.selectedFeatureIds.includes(feature.id)}
                        disabled={feature.readiness === "deferred"}
                        onChange={() => toggle(feature.id)}
                      />
                    </td>
                    <td>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="detection-list-button"
                        onClick={() => setParam("feature", feature.id)}
                      >
                        {feature.source === "derived"
                          ? feature.name
                          : `${feature.source}.${feature.name}`}
                      </Button>
                    </td>
                    <td>{feature.type}</td>
                    <td>{readinessLabels[feature.readiness]}</td>
                  </tr>
                ))}
                {draft.customFeatureIds
                  .filter(
                    (id) =>
                      kind !== "raw" &&
                      (draft.featureEdits[id]?.name ?? id)
                        .toLowerCase()
                        .includes(query.toLowerCase()),
                  )
                  .map((id) => (
                    <tr key={id}>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`${draft.featureEdits[id]?.name || id} 학습 피처 선택`}
                          checked={draft.selectedFeatureIds.includes(id)}
                          onChange={() => toggle(id)}
                        />
                      </td>
                      <td>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="detection-list-button"
                          onClick={() => setParam("feature", id)}
                        >
                          {draft.featureEdits[id]?.name || "이름 없는 파생변수"}
                        </Button>
                      </td>
                      <td>수치</td>
                      <td>편집 검증 필요</td>
                    </tr>
                  ))}
              </tbody>
            </table>
            {listed.length === 0 && draft.customFeatureIds.length === 0 && (
              <EmptyState>검색 조건에 맞는 속성이 없습니다.</EmptyState>
            )}
          </div>
          <p className="detection-note">
            선택과 계산 준비 상태는 별개입니다. 미지원·fit 필요 항목의 사유를
            확인해 주세요.
          </p>
          <div className="detection-actions">
            <Button variant="outline" onClick={addFeature}>
              파생변수 추가
            </Button>
          </div>
        </section>
        <FeatureEditor key={selectedId ?? "none"} featureId={selectedId} />
      </div>
    </div>
  );
};
