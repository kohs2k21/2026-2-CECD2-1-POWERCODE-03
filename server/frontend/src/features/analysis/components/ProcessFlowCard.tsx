import { useState } from "react";
import { IconBinaryTree, IconCodeDots, IconDatabase, IconListDetails, IconMessage2 } from "@tabler/icons-react";
import { SectionCard } from "@/components/layout/SectionCard";
import { ErrorState, LoadingState } from "@/components/ui/feedback";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { analysisErrorTitle, type DecisionFlow, type FlowMessage, type FlowProcess } from "../data/analysisApi";
import { DASH, UNAVAILABLE } from "../labels";

type Node =
  | { key: string; kind: "tx"; level: 0 }
  | { key: string; kind: "process"; level: 1; process: FlowProcess }
  | { key: string; kind: "message"; level: 1 | 2; message: FlowMessage };

/**
 * 트리 순서: Transaction → Process(저장 순서) → 그 Process ID의 Message. 같은 Process ID 노드가 여럿이면 첫 노드 아래에 둔다.
 * Process ID가 맞는 노드가 없는 Message는 Process와 같은 단계로 끝에 둔다(부모를 만들지 않음).
 */
export const flowNodes = (flow: DecisionFlow): Node[] => {
  const nodes: Node[] = flow.transaction ? [{ key: "tx", kind: "tx", level: 0 }] : [];
  const placed = new Set<string>();
  for (const process of flow.processes) {
    nodes.push({ key: process.observationId, kind: "process", level: 1, process });
    if (process.processId === null || placed.has(process.processId)) continue;
    placed.add(process.processId);
    for (const message of flow.messages.filter((m) => m.processId === process.processId))
      nodes.push({ key: message.observationId, kind: "message", level: 2, message });
  }
  for (const message of flow.messages.filter((m) => m.processId === null || !placed.has(m.processId)))
    nodes.push({ key: message.observationId, kind: "message", level: 1, message });
  return nodes;
};

const statusOf = (node: Node, flow: DecisionFlow) =>
  (node.kind === "tx" ? flow.transaction?.status : node.kind === "process" ? node.process.status : node.message.status) ?? DASH;
const labelOf = (node: Node, flow: DecisionFlow) =>
  node.kind === "tx"
    ? `Transaction: ${flow.transaction?.transactionId ?? DASH}`
    : node.kind === "process"
      ? `${node.process.focus ? "Focus Process" : "Process"}: ${node.process.processId ?? DASH}`
      : `Message: ${node.message.direction ?? DASH} · ${node.message.messageId ?? DASH}`;
/** 선택 노드 원본 정보(와이어프레임 dNode 항목). 저장값이 없으면 — 확인 불가. BODY는 조회하지 않으므로 항목을 두지 않는다. */
const fieldsOf = (node: Node, flow: DecisionFlow): [string, string][] => {
  const v = (value: string | null | undefined) => value ?? UNAVAILABLE;
  if (node.kind === "tx") {
    const t = flow.transaction!;
    return [["TRANSACTION_ID", v(t.transactionId)], ["INTERFACE_ID", v(t.interfaceId)], ["CHANNEL", v(t.channelId)], ["STATUS", v(t.status)],
      ["DURATION_TIME", t.processTimeMs === null ? UNAVAILABLE : `${t.processTimeMs} ms`]];
  }
  if (node.kind === "process") {
    const p = node.process;
    return [["PROCESS_ID", v(p.processId)], ["DEPENDS_ON", p.dependsOn ?? DASH],
      ["STATUS", p.status === null ? UNAVAILABLE : `${p.status}${p.responseCode ? ` · Code ${p.responseCode}` : ""}`],
      // PROCESS는 처리 시간 원천 속성이 없다(002 data_processes).
      ["DURATION_TIME", UNAVAILABLE], ["TOTAL_RECORD_COUNT", v(p.totalCount)]];
  }
  const m = node.message;
  return [["MESSAGE_ID", v(m.messageId)], ["PROCESS_ID", v(m.processId)], ["DIRECTION", v(m.direction)], ["STATUS", v(m.status)],
    ["DATA_SIZE", m.dataSize === null ? UNAVAILABLE : `${m.dataSize} bytes`]];
};

/** 노드 아이콘(#100 이전 ProcessFlowTree): Transaction = IconDatabase, Process = IconCodeDots(상태 F면 오류 색), Message = IconMessage2. */
const NodeIcon = ({ node, status }: { node: Node; status: string }) => {
  const Icon = node.kind === "tx" ? IconDatabase : node.kind === "process" ? IconCodeDots : IconMessage2;
  const error = node.kind === "process" && status === "F";
  return <Icon aria-hidden="true" className={cn("size-4 shrink-0", error ? "text-destructive-text" : "text-muted-foreground")} />;
};

const statusTone = (status: string) =>
  status === "F" ? "bg-destructive-soft text-destructive-text" : status === "S" ? "bg-success-soft text-success-text" : "bg-secondary text-secondary-foreground";

/** Process Flow(와이어프레임 dTree·dFlowStats·선택 노드 원본 정보). 조회 시점 저장값, 판정 PROCESS가 Focus Process. */
export const ProcessFlowCard = ({
  query,
}: {
  query: { data: DecisionFlow | undefined; isPending: boolean; isError: boolean; error: unknown; refetch: () => unknown };
}) => {
  const flow = query.data;
  const [selected, setSelected] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const nodes = flow ? flowNodes(flow) : [];
  const focusKey = flow?.processes.find((p) => p.focus)?.observationId ?? nodes[0]?.key ?? null;
  const active = nodes.find((node) => node.key === (selected ?? focusKey));
  // 건수는 서버의 실제 건수(상한으로 잘린 노드 목록과 별개). 응답에 없을 때만 받은 목록으로 센다.
  const counts = flow?.counts ?? (flow && {
    processes: flow.processes.length,
    messages: flow.messages.length,
    errors: flow.processes.filter((p) => p.status === "F").length,
  });
  const stats = counts ? `(프로세스 ${counts.processes}건 · 메시지 ${counts.messages}건 · 에러 ${counts.errors}건)` : null;
  return (
    <SectionCard title="Process Flow" icon={<IconBinaryTree />} headingLevel={2} action={stats && <span className="text-sm text-muted-foreground">{stats}</span>}>
      {query.isPending && !flow ? (
        <LoadingState />
      ) : !flow ? (
        <ErrorState title={analysisErrorTitle(query.error, "Process Flow")} onRetry={() => void query.refetch()} />
      ) : (
        <div className="grid gap-3">
          {query.isError && (
            <ErrorState title={`${analysisErrorTitle(query.error, "Process Flow")} · 이전 관측값 표시`} onRetry={() => void query.refetch()} />
          )}
          {nodes.length ? (
            <div className="grid gap-1" role="group" aria-label="Process Flow">
              {nodes.map((node) => {
                const status = statusOf(node, flow);
                return (
                  <button
                    key={node.key}
                    type="button"
                    aria-pressed={active?.key === node.key}
                    data-node={node.kind}
                    onClick={() => {
                      setSelected(node.key);
                      setOpen(true);
                    }}
                    className={cn(
                      "flex min-w-0 items-center gap-2 rounded-md px-2 py-1 text-left text-sm outline-none hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring/50",
                      node.level === 1 && "ml-5",
                      node.level === 2 && "ml-10",
                      active?.key === node.key && "bg-accent font-medium",
                    )}
                  >
                    <NodeIcon node={node} status={status} />
                    <span className={cn("inline-flex min-w-6 justify-center rounded px-1 font-mono text-xs", statusTone(status))}>{status}</span>
                    <span className="min-w-0 truncate">{labelOf(node, flow)}</span>
                  </button>
                );
              })}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">{UNAVAILABLE}</p>
          )}
          {active && (
            <details open={open} onToggle={(event) => setOpen((event.target as HTMLDetailsElement).open)}>
              <summary className="cursor-pointer text-sm">
                <IconListDetails aria-hidden="true" className="mr-1 inline-block size-4 align-text-bottom text-muted-foreground" />
                선택 노드 원본 정보
              </summary>
              <Table className="mt-2">
                <TableHeader>
                  <TableRow>
                    <TableHead>필드</TableHead>
                    <TableHead>값</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {fieldsOf(active, flow).map(([field, value]) => (
                    <TableRow key={field}>
                      <TableHead scope="row" className="h-auto font-mono text-xs font-normal">
                        {field}
                      </TableHead>
                      <TableCell>{value}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </details>
          )}
        </div>
      )}
    </SectionCard>
  );
};
