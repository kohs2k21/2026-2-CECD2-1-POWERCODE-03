import { useEffect, useState } from "react";
import { IconRefresh } from "@tabler/icons-react";
import { SectionCard } from "@/components/layout/SectionCard";
import { DescriptionList } from "@/components/layout/DescriptionList";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useOperationsQuery } from "../data/useOperationsQuery";
import { operationStageIds, operationsFreshness, operationsReadUnavailable } from "../data/operations";

export const stageLabels = {
  collector: "Collector 수집",
  redaction: "민감 필드 제거",
  observationQueue: "관측 저장 · ID 큐 등록",
  detector: "Detector 조회 · 추론",
  judgmentStore: "판정 저장",
};
export const stateLabels = { healthy: "정상", degraded: "지연 · 주의", stopped: "중단", unknown: "확인 불가" };
const stateVariant = { healthy: "success", degraded: "warning", stopped: "critical", unknown: "secondary" } as const;

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

/** 수집·탐지 운영 관측(GET /api/operations/overview). 이전 데이터가 있어도 실패·오래됨을 정상으로 표시하지 않는다. */
export const OperationsObservation = () => {
  const query = useOperationsQuery();
  const fresh = useObservationFreshness(query.data);
  const label = query.isPending
    ? "운영 관측 조회 중"
    : query.isError
      ? operationsReadUnavailable(query.error)
        ? "운영 관측 미연결"
        : "운영 관측 조회 실패"
      : fresh === "stale"
        ? "운영 관측 오래됨"
        : fresh === "unknown"
          ? "운영 상태 확인 불가"
          : "운영 관측 수신";
  const reliable = fresh === "fresh" && !query.isError;
  return (
    <SectionCard
      title="수집 · 탐지 운영 관측"
      aria-label="수집 · 탐지 운영 관측"
      action={
        <Button variant="outline" size="sm" onClick={() => void query.refetch()} disabled={query.isFetching}>
          <IconRefresh aria-hidden="true" />
          {query.isFetching ? "운영 관측 조회 중" : "운영 관측 새로고침"}
        </Button>
      }
    >
      <div role="status" className="flex flex-wrap items-center gap-2">
        <Badge variant={reliable ? "success" : query.isError ? "critical" : "secondary"}>{label}</Badge>
      </div>
      <DescriptionList
        items={[
          { label: "최근 관측", value: query.data?.observedAt ?? "— 확인 불가" },
          { label: "현재 구성", value: query.data?.activeConfigurationVersion ?? "— 확인 불가" },
          {
            label: "로그 발생 → 판정",
            value: query.data?.endToEndLatencyMs === undefined ? "— 확인 불가" : `${query.data.endToEndLatencyMs} ms`,
          },
        ]}
      />
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>
              <span className="sr-only">단계</span>
            </TableHead>
            <TableHead>상태</TableHead>
            <TableHead>대기량</TableHead>
            <TableHead>최장 대기</TableHead>
            <TableHead>최근 처리</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {operationStageIds.map((id) => {
            const stage = query.data?.stages[id];
            const state = reliable ? (stage?.state ?? "unknown") : "unknown";
            return (
              <TableRow key={id}>
                <TableCell className="font-medium">{stageLabels[id]}</TableCell>
                <TableCell>
                  <Badge variant={stateVariant[state]}>{stateLabels[state]}</Badge>
                </TableCell>
                <TableCell>{stage?.pendingCount === undefined ? "—" : `${stage.pendingCount}건`}</TableCell>
                <TableCell>{stage?.oldestWaitMs === undefined ? "—" : `${stage.oldestWaitMs} ms`}</TableCell>
                <TableCell>{stage?.lastProcessedAt ?? "—"}</TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      <p className="text-sm text-muted-foreground">
        미제공 항목: — 확인 불가{query.data && (fresh === "stale" || query.isError) ? " · 이전 관측값 표시" : ""}
      </p>
    </SectionCard>
  );
};
