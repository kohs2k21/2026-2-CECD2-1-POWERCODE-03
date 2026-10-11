import { useSearchParams } from "react-router-dom";
import { Button } from "../../../components/ui/button";
import { EmptyState } from "../../../components/ui/feedback";
import { featureCatalog } from "../data/catalog";
import { featureOptions } from "../data/formula";
import { defaultFeatureEditor } from "../data/featureBuilder";
import { appendCustomFeature, useCreateDraft } from "../data/createDraft";
import { FeatureEditor } from "./FeatureEditor";
const readinessLabels = {
  ready: "입력 검증 필요",
  "fit-required": "fit 필요",
  unconfirmed: "정의 확인 필요",
  unsupported: "직접 입력 미지원",
  deferred: "보류",
};
export const FeaturesTab = () => {
  const { draft, update } = useCreateDraft();
  const definitions = featureOptions(draft);
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
  const customListed = draft.customFeatureIds.filter(
    (id) =>
      kind !== "raw" &&
      `${draft.featureEdits[id]?.name ?? ""} ${id}`
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
    const created = appendCustomFeature(draft, defaultFeatureEditor());
    update(created.draft);
    setParam("feature", created.id);
  };
  return (
    <div className="detection-stack">
      <div className="detection-grid feature-workspace">
        <section className="detection-card">
          <div className="detection-page-header">
            <h2>원본·파생 피처 목록</h2>
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
              <option value="raw">원본</option>
              <option value="derived">파생</option>
            </select>
          </label>
          <div className="detection-table-wrap detection-catalog detection-feedback">
            <table className="detection-table">
              <thead>
                <tr>
                  <th>선택</th>
                  <th>속성</th>
                  <th>유형</th>
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
                    <td>{feature.source === "derived" ? "파생" : "원본"}</td>
                    <td>{feature.type}</td>
                    <td>{readinessLabels[feature.readiness]}</td>
                  </tr>
                ))}
                {customListed.map((id) => (
                  <tr
                    key={id}
                    className={
                      selectedId === id ? "detection-selection" : undefined
                    }
                  >
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
                    <td>파생</td>
                    <td>
                      {definitions.find((feature) => feature.id === id)?.type ??
                        "확인 필요"}
                    </td>
                    <td>
                      {definitions.find((feature) => feature.id === id)
                        ?.reason ?? "편집 검증 필요"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {listed.length === 0 && customListed.length === 0 && (
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
