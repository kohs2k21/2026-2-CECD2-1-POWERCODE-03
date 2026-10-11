import { useEffect, useState } from "react";
import { useOperationsQuery } from "../data/useOperationsQuery";
import { operationStageIds, operationsFreshness, operationsReadUnavailable } from "../data/operations";
export const stageLabels = { collector: "Collector 수집", redaction: "민감 필드 제거", observationQueue: "관측 저장 · ID 큐 등록", detector: "Detector 조회 · 추론", judgmentStore: "판정 저장" };
export const stateLabels = { healthy: "정상", degraded: "지연 · 주의", stopped: "중단", unknown: "확인 불가" };
export const useObservationFreshness = (data: Parameters<typeof operationsFreshness>[0]) => {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    setNow(Date.now());
    if (!data) return;
    const delay = Date.parse(data.observedAt) + data.maxAgeMs - Date.now() + 1;
    if (delay <= 0) return;
    const timer = window.setTimeout(() => setNow(Date.now()), Math.min(delay, 2_147_483_647));
    return () => window.clearTimeout(timer);
  }, [data]);
  return operationsFreshness(data, now);
};
export const OperationsObservation = () => {
  const query = useOperationsQuery();
  const fresh = useObservationFreshness(query.data);
  const label = query.isPending ? "운영 관측 조회 중" : query.isError ? operationsReadUnavailable(query.error) ? "운영 관측 미연결" : "운영 관측 조회 실패" : fresh === "stale" ? "운영 관측 오래됨" : fresh === "unknown" ? "운영 상태 확인 불가" : "운영 관측 수신";
  const reliable = fresh === "fresh" && !query.isError;
  return <section className="operations-observation" aria-label="수집 · 탐지 운영 관측">
    <div className="operations-observation__summary" role="status">
      <strong>{label}</strong><span>최근 관측: {query.data?.observedAt ?? "— 확인 불가"}</span>
      <span>현재 구성: {query.data?.activeConfigurationVersion ?? "— 확인 불가"}</span>
      <span>로그 발생 → 판정: {query.data?.endToEndLatencyMs === undefined ? "— 확인 불가" : `${query.data.endToEndLatencyMs} ms`}</span>
    </div>
    <div className="operations-observation__stages">{operationStageIds.map(id => {
      const stage = query.data?.stages[id];
      return <article key={id}><strong>{stageLabels[id]}</strong><span>{reliable ? stateLabels[stage?.state ?? "unknown"] : "확인 불가"}</span><dl>
        <div><dt>대기량</dt><dd>{stage?.pendingCount === undefined ? "—" : `${stage.pendingCount}건`}</dd></div>
        <div><dt>최장 대기</dt><dd>{stage?.oldestWaitMs === undefined ? "—" : `${stage.oldestWaitMs} ms`}</dd></div>
        <div><dt>최근 처리</dt><dd>{stage?.lastProcessedAt ?? "—"}</dd></div>
      </dl></article>;
    })}</div>
    <p>미제공 항목: — 확인 불가{query.data && (fresh === "stale" || query.isError) ? " · 이전 관측값 표시" : ""}</p>
  </section>;
};
