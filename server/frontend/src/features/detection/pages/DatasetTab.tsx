import { useCreateDraft } from "../data/createDraft";
import type { DetectionData } from "../data/types";
import { ServiceAction } from "../components/ServiceAction";
export const DatasetTab = ({ data }: { data: DetectionData }) => {
  const { draft, update } = useCreateDraft();
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
            <dt>원본·추출 기준</dt>
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
    </div>
  );
};
