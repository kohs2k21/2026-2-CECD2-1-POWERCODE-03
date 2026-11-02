import { Suspense, lazy, useState, type ReactNode } from "react";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { Label } from "@/components/ui/label";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import { inputErrorText, useSourcesQuery } from "../data/inputs";
import { layers, readErrorTitle, type CollectionView, type Layer, type LatencyStage, type LatencyStageId } from "../data/operationsReads";
import { useCollectionQuery, useLatencyQuery, useSourceFieldsQuery } from "../data/useOperationsQuery";
import { DASH, UNMEASURED, durationText, msText, numberText, percentText, stampText } from "../format";
import { InputControls, InputModeSummary } from "./InputControls";
import { inputRunStatusLabel } from "./inputModeText";

// 차트 라이브러리(Recharts)는 수집 탭에서만 쓰므로 따로 불러온다.
const HourlyTransactionsChart = lazy(() => import("./HourlyTransactionsChart").then((module) => ({ default: module.HourlyTransactionsChart })));

const layerNames: Record<Layer, string> = { T: "TRANSACTION", P: "PROCESS", M: "MESSAGE", B: "BODY" };
const errorTitle = readErrorTitle;

type Read<T> = { data: T | undefined; isPending: boolean; isError: boolean; error: unknown; refetch: () => unknown };
const Reading = <T,>({ query, what, children }: { query: Read<T>; what: string; children: (data: T) => ReactNode }) =>
  query.data !== undefined ? (
    <>
      {query.isError && <p className="text-sm text-destructive-text">{errorTitle(query.error, what)} · 이전 관측값 표시</p>}
      {children(query.data)}
    </>
  ) : query.isPending ? (
    <LoadingState />
  ) : (
    <ErrorState title={errorTitle(query.error, what)} onRetry={() => void query.refetch()} />
  );

const Columns = ({ titles }: { titles: string[] }) => (
  <TableHeader>
    <TableRow>
      {titles.map((title) => (
        <TableHead key={title}>{title}</TableHead>
      ))}
    </TableRow>
  </TableHeader>
);

const StoredCounts = ({ data }: { data: CollectionView }) => (
  <Table>
    <Columns titles={["원본", "누적 저장", "최근 1분", "마지막 저장", "revision 재도착"]} />
    <TableBody>
      {data.layers.map((row) => (
        <TableRow key={row.layer}>
          <TableCell className="font-medium">
            {layerNames[row.layer]} ({row.layer})
          </TableCell>
          <TableCell>{row.storedCount === null ? DASH : numberText(row.storedCount)}</TableCell>
          {row.layer === "B" ? (
            <>
              <TableCell>수집 안 함 · 판정 입력 아님</TableCell>
              <TableCell>{DASH}</TableCell>
              <TableCell>{DASH}</TableCell>
            </>
          ) : (
            <>
              <TableCell>{row.lastMinuteCount === null ? DASH : `+${numberText(row.lastMinuteCount)}`}</TableCell>
              <TableCell>{stampText(row.lastPersistedAt, true)}</TableCell>
              <TableCell>{row.revisionArrivalCount === null ? DASH : `${numberText(row.revisionArrivalCount)}건`}</TableCell>
            </>
          )}
        </TableRow>
      ))}
    </TableBody>
  </Table>
);

const stageNames: Record<LatencyStageId, string> = {
  collector: "Collector 수집",
  persist: "정제·저장",
  detectionQueue: "판정 큐 대기",
  detector: "Detector 판정",
  judgmentStore: "판정 저장",
};
const backlogText = (stage: LatencyStage) =>
  stage.queuePending === null
    ? DASH
    : `${numberText(stage.queuePending)}건${stage.oldestWaitMs === null ? "" : ` · 최장 ${durationText(stage.oldestWaitMs)}`}`;

const StageLatency = ({ sourceId }: { sourceId: string }) => {
  const query = useLatencyQuery(sourceId);
  return (
    <Reading query={query} what="단계별 지연">
      {(data) => (
        <>
          <Table>
            <Columns titles={["단계", "p50", "p95", "대기·적체", "최근 실패"]} />
            <TableBody>
              {data.stages.map((stage) => (
                <TableRow key={stage.id}>
                  <TableCell className="font-medium">{stageNames[stage.id]}</TableCell>
                  <TableCell>{stage.p50Ms === null ? UNMEASURED : msText(stage.p50Ms)}</TableCell>
                  <TableCell>{stage.p95Ms === null ? UNMEASURED : msText(stage.p95Ms)}</TableCell>
                  <TableCell>{backlogText(stage)}</TableCell>
                  <TableCell>{stage.recentFailures === null ? DASH : `${numberText(stage.recentFailures)}건`}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <p className="text-sm">
            원천 발생 → 판정: <strong>{data.endToEndP50Ms === null ? UNMEASURED : msText(data.endToEndP50Ms)}</strong>
          </p>
        </>
      )}
    </Reading>
  );
};

const SourceFields = ({ sourceId }: { sourceId: string }) => {
  const [layer, setLayer] = useState<Layer>("T");
  const query = useSourceFieldsQuery(sourceId, layer);
  return (
    <>
      <div className="grid max-w-xs gap-2">
        <Label htmlFor="raw-source">원본</Label>
        <NativeSelect id="raw-source" value={layer} onChange={(event) => setLayer(event.target.value as Layer)}>
          {layers.map((item) => (
            <NativeSelectOption key={item} value={item}>
              {layerNames[item]}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      </div>
      <Reading query={query} what="원본 필드">
        {(data) => {
          const sample = data.sampleSize;
          const flagsFor = (name: string) => data.qualityFlags.filter((flag) => flag.flag.endsWith(`:${name}`));
          const general = data.qualityFlags.filter((flag) => !flag.flag.includes(":"));
          return (
            <Table>
              <Columns titles={["원본", "필드·지원 여부", "결측·품질"]} />
              <TableBody>
                {data.layer === "B" ? (
                  <TableRow>
                    <TableCell>{layerNames.B}</TableCell>
                    <TableCell>수집 안 함 · 판정 입력 아님</TableCell>
                    <TableCell>{DASH}</TableCell>
                  </TableRow>
                ) : sample === 0 ? (
                  <TableRow>
                    <TableCell>{layerNames[data.layer]}</TableCell>
                    <TableCell>저장된 관측 없음</TableCell>
                    <TableCell>{DASH}</TableCell>
                  </TableRow>
                ) : (
                  <>
                    {data.fields.map((field) => {
                      const absent = field.nullCount + field.missingCount;
                      const flags = flagsFor(field.name);
                      return (
                        <TableRow key={field.name}>
                          <TableCell>{layerNames[data.layer]}</TableCell>
                          <TableCell>
                            {field.name} · {field.presentCount + field.nullCount > 0 ? "제공" : "미제공"}
                          </TableCell>
                          <TableCell>
                            결측 {percentText(absent / sample)} ({numberText(absent)}/{numberText(sample)})
                            {flags.map((flag) => ` · ${flag.flag} ${numberText(flag.count)}건`).join("")}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                    {general.length > 0 && (
                      <TableRow>
                        <TableCell>{layerNames[data.layer]}</TableCell>
                        <TableCell>{DASH}</TableCell>
                        <TableCell>{general.map((flag) => `${flag.flag} ${numberText(flag.count)}건`).join(" · ")}</TableCell>
                      </TableRow>
                    )}
                  </>
                )}
              </TableBody>
            </Table>
          );
        }}
      </Reading>
    </>
  );
};

const resultVariant = (status: string | null) =>
  status === "failed" ? "critical" : status === "running" ? "info" : status === "completed" ? "success" : "secondary";
const InputHistory = ({ data }: { data: CollectionView }) =>
  data.history.length ? (
    <Table>
      <Columns titles={["시각", "입력", "작업", "결과"]} />
      <TableBody>
        {data.history.map((run) => (
          <TableRow key={run.id}>
            <TableCell>{stampText(run.createdAt)}</TableCell>
            <TableCell>{run.mode === "csv" ? "CSV 재생" : "실서버 API"}</TableCell>
            <TableCell>{run.mode === "csv" ? `재생 · ${run.replayKey ?? DASH}` : "수집"}</TableCell>
            <TableCell>
              <Badge variant={resultVariant(run.status)}>{run.status ? (inputRunStatusLabel[run.status] ?? run.status) : DASH}</Badge>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  ) : (
    <EmptyState>입력 실행 이력이 없습니다.</EmptyState>
  );

/** 수집 탭 카드(와이어프레임 ops/collect). 일반 사용자는 읽기 전용, 입력 모드 변경은 관리자만. */
const CollectCards = ({ sourceId }: { sourceId: string }) => {
  const { user } = useAuthSession();
  const admin = user?.userType === "admin";
  const collection = useCollectionQuery(sourceId);
  return (
    <>
      <SectionCard title="시간별 트랜잭션 양">
        <Reading query={collection} what="수집 현황">
          {(data) => (
            <Suspense fallback={<LoadingState />}>
              <HourlyTransactionsChart items={data.hourly} />
            </Suspense>
          )}
        </Reading>
      </SectionCard>
      {admin ? (
        <InputControls key={sourceId} sourceId={sourceId} />
      ) : (
        <SectionCard title="입력 모드">
          <Reading query={collection} what="수집 현황">
            {(data) => (data.input ? <InputModeSummary input={data.input} /> : <p className="text-sm">현재: <strong>{DASH} 확인 불가</strong></p>)}
          </Reading>
        </SectionCard>
      )}
      <SectionCard title="원본별 저장 건수">
        <Reading query={collection} what="수집 현황">
          {(data) => <StoredCounts data={data} />}
        </Reading>
      </SectionCard>
      <SectionCard title="단계별 지연">
        <StageLatency sourceId={sourceId} />
      </SectionCard>
      <SectionCard title="원본 필드">
        <SourceFields sourceId={sourceId} />
      </SectionCard>
      <SectionCard title="수집 이력">
        <Reading query={collection} what="수집 현황">
          {(data) => <InputHistory data={data} />}
        </Reading>
      </SectionCard>
    </>
  );
};

/** 수집 탭: 데이터 출처(여러 개일 때 선택) → 카드. */
export const CollectTab = () => {
  const sources = useSourcesQuery();
  const [selected, setSelected] = useState("");
  const sourceId = sources.data?.find((source) => source.id === selected)?.id ?? sources.data?.[0]?.id ?? "";
  if (sources.isPending) return <LoadingState />;
  if (sources.isError) return <ErrorState title={inputErrorText(sources.error)} onRetry={() => void sources.refetch()} />;
  if (!sourceId) return <EmptyState>저장된 데이터 출처가 없습니다. 먼저 Collector로 로그를 저장하세요.</EmptyState>;
  return (
    <div className="grid gap-4">
      {(sources.data?.length ?? 0) > 1 && (
        <div className="grid max-w-sm gap-2">
          <Label htmlFor="collect-source">출처</Label>
          <NativeSelect id="collect-source" value={sourceId} onChange={(event) => setSelected(event.target.value)}>
            {sources.data?.map((source) => (
              <NativeSelectOption key={source.id} value={source.id}>
                {source.displayName} ({source.datasetKey})
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </div>
      )}
      <CollectCards key={sourceId} sourceId={sourceId} />
    </div>
  );
};

