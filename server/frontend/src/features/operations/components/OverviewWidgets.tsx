import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { IconChevronRight } from "@tabler/icons-react";
import { paths } from "@/app/routes";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { Progress } from "@/components/ui/progress";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import type { ProcessDecision } from "@/services/api/pipeline.api";
import { operationsFreshness, type ActiveConfiguration, type OperationsOverview } from "../data/operations";
import {
  useCollectionQuery,
  useOperationsQuery,
  useRecentDecisionsQuery,
  useStorageQuery,
  useTrendQuery,
} from "../data/useOperationsQuery";
import type { WidgetId } from "../data/widgetLayout";
import { readErrorTitle } from "../data/operationsReads";
import { inputModeLabel } from "./inputModeText";
import { DASH, UNMEASURED, bytesText, dayText, durationText, msText, numberText, percentText, stampText } from "../format";

const errorTitle = readErrorTitle;

/** 조회 상태 공통 처리: 최초 로딩 / 실패(이전 값 없음) / 이전 값 + 실패 표시. 실패를 정상·빈 값으로 바꾸지 않는다. */
const QueryState = <T,>({
  query,
  what,
  children,
}: {
  query: { data: T | undefined; isPending: boolean; isError: boolean; error: unknown; refetch: () => unknown };
  what: string;
  children: (data: T) => ReactNode;
}) => {
  if (query.isPending && query.data === undefined) return <LoadingState />;
  if (query.data === undefined)
    return <ErrorState title={errorTitle(query.error, what)} onRetry={() => void query.refetch()} />;
  return (
    <>
      {query.isError && <p className="text-sm text-destructive-text">{errorTitle(query.error, what)} · 이전 관측값 표시</p>}
      {children(query.data)}
    </>
  );
};

/** 서버 신선도(observedAt + maxAgeMs)가 지나면 다시 그린다. */
export const useObservationFreshness = (data: OperationsOverview | undefined) => {
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

const ObservationNotice = ({ data }: { data: OperationsOverview }) => {
  const fresh = useObservationFreshness(data);
  if (fresh === "fresh") return null;
  return (
    <p className="text-sm text-muted-foreground">
      {fresh === "stale" ? "운영 관측 오래됨" : "운영 상태 확인 불가"} · 최근 관측 {stampText(data.observedAt, true)}
    </p>
  );
};

/** 현재 운영 구성 한 줄(헤더 "현재 운영: …"·위젯 공용). */
export const modelsText = (config: ActiveConfiguration) =>
  config.models.length ? config.models.map((m) => (m.adapterName ? `${m.modelKey}(${m.adapterName})` : m.modelKey)).join(", ") : "없음";
/** 구성 없이(v04) 운영: 룰 세트 버전·활성 모델. 버전을 읽지 못하면 null(구성 없음으로 표시). */
const operationalStateParts = (overview: OperationsOverview) => {
  const state = overview.operationalState;
  if (!state || state.ruleSetRevision === undefined) return null;
  return { rules: `버전 ${state.ruleSetRevision}`, models: state.models.length ? state.models.map((m) => m.modelKey).join(", ") : "없음" };
};
export const activeConfigurationText = (overview: OperationsOverview | undefined) => {
  if (!overview || overview.activeConfiguration === undefined) return `${DASH} 확인 불가`;
  if (overview.activeConfiguration === null) {
    const state = operationalStateParts(overview);
    return state ? `룰 ${state.rules} · 머신러닝 모델 ${state.models}` : "운영 구성 없음";
  }
  const config = overview.activeConfiguration;
  return `룰 ${config.ruleCount === undefined ? DASH : `${config.ruleCount}개`} · 머신러닝 모델 ${modelsText(config)}`;
};

const FlowWidget = () => {
  const query = useOperationsQuery();
  return (
    <QueryState query={query} what="운영 관측">
      {(data) => {
        const steps: [string, string][] = [
          ["수집", stampText(data.stages.collector?.lastProcessedAt, true)],
          ["정제", stampText(data.stages.redaction?.lastProcessedAt, true)],
          ["큐", data.stages.observationQueue?.pendingCount === undefined ? `대기 ${DASH}` : `대기 ${numberText(data.stages.observationQueue.pendingCount)}`],
          ["Detector", stampText(data.stages.detector?.lastProcessedAt, true)],
          ["저장", stampText(data.stages.judgmentStore?.lastProcessedAt, true)],
        ];
        return (
          <div className="grid gap-3">
            <ObservationNotice data={data} />
            <ol className="flex flex-wrap items-center gap-1 text-sm" aria-label="단계 흐름">
              {steps.map(([label, value], index) => (
                <li key={label} className="flex items-center gap-1">
                  {index > 0 && <IconChevronRight className="size-4 text-muted-foreground" aria-hidden="true" />}
                  <span className="rounded-md border px-2 py-1">
                    {label} · {value}
                  </span>
                </li>
              ))}
            </ol>
            <p className="text-sm text-muted-foreground">
              원천 발생 → 판정: {data.endToEndLatencyMs === undefined ? UNMEASURED : msText(data.endToEndLatencyMs)}
            </p>
          </div>
        );
      }}
    </QueryState>
  );
};

const decisionResult = (decision: ProcessDecision) =>
  decision.evidence?.anomalous === true ? "이상 확인" : decision.evidence?.anomalous === false ? "선택 경로 정상" : "정상 여부 미확인";

const RecentWidget = () => {
  const query = useRecentDecisionsQuery();
  return (
    <QueryState query={query} what="최근 판정">
      {(rows) =>
        rows.length ? (
          <ul className="grid gap-2 text-sm">
            {rows.map((decision) => (
              <li key={decision.id} className="flex flex-wrap items-baseline gap-x-2">
                <Link to={paths.analysisDecision(decision.id)} className="font-medium text-link underline-offset-4 hover:underline" aria-label={`판정 ${decision.id}`}>
                  {decision.id.slice(0, 8)}
                </Link>
                <span>
                  {decisionResult(decision)}
                  {decision.decisionCode ? ` · ${decision.decisionCode}` : ""}
                </span>
                <span className="text-muted-foreground">{stampText(decision.decidedAt, true)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState>저장된 판정이 없습니다.</EmptyState>
        )
      }
    </QueryState>
  );
};

const TrendWidget = () => {
  const query = useTrendQuery();
  return (
    <QueryState query={query} what="판정 추이">
      {(trend) => (
        <Table>
          <TableHeader>
            <TableRow>
              {["날짜", "판정", "이상 신호", "정상 여부 미확인"].map((title) => (
                <TableHead key={title}>{title}</TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {trend.items.map((item) => (
              <TableRow key={item.day}>
                <TableCell>{dayText(item.day)}</TableCell>
                <TableCell>{numberText(item.decisionCount)}</TableCell>
                <TableCell>{item.anomalySignalRatio === null ? DASH : percentText(item.anomalySignalRatio)}</TableCell>
                <TableCell>{item.unavailableRatio === null ? DASH : percentText(item.unavailableRatio)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </QueryState>
  );
};

const ActiveWidget = () => {
  const query = useOperationsQuery();
  const { user } = useAuthSession();
  return (
    <QueryState query={query} what="운영 관측">
      {(data) => {
        const config = data.activeConfiguration;
        const state = config === null ? operationalStateParts(data) : null;
        return (
          <div className="grid gap-2 text-sm">
            {config === undefined ? (
              <p>{DASH} 확인 불가</p>
            ) : config === null && state ? (
              <>
                <p>
                  <strong>룰</strong> · {state.rules}
                </p>
                <p>
                  <strong>머신러닝 모델</strong> · {state.models}
                </p>
              </>
            ) : config === null ? (
              <p>운영 구성 없음</p>
            ) : (
              <>
                <p>
                  <strong>룰</strong> · {config.ruleCount === undefined ? DASH : `${config.ruleCount}개`}
                </p>
                <p>
                  <strong>머신러닝 모델</strong> · {modelsText(config)}
                </p>
              </>
            )}
            {user?.userType === "admin" && (
              <Link to={paths.detectionOperations} className="text-link underline-offset-4 hover:underline">
                운영 설정
              </Link>
            )}
          </div>
        );
      }}
    </QueryState>
  );
};

const KeyValueTable = ({ rows }: { rows: [string, string][] }) => (
  <Table>
    <TableBody>
      {rows.map(([label, value]) => (
        <TableRow key={label}>
          <TableHead scope="row" className="font-normal text-muted-foreground">
            {label}
          </TableHead>
          <TableCell>{value}</TableCell>
        </TableRow>
      ))}
    </TableBody>
  </Table>
);

/** 큐 적체: 개요(전체 원천) 기준 대기 수·최장 대기·큐 → Detector p95. */
const QueueWidget = () => {
  const query = useOperationsQuery();
  return (
    <QueryState query={query} what="운영 관측">
      {(data) => {
        const queue = data.stages.observationQueue;
        return (
          <KeyValueTable
            rows={[
              ["판정 큐 대기", queue?.pendingCount === undefined ? DASH : `${numberText(queue.pendingCount)}건`],
              ["가장 오래된 대기", queue?.oldestWaitMs === undefined ? DASH : durationText(queue.oldestWaitMs)],
              ["큐 → Detector p95", data.queueWaitP95Ms === undefined ? UNMEASURED : durationText(data.queueWaitP95Ms)],
            ]}
          />
        );
      }}
    </QueryState>
  );
};

/** 데이터 출처 선택 상태: 조회 중 / 실패 / 없음("") / 원천 ID. */
export type SourceState = { status: "loading" } | { status: "error"; title: string; retry: () => void } | { status: "ready"; id: string };
const sourceIdOf = (source: SourceState) => (source.status === "ready" ? source.id : "");

const CollectWidget = ({ source, collectHref }: { source: SourceState; collectHref: string }) => {
  const query = useCollectionQuery(sourceIdOf(source));
  if (source.status === "loading") return <LoadingState />;
  if (source.status === "error") return <ErrorState title={source.title} onRetry={source.retry} />;
  if (!source.id) return <EmptyState>저장된 데이터 출처가 없습니다.</EmptyState>;
  return (
    <QueryState query={query} what="수집 현황">
      {(data) => {
        const process = data.layers.find((layer) => layer.layer === "P");
        return (
          <div className="grid gap-2 text-sm">
            <p>입력 모드 · {data.input ? inputModeLabel[data.input.mode] : `${DASH} 확인 불가`}</p>
            <p className="text-muted-foreground">
              PROCESS 누적 {process?.storedCount == null ? DASH : numberText(process.storedCount)} · 마지막 저장{" "}
              {stampText(process?.lastPersistedAt, true)}
            </p>
            <Link to={collectHref} className="text-link underline-offset-4 hover:underline">
              수집 탭
            </Link>
          </div>
        );
      }}
    </QueryState>
  );
};

const StorageWidget = () => {
  const query = useStorageQuery();
  return (
    <QueryState query={query} what="저장공간">
      {(data) => {
        const disk = data.disk;
        const used = disk?.capacityBytes && disk.usedBytes !== null ? disk.usedBytes / disk.capacityBytes : null;
        return (
          <div className="grid gap-2 text-sm">
            <p>데이터 마운트 {used === null ? `· ${UNMEASURED}` : `${percentText(used)} 사용`}</p>
            {used !== null && <Progress value={Math.round(used * 100)} aria-label="데이터 마운트 사용률" />}
            <p className="text-muted-foreground">
              PostgreSQL {data.databaseBytes === null ? UNMEASURED : bytesText(data.databaseBytes)}
              {disk?.availableBytes != null ? ` · 여유 ${bytesText(disk.availableBytes)}` : ""}
            </p>
          </div>
        );
      }}
    </QueryState>
  );
};

/** 위젯 본문. 배치에 있는 위젯만 렌더되므로 조회도 배치된 위젯만 한다. */
export const WidgetBody = ({ id, source, collectHref }: { id: WidgetId; source: SourceState; collectHref: string }) => {
  switch (id) {
    case "flow":
      return <FlowWidget />;
    case "recent":
      return <RecentWidget />;
    case "trend":
      return <TrendWidget />;
    case "active":
      return <ActiveWidget />;
    case "queue":
      return <QueueWidget />;
    case "collect":
      return <CollectWidget source={source} collectHref={collectHref} />;
    case "storage":
      return <StorageWidget />;
  }
};
