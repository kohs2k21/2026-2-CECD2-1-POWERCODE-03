import { Component, lazy, Suspense, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { IconBinaryTree, IconCodeDots, IconCrosshair, IconDatabase, IconListDetails, IconMessage2, IconRoute, IconSitemap } from "@tabler/icons-react";
import { SectionCard } from "@/components/layout/SectionCard";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState } from "@/components/ui/feedback";
import { Separator } from "@/components/ui/separator";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { getStored, setStored, storageKeys } from "@/lib/storage";
import { cn } from "@/lib/utils";
import { analysisErrorTitle, type DecisionFlow, type DecisionGraph, type DecisionItem, type FlowMessage, type FlowProcess } from "../data/analysisApi";
import { useDecisionDetail, useDecisionGraph } from "../data/useAnalysis";
import { DASH, flowCountsText, flowLegendLabels, flowViewGroupLabel, flowViewLabels, graphLabels, UNAVAILABLE } from "../labels";

export type FlowTreeNode = Node;
type Node =
  | { key: string; kind: "tx"; level: 0 }
  /**
   * ambiguous: 그래프 process 노드 verification = ambiguous(대표 시도를 고르지 않음, contracts/process-graph.md). 선택 표 STATUS = 확인 불가.
   * ambiguousRow: 트리 행(실제 관측 1건)이 그 모호 묶음의 시도. "연결 모호" 배지만 붙이고 STATUS는 행 자신의 값.
   */
  | { key: string; kind: "process"; level: 1; process: FlowProcess; ambiguous?: boolean; ambiguousRow?: boolean }
  | { key: string; kind: "message"; level: 1 | 2; message: FlowMessage };

/**
 * 트리 순서: Transaction → Process(저장 순서) → 그 Process ID의 Message. 같은 Process ID 노드가 여럿이면 첫 노드 아래에 둔다.
 * Process ID가 맞는 노드가 없는 Message는 Process와 같은 단계로 끝에 둔다(부모를 만들지 않음).
 */
export const flowNodes = (flow: DecisionFlow, ambiguousRows: ReadonlySet<string> = new Set()): Node[] => {
  const nodes: Node[] = flow.transaction ? [{ key: "tx", kind: "tx", level: 0 }] : [];
  const placed = new Set<string>();
  for (const process of flow.processes) {
    nodes.push({ key: process.observationId, kind: "process", level: 1, process, ...(ambiguousRows.has(process.observationId) && { ambiguousRow: true }) });
    if (process.processId === null || placed.has(process.processId)) continue;
    placed.add(process.processId);
    for (const message of flow.messages.filter((m) => m.processId === process.processId))
      nodes.push({ key: message.observationId, kind: "message", level: 2, message });
  }
  for (const message of flow.messages.filter((m) => m.processId === null || !placed.has(m.processId)))
    nodes.push({ key: message.observationId, kind: "message", level: 1, message });
  return nodes;
};

/** 위→아래 트리(포함 관계 T→P→M): flowNodes 순서 그대로 단계만 중첩한다. Transaction이 없으면 Process·Message가 맨 위 단계. */
export type FlowTreeItem = { node: Node; children: FlowTreeItem[] };
export const flowTree = (flow: DecisionFlow, ambiguousRows?: ReadonlySet<string>): FlowTreeItem[] => {
  const roots: FlowTreeItem[] = [];
  let tx: FlowTreeItem | null = null;
  let lastProcess: FlowTreeItem | null = null;
  for (const node of flowNodes(flow, ambiguousRows)) {
    const item: FlowTreeItem = { node, children: [] };
    if (node.level === 0) {
      tx = item;
      roots.push(item);
    } else if (node.level === 2 && lastProcess) lastProcess.children.push(item);
    else {
      (tx ? tx.children : roots).push(item);
      if (node.kind === "process") lastProcess = item;
    }
  }
  return roots;
};

/**
 * 트리 "연결 모호" 행: 그래프 응답에서 verification = ambiguous인 PROCESS 묶음의 시도 관측 ID(같은 거래·Process ID·재시도 번호가
 * 2건 이상 등, 판정 규칙은 서버 contracts/process-graph.md 하나). 그래프와 같은 조회 결과라 트리·그래프 표시가 일치한다.
 * 그래프 응답이 없거나 실패하면 빈 집합(배지 없음, 트리는 Flow 응답만으로 그린다).
 */
export const ambiguousRowsOf = (graph: DecisionGraph | undefined): Set<string> =>
  new Set((graph?.nodes ?? []).flatMap((n) =>
    n.kind === "process" && n.attrs.verification === "ambiguous" && Array.isArray(n.attrs.attempts)
      ? n.attrs.attempts.flatMap((a) => (typeof a === "object" && a !== null && typeof (a as Record<string, unknown>).observationId === "string"
        ? [(a as Record<string, unknown>).observationId as string] : []))
      : []));

/**
 * 노드 바탕 = 판정 결과(사용자 11/12): 이상 = 붉은 바탕, 이상 징후 = 노란 바탕, null(정상·판정 없음·판정 중·등급 값 없음) = 흰 바탕.
 */
export type FlowTone = "anomaly" | "anomaly_sign" | null;
/**
 * 판정 대상(Focus Process): 판정 요약 결과 배지와 같은 값(labels resultText·resultVariant). 이상 신호가 있으면 저장 등급이
 * anomaly_sign일 때 이상 징후, 그 밖(v3 등급 없음 포함)은 이상. 이상 신호가 없거나 판정 중·실패·미확인이면 흰 바탕.
 */
export const focusToneOf = (detail: Pick<DecisionItem, "signal" | "grade"> | undefined): FlowTone =>
  detail?.signal === "yes" ? (detail.grade === "anomaly_sign" ? "anomaly_sign" : "anomaly") : null;
/**
 * 다른 PROCESS 관측: /graph attempts[].grade(그 관측 최신 최종 판정의 저장된 v4 composite.grade)만 쓴다.
 * null(v03 판정·판정 없음)은 흰 바탕이다. "판정 없음"으로 그리지 않는다(contracts/process-graph.md: v03 grade = null).
 */
export const gradeTone = (grade: unknown): FlowTone => (grade === "anomaly" || grade === "anomaly_sign" ? grade : null);
/** 트리 보기용: 관측 ID → 그 시도의 등급 바탕(그래프 응답이 없거나 실패하면 빈 표 = 판정 대상 밖 노드는 흰 바탕). */
export const attemptTonesOf = (graph: DecisionGraph | undefined): Map<string, FlowTone> =>
  new Map((graph?.nodes ?? []).flatMap((n) =>
    n.kind === "process" && Array.isArray(n.attrs.attempts)
      ? n.attrs.attempts.flatMap((a): [string, FlowTone][] => {
        const attempt = typeof a === "object" && a !== null ? (a as Record<string, unknown>) : null;
        return attempt && typeof attempt.observationId === "string" ? [[attempt.observationId, gradeTone(attempt.grade)]] : [];
      })
      : []));

/** 그래프 보기: 판정 대상이 아닌 PROCESS 노드의 바탕: 대표 시도(배열 끝 = 가장 큰 재시도 번호)의 등급. 모호면 대표 시도가 없어 흰 바탕. */
export const processToneOf = (attrs: Record<string, unknown>): FlowTone => {
  if (attrs.verification === "ambiguous" || !Array.isArray(attrs.attempts)) return null;
  const last = attrs.attempts[attrs.attempts.length - 1];
  return typeof last === "object" && last !== null ? gradeTone((last as Record<string, unknown>).grade) : null;
};

export const statusOf = (node: Node, flow: DecisionFlow) =>
  (node.kind === "tx" ? flow.transaction?.status : node.kind === "process" ? node.process.status : node.message.status) ?? DASH;
/** 노드에 보이는 이름: 머리말 없이 ID만(Transaction ID·Process ID·Message ID). Message 방향은 따로 작은 배지로 보인다. */
export const idOf = (node: Node, flow: DecisionFlow) =>
  (node.kind === "tx" ? flow.transaction?.transactionId : node.kind === "process" ? node.process.processId : node.message.messageId) ?? DASH;
/** 접근성 이름·툴팁: 종류 + ID(화면에는 머리말을 보이지 않으므로 화면 낭독기에는 종류를 알린다). */
export const nameOf = (node: Node, flow: DecisionFlow) =>
  node.kind === "tx"
    ? `Transaction ${idOf(node, flow)}`
    : node.kind === "process"
      ? `${node.process.focus ? "Focus Process" : "Process"} ${idOf(node, flow)}`
      : `Message ${node.message.direction ?? DASH} ${idOf(node, flow)}`;
/** 선택 노드 원본 정보(와이어프레임 dNode 항목). 저장값이 없으면 — 확인 불가. BODY는 조회하지 않으므로 항목을 두지 않는다. */
export const fieldsOf = (node: Node, flow: DecisionFlow): [string, string][] => {
  const v = (value: string | null | undefined) => value ?? UNAVAILABLE;
  if (node.kind === "tx") {
    const t = flow.transaction!;
    return [["TRANSACTION_ID", v(t.transactionId)], ["INTERFACE_ID", v(t.interfaceId)], ["CHANNEL", v(t.channelId)], ["STATUS", v(t.status)],
      ["DURATION_TIME", t.processTimeMs === null ? UNAVAILABLE : `${t.processTimeMs} ms`]];
  }
  if (node.kind === "process") {
    const p = node.process;
    // 모호 PROCESS는 시도 중 하나의 상태를 고르지 않는다(그래프 attrs.status = null과 같은 뜻).
    return [["PROCESS_ID", v(p.processId)], ["DEPENDS_ON", p.dependsOn ?? DASH],
      ["STATUS", node.ambiguous || p.status === null ? UNAVAILABLE : `${p.status}${p.responseCode ? ` · Code ${p.responseCode}` : ""}`],
      // PROCESS는 처리 시간 원천 속성이 없다(002 data_processes).
      ["DURATION_TIME", UNAVAILABLE], ["TOTAL_RECORD_COUNT", v(p.totalCount)]];
  }
  const m = node.message;
  return [["MESSAGE_ID", v(m.messageId)], ["PROCESS_ID", v(m.processId)], ["DIRECTION", v(m.direction)], ["STATUS", v(m.status)],
    ["DATA_SIZE", m.dataSize === null ? UNAVAILABLE : `${m.dataSize} bytes`]];
};

/** 노드 아이콘(#100 이전 ProcessFlowTree): Transaction = IconDatabase, Process = IconCodeDots(상태 F면 오류 색), Message = IconMessage2. 그래프 보기도 같은 아이콘. */
export const NodeIcon = ({ kind, status }: { kind: Node["kind"]; status: string }) => {
  const Icon = kind === "tx" ? IconDatabase : kind === "process" ? IconCodeDots : IconMessage2;
  const error = kind === "process" && status === "F";
  return <Icon aria-hidden="true" className={cn("size-4 shrink-0", error ? "text-destructive-text" : "text-muted-foreground")} />;
};

/**
 * 트리·그래프 공용 노드 모양: 같은 폭(Transaction·Process w-50 = 그래프 FLOW_NODE_W), 같은 글자 크기·여백.
 * 1440 화면 트리 상자(안쪽 629px)에 Process 3개가 가로 스크롤 없이 들어가는 폭.
 * 바탕 = 판정 결과(FlowTone, 사용자 11/12): 이상 = destructive-soft, 이상 징후 = warning-soft, 그 밖 = 흰 바탕(card). 노드 종류는 아이콘으로 구분.
 * 테두리 없음(사용자 11/12 "테두리 있다. 삭제해"): 상자 바탕(muted)과 옅은 그림자(shadow-xs)로 흰 노드를 구분한다.
 * 선택 노드도 테두리·링 없이 더 진한 그림자(shadow-md)로 보인다. 키보드 초점만 focus-visible 링(마우스 누름·기본 선택에는 보이지 않음).
 * 판정 대상은 바탕이 아니라 표적 아이콘(FocusMarker)으로 보인다. 뜻은 상자 오른쪽 위 범례(FlowViewOverlay).
 */
export const FLOW_NODE_W = 200;
export const flowNodeClass = ({ tone, focus, selected }: { tone: FlowTone; focus: boolean; selected: boolean }) =>
  cn(
    "rounded-md bg-card text-left text-sm shadow-xs outline-none transition-shadow hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring/50",
    tone === "anomaly" && "bg-destructive-soft hover:bg-destructive-soft",
    tone === "anomaly_sign" && "bg-warning-soft hover:bg-warning-soft",
    focus && "font-medium",
    selected && "shadow-md",
  );
/** 판정 대상 표시(범례 "판정 대상"과 같은 아이콘). 접근성 이름은 "Focus Process"로 따로 알린다. */
export const FocusMarker = ({ className }: { className?: string }) => (
  <IconCrosshair aria-hidden="true" data-focus-marker="" className={cn("size-4 shrink-0 text-foreground", className)} />
);
/**
 * 상태 배지(S·F 등 원천 값). 색 바탕 노드(이상·이상 징후) 위에서는 배지 바탕이 노드 바탕과 같아져 모양이 사라지므로
 * 흰 바탕(card) + 상태 글자색으로 그린다(검토 11/12).
 */
export const StatusBadge = ({ status, onTint = false }: { status: string; onTint?: boolean }) => (
  <span
    data-status-badge=""
    className={cn("inline-flex min-w-6 shrink-0 justify-center rounded px-1 font-mono text-xs", onTint ? cn("bg-card", statusText(status)) : statusTone(status))}
  >
    {status}
  </span>
);

export const statusTone = (status: string) =>
  status === "F" ? "bg-destructive-soft text-destructive-text" : status === "S" ? "bg-success-soft text-success-text" : "bg-secondary text-secondary-foreground";
const statusText = (status: string) => (status === "F" ? "text-destructive-text" : status === "S" ? "text-success-text" : "text-secondary-foreground");

/** 위→아래 트리 한 단계(부모-자식 꺾은 연결선은 styles.css .flow-tree). 노드 버튼은 아이콘·상태·이름(Focus Process는 붉은 바탕). */
const FlowTreeLevel = ({ items, flow, tones, activeKey, onSelect, root = true, leaves = false }: {
  items: FlowTreeItem[];
  flow: DecisionFlow;
  /** 판정 결과 바탕: focus = 판정 요약 결과, attempts = 다른 PROCESS 관측의 /graph 시도 등급. */
  tones: { focus: FlowTone; attempts: ReadonlyMap<string, FlowTone> };
  activeKey: string | null;
  onSelect: (key: string) => void;
  root?: boolean;
  /** Process 아래 Message: 가로로 늘어놓지 않고 세로 목록 + 꺾은 연결선(폭 절약). */
  leaves?: boolean;
}) => (
  <ul className={cn(leaves ? "flow-tree-leaves" : "flow-tree", root && "flow-tree-root mx-auto w-max min-w-full")}>
    {items.map(({ node, children }) => {
      const status = statusOf(node, flow);
      const focus = node.kind === "process" && node.process.focus;
      const ambiguousRow = node.kind === "process" && node.ambiguousRow === true;
      const tone = node.kind !== "process" ? null : focus ? tones.focus : (tones.attempts.get(node.process.observationId) ?? null);
      return (
        <li key={node.key}>
          <button
            type="button"
            aria-pressed={activeKey === node.key}
            // 화면에는 ID만 보이므로 접근성 이름에 종류·상태를 넣는다.
            aria-label={`${nameOf(node, flow)} · ${status}${ambiguousRow ? ` · ${graphLabels.ambiguous}` : ""}`}
            data-node={node.kind}
            data-focus={focus ? "true" : undefined}
            data-tone={tone ?? undefined}
            title={nameOf(node, flow)}
            onClick={() => onSelect(node.key)}
            className={cn(
              flowNodeClass({ tone, focus, selected: activeKey === node.key }),
              "flex items-center gap-1.5",
              // 고정 폭(그래프 노드와 같은 폭). 긴 ID는 줄바꿈(말줄임 없음). Message는 Process 아래 들여쓴 만큼 좁고 글자가 작다.
              node.kind === "message" ? "w-46 px-1.5 py-0.5 text-xs" : "w-50 px-2 py-1",
            )}
          >
            <NodeIcon kind={node.kind} status={status} />
            <StatusBadge status={status} onTint={tone !== null} />
            {node.kind === "message" && node.message.direction && (
              <span className="shrink-0 rounded border px-1 font-mono text-[10px] leading-4 text-muted-foreground">{node.message.direction}</span>
            )}
            <span className="min-w-0 wrap-anywhere">{idOf(node, flow)}</span>
            {(ambiguousRow || focus) && (
              <span className="ml-auto flex shrink-0 items-center gap-1">
                {ambiguousRow && (
                  // 색 바탕 노드 위에서도 보이게 흰 바탕 배지(상태 배지와 같은 규칙).
                  <span className={cn("rounded px-1 text-xs whitespace-nowrap text-warning-text", tone === null ? "bg-warning-soft" : "bg-card")}>
                    {graphLabels.ambiguous}
                  </span>
                )}
                {focus && <FocusMarker />}
              </span>
            )}
          </button>
          {children.length > 0 && (
            <FlowTreeLevel items={children} flow={flow} tones={tones} activeKey={activeKey} onSelect={onSelect} root={false} leaves={node.kind === "process"} />
          )}
        </li>
      );
    })}
  </ul>
);

/** 선택 노드 원본 정보 표(트리·그래프 보기 공용). 접지 않고 늘 보인다. 위에 구분선(사용자 11/12). */
export const SelectedNodeFields = ({ fields }: { fields: [string, string][] }) => (
  <section aria-labelledby="flow-node-fields" className="grid gap-2">
    <Separator className="mb-1" />
    <h3 id="flow-node-fields" className="text-sm font-medium">
      <IconListDetails aria-hidden="true" className="mr-1 inline-block size-4 align-text-bottom text-muted-foreground" />
      선택 노드 원본 정보
    </h3>
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>필드</TableHead>
          <TableHead>값</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {fields.map(([field, value]) => (
          <TableRow key={field}>
            <TableHead scope="row" className="h-auto font-mono text-xs font-normal">
              {field}
            </TableHead>
            <TableCell>{value}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  </section>
);

type FlowView = keyof typeof flowViewLabels;
/** 보기 전환 버튼 아이콘(사용자 11/12): 로그 구성 = 계층(IconSitemap), 실행 순서 = 경로(IconRoute). */
const ViewIcon = ({ mode }: { mode: FlowView }) => {
  const Icon = mode === "tree" ? IconSitemap : IconRoute;
  return <Icon aria-hidden="true" />;
};
const flowViews = Object.entries(flowViewLabels) as [FlowView, string][];
const storedView = (): FlowView => (getStored<unknown>(storageKeys.processFlowView, "tree") === "graph" ? "graph" : "tree");
/** 그래프 보기(비교 시제품): React Flow·dagre는 그래프 보기를 열 때만 불러온다. 다시 시도하면 새로 불러온다. */
const loadGraphView = () => lazy(() => import("./ProcessGraphView"));

/** 그래프 보기 조각 불러오기·그리기 실패를 카드 안 ErrorState로 가둔다(페이지 전체가 깨지지 않게). */
export class GraphViewBoundary extends Component<{ children: ReactNode; onRetry: () => void }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <ErrorState
        title={graphLabels.loadFailed}
        onRetry={() => {
          this.props.onRetry();
          this.setState({ failed: false });
        }}
      />
    );
  }
}

/** 트리·그래프 보기 공용 상자: 같은 높이·폭이라 보기를 바꿔도 카드 크기가 변하지 않는다. relative: 범례·건수(FlowViewOverlay)를 상자 모서리에 둔다. */
/**
 * 상자 바탕은 muted: 테두리 없는 흰 노드가 바탕과 구분되게 한다(사용자 11/12).
 * min-w-0: 격자 항목 최소 폭이 트리 폭(w-max)을 따라 커지지 않게 한다(넓은 트리는 카드를 넓히지 않고 상자 안에서 스크롤).
 */
export const flowViewBoxClass = "relative h-80 w-full min-w-0 rounded-md border bg-muted";
export const FlowViewBox = ({ children, label, overlay }: { children: ReactNode; label?: string; overlay?: ReactNode }) => (
  <div className={flowViewBoxClass} role={label ? "group" : undefined} aria-label={label}>
    {children}
    {overlay}
  </div>
);

const LegendChip = ({ className, label }: { className: string; label: string }) => (
  <li className="flex items-center gap-1">
    <span aria-hidden="true" className={cn("size-3 rounded-sm", className)} />
    {label}
  </li>
);

/**
 * 상자 오른쪽 위 범례·오른쪽 아래 건수(사용자 11/12 요청, 로그 구성·실행 순서 공용). 왼쪽 아래 확대·축소 버튼과 겹치지 않는다.
 * 범례: 이상(붉은 칸)·이상 징후(노란 칸)·판정 대상(표적 아이콘), 로그 없음(점선 칸)은 그 노드가 있을 때만. 흰 바탕(정상 등)은 항목을 두지 않는다.
 * 노드와 겹치지 않게 트리는 위·아래 안쪽 여백(pt-9·pb-8), 그래프는 맞춤 여백(fitOptions y)으로 띠를 비워 둔다.
 * 트리가 넘쳐 스크롤 막대가 생기면 그 폭만큼 안쪽으로 옮긴다(--flow-sb-x·--flow-sb-y, ProcessFlowCard가 잰다. 그래프는 0).
 * pointer-events-none: 넘치는 트리를 스크롤할 때 그 아래 노드도 누를 수 있게 한다.
 * 연결 모호는 노드 안 배지 글자로 뜻이 보여 범례에 넣지 않는다.
 */
export const FlowViewOverlay = ({ stats, notLogged = false }: { stats: string | null; notLogged?: boolean }) => (
  <>
    <ul
      aria-label={flowLegendLabels.name}
      data-flow-legend=""
      className="pointer-events-none absolute top-2 right-[calc(0.5rem+var(--flow-sb-x,0px))] z-10 flex items-center gap-2.5 rounded bg-muted/90 px-1 text-xs whitespace-nowrap text-muted-foreground"
    >
      {/* 칸 바탕은 노드와 같은 색, 테두리는 짙은 글자색 — 연한 바탕이 muted 상자 바탕과 구분되게(이상 징후 바탕 대비 ≈1.1). */}
      <LegendChip className="border border-destructive-text bg-destructive-soft" label={flowLegendLabels.anomaly} />
      <LegendChip className="border border-warning-text bg-warning-soft" label={flowLegendLabels.anomalySign} />
      <li className="flex items-center gap-1">
        <FocusMarker className="size-3.5" />
        {flowLegendLabels.focus}
      </li>
      {notLogged && <LegendChip className="border border-dashed border-subtle bg-card" label={flowLegendLabels.notLogged} />}
    </ul>
    {stats && (
      <p
        data-flow-counts=""
        className="pointer-events-none absolute right-[calc(0.5rem+var(--flow-sb-x,0px))] bottom-[calc(0.5rem+var(--flow-sb-y,0px))] z-10 rounded bg-muted/90 px-1 text-xs whitespace-nowrap text-muted-foreground"
      >
        {stats}
      </p>
    )}
  </>
);

/** 트리 상자 스크롤 막대 폭·높이를 바깥 상자 CSS 변수로 둔다(범례·건수가 막대 위에 겹치지 않게, 검토 11/12). */
const syncScrollbarInset = (box: HTMLElement | null) => {
  const outer = box?.parentElement;
  if (!box || !outer || typeof box.offsetWidth !== "number") return;
  outer.style.setProperty("--flow-sb-x", `${Math.max(0, box.offsetWidth - box.clientWidth)}px`);
  outer.style.setProperty("--flow-sb-y", `${Math.max(0, box.offsetHeight - box.clientHeight)}px`);
};

/** 트리가 상자보다 클 때만: 상자 안에서 Focus Process를 보이게 스크롤한다(가로 가운데, 세로는 필요할 때만). 작으면 스크롤 0(가운데 정렬). 페이지는 스크롤하지 않는다. */
const scrollFocusIntoBox = (box: HTMLElement | null) => {
  const focus = box?.querySelector<HTMLElement>('[data-focus="true"]');
  if (!box || !focus || typeof box.getBoundingClientRect !== "function") return;
  const b = box.getBoundingClientRect();
  const f = focus.getBoundingClientRect();
  box.scrollLeft += f.left + f.width / 2 - (b.left + b.width / 2);
  if (f.top < b.top) box.scrollTop -= b.top - f.top;
  else if (f.bottom > b.bottom) box.scrollTop += f.bottom - b.bottom;
};

/** Process Flow(와이어프레임 dTree·dFlowStats·선택 노드 원본 정보). 조회 시점 저장값, 판정 PROCESS가 Focus Process. */
export const ProcessFlowCard = ({
  query,
  decisionId,
}: {
  query: { data: DecisionFlow | undefined; isPending: boolean; isError: boolean; error: unknown; refetch: () => unknown };
  /** 그래프 보기 조회 대상. 없으면 보기 전환을 두지 않는다(트리만). */
  decisionId?: string;
}) => {
  const flow = query.data;
  const [selected, setSelected] = useState<string | null>(null);
  const [view, setView] = useState<FlowView>(() => (decisionId ? storedView() : "tree"));
  const [GraphView, setGraphView] = useState(loadGraphView);
  const treeBox = useRef<HTMLDivElement>(null);
  // 트리 "연결 모호" 배지용(그래프 보기와 같은 조회·캐시). 실패해도 트리는 그대로 그린다.
  const graph = useDecisionGraph(decisionId ?? "", Boolean(decisionId)).data;
  const ambiguousRows = useMemo(() => ambiguousRowsOf(graph), [graph]);
  // 노드 바탕: 판정 대상 = 판정 요약과 같은 상세 조회(같은 캐시), 다른 PROCESS 관측 = 그래프 시도 등급.
  const focusTone = focusToneOf(useDecisionDetail(decisionId ?? "").data);
  const attemptTones = useMemo(() => attemptTonesOf(graph), [graph]);
  const nodes = flow ? flowNodes(flow, ambiguousRows) : [];
  const focusKey = flow?.processes.find((p) => p.focus)?.observationId ?? nodes[0]?.key ?? null;
  const active = nodes.find((node) => node.key === (selected ?? focusKey));
  // 건수는 서버의 실제 건수(상한으로 잘린 노드 목록과 별개). 응답에 없을 때만 받은 목록으로 센다.
  const counts = flow?.counts ?? (flow && {
    processes: flow.processes.length,
    messages: flow.messages.length,
    errors: flow.processes.filter((p) => p.status === "F").length,
  });
  const hasNodes = nodes.length > 0;
  // 처음 그릴 때(그리고 트리 보기로 돌아올 때) Focus Process를 트리 상자 안에 보인다. 노드 선택으로는 움직이지 않는다.
  useLayoutEffect(() => {
    if (view === "tree" && hasNodes) scrollFocusIntoBox(treeBox.current);
  }, [view, hasNodes, focusKey]);
  // 트리 상자·트리 크기가 바뀔 때마다 스크롤 막대 폭을 다시 잰다(넓은 화면 전환·노드 수 변화).
  useLayoutEffect(() => {
    const box = treeBox.current;
    if (view !== "tree" || !hasNodes || !box || typeof ResizeObserver === "undefined") return;
    syncScrollbarInset(box);
    const observer = new ResizeObserver(() => syncScrollbarInset(box));
    observer.observe(box);
    if (box.firstElementChild) observer.observe(box.firstElementChild);
    return () => observer.disconnect();
  }, [view, hasNodes]);
  const stats = counts ? flowCountsText(counts) : null;
  const viewSwitch = decisionId && (
    <div role="group" aria-label={flowViewGroupLabel} className="inline-flex gap-1">
      {flowViews.map(([mode, label]) => (
        <Button
          key={mode}
          size="sm"
          variant={view === mode ? "default" : "outline"}
          aria-pressed={view === mode}
          onClick={() => {
            setView(mode);
            setStored(storageKeys.processFlowView, mode);
          }}
        >
          <ViewIcon mode={mode} />
          {label}
        </Button>
      ))}
    </div>
  );
  return (
    <SectionCard title="Process Flow" icon={<IconBinaryTree />} headingLevel={2} action={viewSwitch || undefined}>
      {query.isPending && !flow ? (
        <LoadingState />
      ) : !flow ? (
        <ErrorState title={analysisErrorTitle(query.error, "Process Flow")} onRetry={() => void query.refetch()} />
      ) : (
        <div className="grid gap-3">
          {query.isError && (
            <ErrorState title={`${analysisErrorTitle(query.error, "Process Flow")} · 이전 관측값 표시`} onRetry={() => void query.refetch()} />
          )}
          {view === "graph" && decisionId ? (
            <GraphViewBoundary onRetry={() => setGraphView(loadGraphView)}>
              <Suspense fallback={<FlowViewBox><LoadingState /></FlowViewBox>}>
                <GraphView decisionId={decisionId} flow={flow} stats={stats} focusTone={focusTone} />
              </Suspense>
            </GraphViewBoundary>
          ) : nodes.length ? (
            <div className={flowViewBoxClass} role="group" aria-label="Process Flow">
              <div ref={treeBox} className="h-full overflow-auto px-3 pt-9 pb-8">
                {/*
                  상자보다 작으면 가로·세로 가운데(my-auto·mx-auto: 넘칠 때는 위·왼쪽이 잘리지 않고 스크롤된다).
                  w-max min-w-full: 스크롤 영역의 바로 아래 자식이 트리 폭만큼 넓어져야 가로로 넘칠 때 오른쪽 끝에도 안쪽 여백이 남는다.
                  위·아래 여백(pt-9·pb-8)은 범례·건수 띠(FlowViewOverlay)를 비워 노드와 겹치지 않게 한다.
                */}
                <div className="flex min-h-full w-max min-w-full flex-col">
                  <div className="my-auto">
                    <FlowTreeLevel
                      items={flowTree(flow, ambiguousRows)}
                      flow={flow}
                      tones={{ focus: focusTone, attempts: attemptTones }}
                      activeKey={active?.key ?? null}
                      onSelect={setSelected}
                    />
                  </div>
                </div>
              </div>
              <FlowViewOverlay stats={stats} />
            </div>
          ) : (
            <FlowViewBox overlay={<FlowViewOverlay stats={stats} />}>
              <p className="p-3 text-sm text-muted-foreground">{UNAVAILABLE}</p>
            </FlowViewBox>
          )}
          {view === "tree" && active && <SelectedNodeFields fields={fieldsOf(active, flow)} />}
        </div>
      )}
    </SectionCard>
  );
};
