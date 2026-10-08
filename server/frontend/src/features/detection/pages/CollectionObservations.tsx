import type { DetectionData, StorageObservation } from "../data/types";
import { valueText } from "../components/DetectionQueryBoundary";
import { bytesText, storageAssessment } from "./operationPresentation";
import { EmptyState } from "../../../components/ui/feedback";

const StorageCard = ({ observation }: { observation: StorageObservation }) => {
  const assessment = storageAssessment(observation);
  return (
    <article className="detection-card">
      <h3>{observation.name}</h3>
      <p
        className={
          assessment.insufficient || observation.state === "failed"
            ? "detection-error"
            : "detection-status"
        }
        role="status"
      >
        {assessment.label}
      </p>
      <dl className="detection-summary">
        <div>
          <dt>실제 저장 볼륨</dt>
          <dd>{valueText(observation.path)}</dd>
        </div>
        <div>
          <dt>관측 시각</dt>
          <dd>{valueText(observation.observedAt)}</dd>
        </div>
        <div>
          <dt>전체 용량</dt>
          <dd>{bytesText(observation.totalBytes)}</dd>
        </div>
        <div>
          <dt>사용 가능 용량</dt>
          <dd>{bytesText(observation.availableBytes)}</dd>
        </div>
        <div>
          <dt>저장소 할당량</dt>
          <dd>{bytesText(observation.quotaBytes)}</dd>
        </div>
        <div>
          <dt>볼륨 사용률</dt>
          <dd>
            {assessment.usedPercent == null
              ? "—"
              : `${assessment.usedPercent.toFixed(1)}%`}
          </dd>
        </div>
        <div>
          <dt>사용률 경고 기준</dt>
          <dd>{observation.warningUsedPercent}% 이상</dd>
        </div>
        <div>
          <dt>사용 가능 용량 경고 기준</dt>
          <dd>{bytesText(observation.warningAvailableBytes)} 미만</dd>
        </div>
      </dl>
      {observation.error && (
        <p className="detection-error" role="alert">
          {observation.error}
        </p>
      )}
      {observation.state !== "fresh" && (
        <p className="detection-note">
          현재 용량 상태를 확정할 수 없습니다. 값이 있으면 마지막 관측값입니다.
        </p>
      )}
      <p className="detection-note">
        사용률 또는 남은 절대 용량 중 하나라도 경고 기준에 도달하면 부족 경고를
        표시합니다. 볼륨 사용률과 저장소 할당량은 별도 값입니다.
      </p>
    </article>
  );
};

export const CollectionObservations = ({ data }: { data: DetectionData }) => {
  const { latency } = data;
  const measured =
    latency.sourceTimestampAvailable &&
    latency.clockSynchronized &&
    latency.endToEndMs != null &&
    Number.isFinite(latency.endToEndMs) &&
    latency.endToEndMs >= 0;
  const latencyText = (value: number | null) =>
    value != null && Number.isFinite(value) && value >= 0
      ? `${value.toLocaleString("ko-KR")} ms`
      : "—";
  return (
    <>
      <section aria-labelledby="storage-title">
        <h2 id="storage-title">저장소 용량</h2>
        {data.storage.length ? (
          <div className="detection-grid">
            {data.storage.map((observation) => (
              <StorageCard key={observation.id} observation={observation} />
            ))}
          </div>
        ) : (
          <EmptyState>저장소 관측 정보가 없습니다.</EmptyState>
        )}
      </section>
      <section className="detection-card" aria-labelledby="latency-title">
        <h2 id="latency-title">발생 기준 탐지 지연</h2>
        <p className="detection-note">
          목표 2–3초 · ESB 로그 발생부터 이상 판정까지
        </p>
        <p
          className={
            measured && latency.endToEndMs! > 3000
              ? "detection-error"
              : "detection-status"
          }
          role="status"
        >
          {measured
            ? latency.endToEndMs! > 3000
              ? "목표 지연 초과"
              : "발생 기준 실측값"
            : "발생 기준 전체 지연 미측정"}
        </p>
        <dl className="detection-summary">
          <div>
            <dt>발생 → 이상 판정</dt>
            <dd>{measured ? latencyText(latency.endToEndMs) : "—"}</dd>
          </div>
          <div>
            <dt>화면 전달 지연</dt>
            <dd>{latencyText(latency.uiDeliveryMs)}</dd>
          </div>
          <div>
            <dt>XAI 설명 지연</dt>
            <dd>{latencyText(latency.xaiMs)}</dd>
          </div>
          <div>
            <dt>발생 시각 가용성</dt>
            <dd>{latency.sourceTimestampAvailable ? "확인됨" : "미확인"}</dd>
          </div>
          <div>
            <dt>시계 동기화</dt>
            <dd>{latency.clockSynchronized ? "확인됨" : "미확인"}</dd>
          </div>
        </dl>
        <p className="detection-note">
          API 응답 수신 시각을 발생 시각으로 대체하지 않습니다. 화면 전달·XAI
          지연은 전체 판정 지연과 구분합니다.
        </p>
      </section>
    </>
  );
};
