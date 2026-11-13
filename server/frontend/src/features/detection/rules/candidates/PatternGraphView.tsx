import "@xyflow/react/dist/style.css";
import { useMemo } from "react";
import dagre from "@dagrejs/dagre";
import {
  BaseEdge, Controls, EdgeLabelRenderer, getSmoothStepPath, Handle, MarkerType, Position, ReactFlow,
  type Edge, type EdgeProps, type FitViewOptions, type Node, type NodeProps,
} from "@xyflow/react";
import { cn } from "@/lib/utils";
import { FLOW_NODE_W, flowNodeClass, FlowViewBox } from "@/features/analysis/components/ProcessFlowCard";
import { graphLabels } from "@/features/analysis/labels";
import type { PatternEdge, PatternNode } from "./candidateApi";
import { n, rateText } from "./candidateLabels";

/**
 * 인터페이스 정상 패턴 그래프(층 B, contracts/process-graph.md `layer: "pattern"`) 읽기 전용 보기. 실행 순서 보기(ProcessGraphView)와 같은
 * React Flow + dagre(왼쪽 → 오른쪽) 배치, 같은 노드 폭·상자.
 * - 노드: pattern_step(단계 ID · 어댑터 · 관측 거래 수). 끌기·선택·연결 없음
 * - 간선: precedes만(from 선행 → to 후행). 이름 = 선행 충족률(evidence.satisfactionRate, Trainer 건수 기준 from processId × to 단계).
 *   hands_off·branches_to는 같은 두 단계 사이라 선을 겹쳐 그리지 않는다(인계는 아래 후보 표에 따로 있음)
 * - id는 쪼개 읽지 않고 from·to로만 잇는다
 */
const NODE_H = 56;
const fitOptions: FitViewOptions = { padding: { x: "48px", y: "40px" }, minZoom: 0.1, maxZoom: 1 };

type StepData = { processId: string; adapterType: string | null; transactions: number | null };
type StepNode = Node<StepData, "step">;
const StepCard = ({ data }: NodeProps<StepNode>) => (
  <>
    <Handle type="target" position={Position.Left} isConnectable={false} className="!size-1.5 !border-0 !bg-transparent" />
    <div
      data-pattern-step={data.processId}
      title={data.processId}
      style={{ width: FLOW_NODE_W }}
      className={cn(flowNodeClass({ tone: null, focus: false, selected: false }), "grid gap-0.5 px-2 py-1 text-card-foreground hover:bg-card")}
    >
      <span className="line-clamp-2 min-w-0 font-medium wrap-anywhere">{data.processId}</span>
      <span className="flex flex-wrap gap-x-2 text-xs text-muted-foreground">
        <span>{data.adapterType ?? "어댑터 없음"}</span>
        {data.transactions !== null && <span>거래 {n(data.transactions)}</span>}
      </span>
    </div>
    <Handle type="source" position={Position.Right} isConnectable={false} className="!size-1.5 !border-0 !bg-transparent" />
  </>
);

type RateData = { label: string };
type RateEdge = Edge<RateData, "rate">;
const RateStepEdge = ({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, markerEnd, style, data }: EdgeProps<RateEdge>) => {
  const [path, labelX, labelY] = getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  return (
    <>
      <BaseEdge path={path} markerEnd={markerEnd} style={style} />
      {data?.label && (
        <EdgeLabelRenderer>
          <span
            data-edge-rate=""
            className="nodrag nopan pointer-events-none absolute rounded border bg-card px-1 text-[11px] leading-4 whitespace-nowrap text-foreground"
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          >
            {data.label}
          </span>
        </EdgeLabelRenderer>
      )}
    </>
  );
};
const nodeTypes = { step: StepCard };
const edgeTypes = { rate: RateStepEdge };

export const buildPatternGraph = (nodes: PatternNode[], edges: PatternEdge[]) => {
  const ids = new Set(nodes.map((node) => node.id));
  const precedes = edges.filter((edge) => edge.kind === "precedes" && ids.has(edge.from) && ids.has(edge.to));
  const layout = new dagre.graphlib.Graph();
  layout.setGraph({ rankdir: "LR", nodesep: 28, ranksep: 110, marginx: 0, marginy: 0 });
  layout.setDefaultEdgeLabel(() => ({}));
  for (const node of nodes) layout.setNode(node.id, { width: FLOW_NODE_W, height: NODE_H });
  for (const edge of precedes) layout.setEdge(edge.from, edge.to);
  if (nodes.length) dagre.layout(layout);
  const stroke = "var(--hairline-strong)";
  return {
    nodes: nodes.map((node): StepNode => {
      const point = layout.node(node.id);
      return {
        id: node.id, type: "step", draggable: false, selectable: false,
        position: { x: point.x - FLOW_NODE_W / 2, y: point.y - NODE_H / 2 },
        data: { processId: node.attrs.processId ?? node.id, adapterType: node.attrs.adapterType, transactions: node.attrs.transactions },
      };
    }),
    edges: precedes.map((edge): RateEdge => ({
      id: edge.id, source: edge.from, target: edge.to, type: "rate", selectable: false, focusable: false,
      data: { label: rateText(edge.evidence.satisfactionRate) },
      markerEnd: { type: MarkerType.ArrowClosed, color: stroke, width: 16, height: 16 },
      style: { stroke, strokeWidth: 1.5 },
    })),
  };
};

const PatternGraphView = ({ nodes, edges, label }: { nodes: PatternNode[]; edges: PatternEdge[]; label: string }) => {
  const built = useMemo(() => buildPatternGraph(nodes, edges), [nodes, edges]);
  return (
    <FlowViewBox
      label={label}
      overlay={
        <>
          <p data-pattern-legend="" className="pointer-events-none absolute top-2 right-2 z-10 rounded bg-muted/90 px-1 text-xs text-muted-foreground">
            화살표 숫자 · 선행 충족률
          </p>
          <p data-pattern-counts="" className="pointer-events-none absolute right-2 bottom-2 z-10 rounded bg-muted/90 px-1 text-xs text-muted-foreground">
            단계 {n(built.nodes.length)} · 선행 관계 {n(built.edges.length)}
          </p>
        </>
      }
    >
      <ReactFlow
        nodes={built.nodes}
        edges={built.edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        colorMode="light"
        attributionPosition="bottom-center"
        fitView
        fitViewOptions={fitOptions}
        minZoom={0.1}
        maxZoom={2}
        nodesDraggable={false}
        nodesConnectable={false}
        nodesFocusable={false}
        edgesFocusable={false}
        elementsSelectable={false}
        zoomOnScroll={false}
        preventScrolling={false}
        ariaLabelConfig={{
          "controls.ariaLabel": "패턴 그래프 조작",
          "controls.zoomIn.ariaLabel": graphLabels.zoomIn,
          "controls.zoomOut.ariaLabel": graphLabels.zoomOut,
          "controls.fitView.ariaLabel": graphLabels.fitView,
        }}
      >
        <Controls showInteractive={false} fitViewOptions={fitOptions} />
      </ReactFlow>
    </FlowViewBox>
  );
};

export default PatternGraphView;
