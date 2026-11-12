import "@xyflow/react/dist/style.css";
import { useEffect, useMemo, useRef, useState } from "react";
import dagre from "@dagrejs/dagre";
import {
  BaseEdge, Controls, EdgeLabelRenderer, getSmoothStepPath, Handle, MarkerType, Position, ReactFlow, useReactFlow, useStore,
  type Edge, type FitViewOptions, type EdgeProps, type Node, type NodeProps,
} from "@xyflow/react";
import { ErrorState, LoadingState } from "@/components/ui/feedback";
import { cn } from "@/lib/utils";
import { analysisErrorTitle, type DecisionFlow, type DecisionGraph, type FlowProcess, type GraphNode, type HandoffResult } from "../data/analysisApi";
import { useDecisionGraph } from "../data/useAnalysis";
import { DASH, graphLabels, handoffLabels, UNAVAILABLE } from "../labels";
import {
  fieldsOf, FLOW_NODE_W, flowNodeClass, FlowViewBox, FlowViewOverlay, FocusMarker, idOf, nameOf, NodeIcon, processToneOf, SelectedNodeFields, StatusBadge,
  type FlowTone, type FlowTreeNode,
} from "./ProcessFlowCard";

/**
 * Process Flow 그래프 보기(비교 시제품, contracts/process-graph.md 층 A).
 * - 노드: transaction·process·missing. message는 노드로 그리지 않고 PROCESS 노드 안에 방향별 건수로 요약한다.
 * - 간선: precedes(선행 → 후행, verified 외 점선), 인계 비교(hands_off)는 선 대신 precedes 간선 이름으로 보인다.
 *   contains는 거래 → 시작 단계(들어오는 precedes가 없는 PROCESS)만 옅은 점선.
 * - 배치: PROCESS·missing만 dagre 왼쪽 → 오른쪽, 거래는 첫 단계 위 머리 줄(가로 단계를 하나 줄인다).
 * - 처음 보기: 늘 그래프 전체를 상자에 맞춰 가운데(가로·세로)에 둔다. 작은 그래프는 100%보다 키우지 않는다.
 * - 노드 바탕 = 판정 결과(사용자 11/12): 판정 대상 = 판정 요약 결과(focusTone), 다른 PROCESS = 대표 시도(가장 큰 재시도 번호,
 *   attrs.status와 같은 시도)의 attempts[].grade. 모호 PROCESS는 대표 시도를 고르지 않으므로 흰 바탕. 거래·missing은 흰 바탕.
 *   id는 쪼개 읽지 않고 from·to로만 잇는다.
 */

/** 트리 보기 노드와 같은 폭. 높이는 배치 간격용(Transaction 한 줄, Process는 메시지 요약 줄 포함). */
const NODE_W = FLOW_NODE_W;
const TX_H = 48;
const PROCESS_H = 64;
/** 거래 머리 줄과 첫 단계 사이. */
const TX_GAP = 36;

type GraphCardData = {
  kind: "transaction" | "process" | "missing";
  /** 보이는 이름 = ID만. name = 종류 + ID(접근성 이름·툴팁). */
  label: string;
  name: string;
  status: string | null;
  focus: boolean;
  tone: FlowTone;
  ambiguous: boolean;
  missingText: string | null;
  messages: string | null;
  selected: boolean;
  onSelect: () => void;
};
type GraphCardNode = Node<GraphCardData, "card">;

const str = (value: unknown) => (typeof value === "string" ? value : null);

const GraphCard = ({ data }: NodeProps<GraphCardNode>) => {
  const status = data.status ?? DASH;
  return (
    <>
      <Handle type="target" position={Position.Left} isConnectable={false} className="!size-1.5 !border-0 !bg-transparent" />
      <button
        type="button"
        aria-pressed={data.selected}
        // 접근성 이름: 종류 + ID와 카드에 보이는 상태·요약(aria-label이 내용을 대신하므로 함께 넣는다).
        aria-label={[data.name, data.kind === "missing" ? null : status, data.missingText, data.messages, data.ambiguous ? graphLabels.ambiguous : null]
          .filter(Boolean)
          .join(" · ")}
        title={data.name}
        data-node={data.kind}
        data-focus={data.focus ? "true" : undefined}
        data-tone={data.tone ?? undefined}
        onClick={data.onSelect}
        style={{ width: NODE_W }}
        className={cn(
          flowNodeClass({ tone: data.tone, focus: data.focus, selected: data.selected }),
          "grid gap-1 px-2 py-1 text-card-foreground",
          // 로그 없음 점선은 뜻이 있는 표시라 남긴다(일반 노드 테두리는 없음, 사용자 11/12).
          data.kind === "missing" && "border border-dashed border-subtle text-muted-foreground",
        )}
      >
        <span className="flex min-w-0 items-center gap-1.5">
          <NodeIcon kind={data.kind === "transaction" ? "tx" : "process"} status={status} />
          {data.kind !== "missing" && <StatusBadge status={status} onTint={data.tone !== null} />}
          <span className="line-clamp-2 min-w-0 wrap-anywhere">{data.label}</span>
          {data.focus && <FocusMarker className="ml-auto" />}
        </span>
        {(data.missingText || data.messages || data.ambiguous) && (
          <span className="flex min-w-0 flex-wrap items-center gap-1 text-xs text-muted-foreground">
            {data.missingText && <span>{data.missingText}</span>}
            {data.messages && <span className="font-mono">{data.messages}</span>}
            {data.ambiguous && (
              <span className={cn("rounded px-1 text-warning-text", data.tone === null ? "bg-warning-soft" : "bg-card")}>{graphLabels.ambiguous}</span>
            )}
          </span>
        )}
      </button>
      <Handle type="source" position={Position.Right} isConnectable={false} className="!size-1.5 !border-0 !bg-transparent" />
      {/* 거래 → 시작 단계(contains): 거래 아래 → 시작 PROCESS 위. 선행 간선은 위의 왼쪽·오른쪽 연결점(먼저 선언)을 쓴다. */}
      {data.kind === "transaction" ? (
        <Handle id="down" type="source" position={Position.Bottom} isConnectable={false} className="!size-1.5 !border-0 !bg-transparent" />
      ) : (
        <Handle id="top" type="target" position={Position.Top} isConnectable={false} className="!size-1.5 !border-0 !bg-transparent" />
      )}
    </>
  );
};
const nodeTypes = { card: GraphCard };

/**
 * 선행 간선: smoothstep 선 + 인계 비교 이름. 이름은 SVG 선 위 HTML 층(EdgeLabelRenderer)에 바탕색과 함께 그려
 * 갈라지는 다른 간선의 선이 글자 위로 지나가지 않게 한다.
 */
type FlowEdgeData = { label?: string; tone?: "error" };
type FlowEdge = Edge<FlowEdgeData, "flow">;
const FlowStepEdge = ({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, markerEnd, style, data }: EdgeProps<FlowEdge>) => {
  const [path, labelX, labelY] = getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  return (
    <>
      <BaseEdge path={path} markerEnd={markerEnd} style={style} />
      {data?.label && (
        <EdgeLabelRenderer>
          <span
            data-edge-label=""
            className={cn(
              "nodrag nopan pointer-events-none absolute rounded border bg-card px-1 text-[11px] leading-4 whitespace-nowrap",
              data.tone === "error" ? "text-destructive-text" : "text-foreground",
            )}
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          >
            {data.label}
          </span>
        </EdgeLabelRenderer>
      )}
    </>
  );
};
const edgeTypes = { flow: FlowStepEdge };

/** 방향별 메시지 건수: OUT·IN 먼저, 그 밖의 저장값은 이름순, 방향 없음은 —. */
const messageSummary = (directions: (string | null)[]) => {
  if (!directions.length) return graphLabels.noMessages;
  const tally = new Map<string, number>();
  for (const d of directions) tally.set(d ?? DASH, (tally.get(d ?? DASH) ?? 0) + 1);
  const rank = (d: string) => (d === "OUT" ? 0 : d === "IN" ? 1 : d === DASH ? 3 : 2);
  return [...tally.entries()]
    .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
    .map(([d, n]) => `${d} ${n}`)
    .join(" · ");
};

/** 인계 요약(contracts 선행 간선 evidence 요약 규칙): match 하나라도 → match, 아니면 mismatch → mismatch, 아니면 undetermined. */
const summarizeHandoff = (results: unknown[]): HandoffResult | null => {
  if (!results.length) return null;
  if (results.includes("match")) return "match";
  if (results.includes("mismatch")) return "mismatch";
  return "undetermined";
};

/** 그래프 노드 → 트리 보기와 같은 원본 정보 행. PROCESS는 Flow 응답의 같은 관측(판정 관측 우선, 없으면 마지막 시도)을 쓴다. */
const treeNodeOf = (node: GraphNode, flow: DecisionFlow): { node: FlowTreeNode; flow: DecisionFlow } | null => {
  const a = node.attrs;
  if (node.kind === "transaction") {
    // 상태·응답코드는 그래프 거래 노드(대표 행, contracts/process-graph.md "거래 노드 대표 행", 모호 = null)를 쓴다. 나머지는 Flow 행.
    const transaction = {
      ...(flow.transaction ?? {
        transactionId: str(a.transactionId), interfaceId: str(a.interfaceId), channelId: str(a.channelId),
        processTimeMs: a.processTimeMs == null ? null : String(a.processTimeMs), processCount: null,
      }),
      status: str(a.status),
      responseCode: str(a.responseCode),
    };
    return { node: { key: node.id, kind: "tx", level: 0 }, flow: { ...flow, transaction } };
  }
  if (node.kind === "process") {
    const attempts = Array.isArray(a.attempts) ? a.attempts.filter((x): x is Record<string, unknown> => typeof x === "object" && x !== null) : [];
    const pick = attempts.find((x) => x.focus === true) ?? attempts[attempts.length - 1];
    const matched = flow.processes.find((p) => p.observationId === str(pick?.observationId));
    const process: FlowProcess = matched ?? {
      observationId: node.id, processId: str(a.processId), dependsOn: str(a.dependsOn), status: str(a.status), responseCode: str(a.responseCode),
      totalCount: str(pick?.totalCount), focus: a.focus === true,
    };
    // 모호 PROCESS: 시도 하나의 상태를 대표로 보이지 않는다(STATUS — 확인 불가).
    return { node: { key: node.id, kind: "process", level: 1, process, ambiguous: a.verification === "ambiguous" }, flow };
  }
  if (node.kind === "missing") {
    const process: FlowProcess = { observationId: node.id, processId: str(a.processId), dependsOn: null, status: null, responseCode: null, totalCount: null, focus: false };
    return { node: { key: node.id, kind: "process", level: 1, process }, flow };
  }
  return null;
};

const buildGraph = (graph: DecisionGraph, flow: DecisionFlow, selected: string | null, onSelect: (id: string) => void, focusTone: FlowTone) => {
  const drawn = graph.nodes.filter((n) => n.kind !== "message");
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const drawnIds = new Set(drawn.map((n) => n.id));
  const precedes = graph.edges.filter((e) => e.kind === "precedes" && drawnIds.has(e.from) && drawnIds.has(e.to));
  const hasIncoming = new Set(precedes.map((e) => e.to));
  const starts = graph.edges.filter(
    (e) => e.kind === "contains" && byId.get(e.from)?.kind === "transaction" && byId.get(e.to)?.kind === "process" && !hasIncoming.has(e.to),
  );
  const directions = new Map<string, (string | null)[]>();
  for (const e of graph.edges) {
    const target = byId.get(e.to);
    if (e.kind !== "contains" || target?.kind !== "message") continue;
    directions.set(e.from, [...(directions.get(e.from) ?? []), str(target.attrs.direction)]);
  }
  const handoffs = new Map<string, unknown[]>();
  for (const e of graph.edges) {
    if (e.kind !== "hands_off") continue;
    const key = `${e.from}\u0000${e.to}`;
    handoffs.set(key, [...(handoffs.get(key) ?? []), e.evidence?.result]);
  }

  const layout = new dagre.graphlib.Graph();
  // 단계 사이를 넓혀 간선 이름이 놓일 자리를 둔다.
  layout.setGraph({ rankdir: "LR", nodesep: 28, ranksep: 90, marginx: 0, marginy: 0 });
  layout.setDefaultEdgeLabel(() => ({}));
  const steps = drawn.filter((n) => n.kind !== "transaction");
  for (const n of steps) layout.setNode(n.id, { width: NODE_W, height: PROCESS_H });
  for (const e of precedes) layout.setEdge(e.from, e.to);
  if (steps.length) dagre.layout(layout);
  const topLeft = new Map<string, { x: number; y: number }>();
  for (const n of steps) {
    const point = layout.node(n.id);
    topLeft.set(n.id, { x: point.x - NODE_W / 2, y: point.y - PROCESS_H / 2 });
  }
  const left = Math.min(0, ...[...topLeft.values()].map((p) => p.x));
  const top = Math.min(0, ...[...topLeft.values()].map((p) => p.y));
  for (const n of drawn) if (n.kind === "transaction") topLeft.set(n.id, { x: left, y: top - TX_H - TX_GAP });

  const nodes: GraphCardNode[] = drawn.map((n) => {
    const a = n.attrs;
    const tree = treeNodeOf(n, flow);
    const label = tree ? idOf(tree.node, tree.flow) : n.id;
    const name = tree ? nameOf(tree.node, tree.flow) : n.id;
    return {
      id: n.id,
      type: "card",
      position: topLeft.get(n.id)!,
      draggable: false,
      selectable: false,
      data: {
        kind: n.kind as GraphCardData["kind"],
        label,
        name,
        status: n.kind === "missing" ? null : str(a.status),
        focus: n.kind === "process" && a.focus === true,
        tone: n.kind !== "process" ? null : a.focus === true ? focusTone : processToneOf(a),
        ambiguous: (n.kind === "process" || n.kind === "transaction") && a.verification === "ambiguous",
        missingText: n.kind === "missing" ? (a.reason === "not_logged" ? graphLabels.notLogged : UNAVAILABLE) : null,
        // missing은 연결된 MESSAGE가 있을 때만(P→M unmatched) 건수를 보인다.
        messages: n.kind === "transaction" || (n.kind === "missing" && !directions.has(n.id)) ? null : messageSummary(directions.get(n.id) ?? []),
        selected: selected === n.id,
        onSelect: () => onSelect(n.id),
      },
    };
  });

  const stroke = "var(--hairline-strong)";
  const edges: (Edge | FlowEdge)[] = [
    ...starts.map((e): Edge => ({
      id: e.id, source: e.from, target: e.to, sourceHandle: "down", targetHandle: "top", type: "smoothstep", selectable: false, focusable: false,
      style: { stroke: "var(--hairline-strong)", strokeDasharray: "2 4" },
    })),
    ...precedes.map((e): FlowEdge => {
      const result = summarizeHandoff(handoffs.get(`${e.from}\u0000${e.to}`) ?? [])
        ?? (e.verification === "verified" ? (str(e.evidence?.handoff) as HandoffResult | null) : null);
      const label = result && handoffLabels[result] ? handoffLabels[result] : undefined;
      return {
        id: e.id, source: e.from, target: e.to, type: "flow", selectable: false, focusable: false,
        data: { label, tone: result === "mismatch" ? "error" : undefined },
        markerEnd: { type: MarkerType.ArrowClosed, color: stroke, width: 16, height: 16 },
        style: { stroke, strokeWidth: 1.5, ...(e.verification === "verified" ? {} : { strokeDasharray: "5 4" }) },
      };
    }),
  ];
  return { nodes, edges };
};

/**
 * 처음 보기·전체 보기: 그래프 전체를 상자에 맞추고 가로·세로 가운데에 둔다.
 * 여백은 좌우 같게(가운데 정렬 유지) 하되 왼쪽 아래 조작 버튼(약 41px)을 피할 만큼 둔다.
 * 위·아래 40px: 오른쪽 위 범례·오른쪽 아래 건수 띠(FlowViewOverlay, 약 28px)를 비워 노드와 겹치지 않게 한다(사용자 11/12).
 * maxZoom 1: 작은 그래프는 트리 보기와 같은 노드 크기(100%)로 두고 키우지 않는다.
 * minZoom: 긴 사슬(예: 단계 9개 이상)도 다 들어가게 낮게 둔다.
 */
const FIT_MIN_ZOOM = 0.1;
const fitOptions: FitViewOptions = { padding: { x: "48px", y: "40px" }, minZoom: FIT_MIN_ZOOM, maxZoom: 1 };

/**
 * 그래프 자료가 바뀌거나 상자 크기가 바뀌면(넓은 화면 전환 등) 다시 전체를 맞춘다. 노드 선택으로는 다시 맞추지 않는다.
 * - 첫 실행은 건너뛴다(처음 맞춤은 ReactFlow fitView 속성이 노드 측정 뒤 한다).
 * - nodesInitialized·측정 상태를 조건으로 쓰지 않는다: 노드를 속성으로만 넘기면(onNodesChange 없음) 측정 뒤에도 갱신되지 않고,
 *   노드 선택마다 노드 객체를 새로 만들어 값이 바뀌므로 선택으로 다시 맞추게 된다. 아직 측정 전이면 xyflow가 측정 뒤 맞춘다.
 */
const FitWhole = ({ dataKey }: { dataKey: unknown }) => {
  const { fitView } = useReactFlow();
  const width = useStore((s) => s.width);
  const height = useStore((s) => s.height);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    if (width > 0 && height > 0) void fitView(fitOptions);
  }, [width, height, dataKey, fitView]);
  return null;
};

const ProcessGraphView = ({ decisionId, flow, stats = null, focusTone = null }: {
  decisionId: string;
  flow: DecisionFlow;
  stats?: string | null;
  /** 판정 대상 바탕(판정 요약 결과, ProcessFlowCard focusToneOf). */
  focusTone?: FlowTone;
}) => {
  const query = useDecisionGraph(decisionId, true);
  const graph = query.data;
  const [picked, setPicked] = useState<string | null>(null);
  const focusId = graph?.nodes.find((n) => n.kind === "process" && n.attrs.focus === true)?.id ?? graph?.nodes.find((n) => n.kind !== "message")?.id ?? null;
  const selected = picked ?? focusId;
  const built = useMemo(
    () => graph && buildGraph(graph, flow, selected, setPicked, focusTone),
    [graph, flow, selected, focusTone],
  );
  // 불러오는 중·실패도 트리 보기와 같은 크기 상자 안에 둔다(보기를 바꿔도 카드 크기가 변하지 않게).
  if (query.isPending && !graph)
    return (
      <FlowViewBox>
        <LoadingState />
      </FlowViewBox>
    );
  if (!graph || !built)
    return (
      <FlowViewBox>
        <ErrorState title={analysisErrorTitle(query.error, graphLabels.graph)} onRetry={() => void query.refetch()} />
      </FlowViewBox>
    );
  const selectedNode = graph.nodes.find((n) => n.id === selected);
  const tree = selectedNode && treeNodeOf(selectedNode, flow);
  return (
    <div className="grid gap-3">
      {query.isError && (
        <ErrorState title={`${analysisErrorTitle(query.error, graphLabels.graph)} · 이전 관측값 표시`} onRetry={() => void query.refetch()} />
      )}
      {built.nodes.length ? (
        <FlowViewBox
          label={graphLabels.graph}
          overlay={<FlowViewOverlay stats={stats} notLogged={built.nodes.some((n) => n.data.kind === "missing")} />}
        >
          <ReactFlow
            nodes={built.nodes}
            edges={built.edges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            colorMode="light"
            // 오른쪽 아래는 건수(FlowViewOverlay) 자리라 React Flow 표기를 아래 가운데로 옮긴다(사용자 11/12).
            attributionPosition="bottom-center"
            fitView
            fitViewOptions={fitOptions}
            minZoom={FIT_MIN_ZOOM}
            maxZoom={2}
            nodesDraggable={false}
            nodesConnectable={false}
            nodesFocusable={false}
            edgesFocusable={false}
            elementsSelectable={false}
            zoomOnScroll={false}
            preventScrolling={false}
            ariaLabelConfig={{
              "controls.ariaLabel": graphLabels.controls,
              "controls.zoomIn.ariaLabel": graphLabels.zoomIn,
              "controls.zoomOut.ariaLabel": graphLabels.zoomOut,
              "controls.fitView.ariaLabel": graphLabels.fitView,
            }}
          >
            <FitWhole dataKey={graph} />
            <Controls showInteractive={false} fitViewOptions={fitOptions} />
          </ReactFlow>
        </FlowViewBox>
      ) : (
        <FlowViewBox overlay={<FlowViewOverlay stats={stats} />}>
          <p className="p-3 text-sm text-muted-foreground">{UNAVAILABLE}</p>
        </FlowViewBox>
      )}
      {tree && <SelectedNodeFields fields={fieldsOf(tree.node, tree.flow)} />}
    </div>
  );
};

export default ProcessGraphView;
