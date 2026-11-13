import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import ReactGridLayout, { useContainerWidth, verticalCompactor, type Layout } from "react-grid-layout";
import { IconChevronLeft, IconChevronRight, IconLayoutGridAdd, IconMinus, IconPencil, IconRefresh } from "@tabler/icons-react";
import { paths } from "@/app/routes";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardHeader } from "@/components/ui/card";
import { motionTransition } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import { useDraftStore } from "@/stores/draftStore";
import { ErrorState, LoadingState } from "@/components/ui/feedback";
import { inputErrorText, useSourcesQuery } from "../data/inputs";
import { useOperationsQuery } from "../data/useOperationsQuery";
import {
  GRID_COLUMNS,
  availableWidgets,
  cancelledLayoutMessage,
  defaultWidgetLayout,
  layoutReducer,
  parseStoredLayout,
  readLegacyLayout,
  readingOrder,
  removeLegacyLayout,
  sanitizeLayout,
  savedLayoutMessage,
  serializeLayout,
  widgetLabels,
  widgetSizes,
  type LayoutAction,
  type WidgetId,
  type WidgetPlacement,
} from "../data/widgetLayout";
import { useMySettings, useSaveMySettings } from "@/features/notifications/useNotifications";
import { WidgetBody, activeConfigurationText, type SourceState } from "./OverviewWidgets";
import { WidgetCatalog } from "./WidgetCatalog";
import { WidgetMiniature } from "./WidgetMiniature";

/** 개요 위젯 편집 중 배치는 화면 초안(draftStore, /operations)으로 둔다 → 탭 이동 유지, 이탈 시 확인. */
const draftPath = paths.operations;
/**
 * 격자 수치(react-grid-layout 설정값): 행 높이·간격 px. 위젯 영역 너비가 GRID_MIN_WIDTH 미만이면 격자 대신 읽는 순서 배치
 * (넓으면 2열·좁으면 1열, 높이는 내용만큼, 끌기·크기 조절 없음, 키보드 이동). 2칸 카드가 약 320px 미만이면 판정 추이 표가
 * 가로로 넘치므로 1000px(화면 약 1064px 이상에서 격자).
 */
const ROW_HEIGHT = 72;
const GAP = 16;
const GRID_MIN_WIDTH = 1000;
const spring = { type: "spring", stiffness: 420, damping: 30 } as const;
/** 끌기 시작 제외: 버튼·링크·입력(제거 배지·앞/뒤 버튼·위젯 안 링크). */
const DRAG_CANCEL = "button, a, input, select, textarea, [data-no-drag]";

type DragPreview = { id: WidgetId; x: number; y: number };
type Ghost = { id: WidgetId; x: number; y: number; width: number; height: number };

const toGridItems = (layout: readonly WidgetPlacement[]): Layout =>
  layout.map((item) => {
    const { minW, minH, maxW, maxH } = widgetSizes[item.i];
    return { ...item, minW, minH, maxW, maxH };
  });

/** 위젯 카드: 편집 중 살짝 흔들림·그림자, 모서리 제거 배지, 끌 때 들림(축소 동작 설정 시 멈춤). 추가될 때 스프링으로 등장. */
const WidgetCard = ({
  id,
  editing,
  grid,
  first,
  last,
  added,
  source,
  collectHref,
  onAction,
  onRemove,
}: {
  id: WidgetId;
  editing: boolean;
  grid: boolean;
  first: boolean;
  last: boolean;
  added: boolean;
  source: SourceState;
  collectHref: string;
  onAction: (action: Extract<LayoutAction, { type: "move" }>) => void;
  onRemove: (id: WidgetId, event: MouseEvent<HTMLButtonElement>) => void;
}) => {
  const label = widgetLabels[id];
  return (
    <motion.div
      className="h-full"
      initial={added ? { opacity: 0, scale: 0.9, y: 12 } : false}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      transition={spring}
    >
      <Card
        aria-label={label}
        className={cn(
          "relative h-full gap-3 py-5 transition-[box-shadow,scale] duration-200",
          editing && "animate-widget-wiggle shadow-widget-edit motion-reduce:animate-none",
          editing && grid && "cursor-grab select-none",
          "group-[.react-draggable-dragging]/item:scale-102 group-[.react-draggable-dragging]/item:animate-none group-[.react-draggable-dragging]/item:shadow-widget-lift group-[.react-draggable-dragging]/item:cursor-grabbing",
          "group-[.resizing]/item:animate-none",
        )}
      >
        <AnimatePresence>
          {editing && (
            <motion.span
              className="absolute -top-2 -left-2 z-10"
              initial={{ opacity: 0, scale: 0.6 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.6 }}
              transition={spring}
            >
              <Button
                size="icon-sm"
                variant="outline"
                aria-label={`${label} 제거`}
                onClick={(event) => onRemove(id, event)}
                className="size-6 rounded-full shadow-toast transition-[background-color,color,border-color,scale] hover:scale-110 hover:border-destructive hover:bg-destructive hover:text-primary-foreground"
              >
                <IconMinus className="size-3" aria-hidden="true" />
              </Button>
            </motion.span>
          )}
        </AnimatePresence>
        <CardHeader className="px-5">
          <h2 className="text-section-title">{label}</h2>
          {editing && (
            <CardAction className="flex gap-1">
              <Button size="icon-sm" variant="ghost" className="size-6" disabled={first} aria-label={`${label} 앞으로`} onClick={() => onAction({ type: "move", id, direction: -1 })}>
                <IconChevronLeft aria-hidden="true" />
              </Button>
              <Button size="icon-sm" variant="ghost" className="size-6" disabled={last} aria-label={`${label} 뒤로`} onClick={() => onAction({ type: "move", id, direction: 1 })}>
                <IconChevronRight aria-hidden="true" />
              </Button>
            </CardAction>
          )}
        </CardHeader>
        <CardContent className={cn("grid min-h-0 flex-1 content-start gap-4 px-5", grid && "overflow-auto")}>
          <WidgetBody id={id} source={source} collectHref={collectHref} />
        </CardContent>
      </Card>
    </motion.div>
  );
};

/**
 * 운영 현황 개요: 현재 운영 줄 + 위젯 격자(react-grid-layout, #100 이전 홈 위젯 방식).
 * 편집: 끌어서 이동·모서리로 크기 조절(다른 위젯은 부드럽게 다시 배치), 위젯 추가 목록(끌어 놓기·추가 버튼), 제거 배지,
 * 키보드 대안(앞으로/뒤로·제거 버튼), 배치 저장·기본 배치로 초기화·편집 취소. 좁은 화면은 한 줄 배치.
 * 배치는 계정별 서버 저장(GET·PUT /api/me/settings). 불러오기 전에는 위젯을 그리지 않고, 불러오지 못하면 기본 배치로 보이되 편집·저장은 막는다
 * (저장된 배치를 모르는 채 덮어쓰지 않음). 저장에 실패하면 편집 중 배치를 그대로 두고 다시 저장할 수 있다.
 */
export const OverviewTab = ({ collectHref }: { collectHref: string }) => {
  const { user } = useAuthSession();
  const role = user?.userType === "admin" ? "admin" : "user";
  const cache = useQueryClient();
  const overview = useOperationsQuery();
  const sources = useSourcesQuery();
  const settings = useMySettings();
  const saveLayout = useSaveMySettings();
  const storedLayout = settings.data?.widgetLayout.layout ?? null;
  const accountId = user?.id ?? "";
  // 옛 브라우저 저장 배치(서버 저장 전): 서버에 배치가 없을 때만 쓰고 서버로 한 번 옮긴다.
  const [legacy] = useState(() => readLegacyLayout(accountId));
  const useLegacy = settings.isSuccess && !storedLayout && Boolean(legacy);
  const saved = useMemo<readonly WidgetPlacement[]>(
    () => parseStoredLayout(storedLayout) ?? (useLegacy ? legacy : null) ?? defaultWidgetLayout.map((item) => ({ ...item })),
    [storedLayout, useLegacy, legacy],
  );
  const migrated = useRef(false);
  useEffect(() => {
    if (!settings.isSuccess || migrated.current || !accountId) return;
    migrated.current = true;
    if (storedLayout || !legacy) {
      removeLegacyLayout(accountId);
      return;
    }
    saveLayout.mutate({ widgetLayout: serializeLayout(legacy) }, { onSuccess: () => removeLegacyLayout(accountId) });
  }, [accountId, legacy, saveLayout, settings.isSuccess, storedLayout]);
  const layoutReady = settings.isSuccess;
  const [status, setStatus] = useState("");
  const [saveFailed, setSaveFailed] = useState(false);
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [dragPreview, setDragPreview] = useState<DragPreview | null>(null);
  const [ghost, setGhost] = useState<Ghost | null>(null);
  const [added, setAdded] = useState<ReadonlySet<WidgetId>>(new Set());
  const { width, containerRef, mounted } = useContainerWidth({ initialWidth: 1216 });
  const draft = useDraftStore((state) => state.drafts[draftPath]);
  const editing = draft ? (sanitizeLayout(draft.value) ?? [...saved]) : null;
  const layout = editing ?? saved;
  const grid = mounted && width >= GRID_MIN_WIDTH;

  const activeSource = overview.data?.activeConfiguration?.sourceId;
  const source: SourceState = sources.isPending
    ? { status: "loading" }
    : sources.isError
      ? { status: "error", title: inputErrorText(sources.error), retry: () => void sources.refetch() }
      : { status: "ready", id: sources.data?.find((item) => item.id === activeSource)?.id ?? sources.data?.[0]?.id ?? "" };

  // 최신 편집 배치(창 이벤트 처리기에서 읽음).
  const editingRef = useRef(editing);
  editingRef.current = editing;
  const apply = useCallback((action: LayoutAction) => {
    const current = editingRef.current;
    if (!current) return;
    const next = layoutReducer(current, action);
    if (next !== current) useDraftStore.getState().edit(draftPath, next.map((item) => ({ ...item })));
    if (action.type === "add" && next !== current) setAdded((ids) => new Set(ids).add(action.id));
  }, []);
  // 키보드 초점 유지: 이동 후 끝에 닿아 버튼이 비활성되면 반대 방향 버튼, 제거 후에는 이웃 위젯의 제거 버튼(없으면 위젯 추가).
  const [focusTarget, setFocusTarget] = useState<string | null>(null);
  useEffect(() => {
    if (!focusTarget || typeof document.querySelector !== "function") return;
    const timer = window.setTimeout(() => {
      const element = document.querySelector<HTMLButtonElement>(focusTarget);
      (element && !element.disabled ? element : document.querySelector<HTMLElement>('[data-focus="add-widget"]'))?.focus();
      setFocusTarget(null);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [focusTarget]);
  const buttonFor = (id: WidgetId, kind: "앞으로" | "뒤로" | "제거") => `[aria-label="${widgetLabels[id]} ${kind}"]`;
  const move = (action: Extract<LayoutAction, { type: "move" }>) => {
    const current = editingRef.current;
    if (!current) return;
    const order = readingOrder(layoutReducer(current, action));
    const index = order.indexOf(action.id);
    const atEdge = action.direction < 0 ? index === 0 : index === order.length - 1;
    apply(action);
    setFocusTarget(buttonFor(action.id, (action.direction < 0) !== atEdge ? "앞으로" : "뒤로"));
  };
  const startEdit = () => {
    if (!layoutReady) return;
    setStatus("");
    setSaveFailed(false);
    setAdded(new Set());
    useDraftStore.getState().edit(draftPath, saved.map((item) => ({ ...item })));
  };
  const save = () => {
    if (!editing || saveLayout.isPending) return;
    const next = editing.map((item) => ({ ...item }));
    setSaveFailed(false);
    setStatus("");
    saveLayout.mutate(
      { widgetLayout: serializeLayout(next) },
      {
        onSuccess: () => {
          useDraftStore.getState().discard(draftPath);
          setCatalogOpen(false);
          setStatus(savedLayoutMessage(role, next.length));
        },
        onError: () => setSaveFailed(true),
      },
    );
  };
  const cancel = () => {
    useDraftStore.getState().discard(draftPath);
    setCatalogOpen(false);
    setSaveFailed(false);
    setStatus(cancelledLayoutMessage);
  };
  const refresh = () =>
    void cache.refetchQueries({
      type: "active",
      predicate: (query) => {
        const name = String(query.queryKey[0]);
        return name.startsWith("operations-") || name === "pipeline-sources";
      },
    });
  // 제거: 카드 자리에 잔상을 남겨 작아지며 사라지게 한다(격자는 즉시 다시 배치).
  const remove = (id: WidgetId, event: MouseEvent<HTMLButtonElement>) => {
    const rect = event.currentTarget.closest("[data-widget]")?.getBoundingClientRect();
    if (rect) setGhost({ id, x: rect.left, y: rect.top, width: rect.width, height: rect.height });
    const order = readingOrder(editingRef.current ?? []);
    const index = order.indexOf(id);
    const neighbor = order[index + 1] ?? order[index - 1];
    apply({ type: "remove", id });
    setFocusTarget(neighbor ? buttonFor(neighbor, "제거") : '[data-focus="add-widget"]');
  };
  // 목록에서 끌기: 목록을 닫고 포인터를 따라 미리보기를 띄운다. 격자 위에서 놓으면 그 칸에, 밖이면 취소.
  const startCatalogDrag = (id: WidgetId, point: { x: number; y: number }) => {
    setCatalogOpen(false);
    setDragPreview({ id, ...point });
  };
  const dragging = dragPreview?.id;
  useEffect(() => {
    if (!dragging) return;
    const move = (event: PointerEvent) => setDragPreview((current) => (current ? { ...current, x: event.clientX, y: event.clientY } : null));
    const up = (event: PointerEvent) => {
      const frame = containerRef.current?.getBoundingClientRect();
      if (frame && event.clientX >= frame.left && event.clientX <= frame.right && event.clientY >= frame.top && event.clientY <= frame.bottom + ROW_HEIGHT) {
        const size = widgetSizes[dragging];
        const column = (frame.width + GAP) / GRID_COLUMNS;
        const at = grid
          ? {
              x: Math.max(0, Math.min(GRID_COLUMNS - size.w, Math.round((event.clientX - frame.left) / column - size.w / 2))),
              y: Math.max(0, Math.round((event.clientY - frame.top) / (ROW_HEIGHT + GAP) - size.h / 2)),
            }
          : undefined;
        apply({ type: "add", id: dragging, at });
      }
      setDragPreview(null);
    };
    const escape = (event: KeyboardEvent) => event.key === "Escape" && setDragPreview(null);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("keydown", escape);
    };
  }, [apply, containerRef, dragging, grid]);

  const order = readingOrder(layout);
  const card = (id: WidgetId) => (
    <WidgetCard
      id={id}
      editing={Boolean(editing)}
      grid={grid}
      first={order[0] === id}
      last={order.at(-1) === id}
      added={added.has(id)}
      source={source}
      collectHref={collectHref}
      onAction={move}
      onRemove={remove}
    />
  );
  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm">
          현재 운영: <strong>{overview.isPending ? "조회 중" : activeConfigurationText(overview.data)}</strong>
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" onClick={refresh} disabled={overview.isFetching}>
            <IconRefresh aria-hidden="true" />
            {overview.isFetching ? "운영 관측 조회 중" : "운영 관측 새로고침"}
          </Button>
          {!editing && (
            <Button variant="outline" size="sm" onClick={startEdit} disabled={!layoutReady}>
              <IconPencil aria-hidden="true" />
              위젯 편집
            </Button>
          )}
        </div>
      </div>
      <AnimatePresence initial={false}>
        {editing && (
          <motion.div
            key="edit-bar"
            role="group"
            aria-label="위젯 편집"
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={motionTransition("base")}
            className="flex flex-wrap items-center gap-2 rounded-lg border bg-card px-4 py-3 text-sm shadow-xs"
          >
            <strong className="mr-auto">위젯 편집 중</strong>
            <Button size="sm" variant="outline" data-focus="add-widget" onClick={() => setCatalogOpen(true)}>
              <IconLayoutGridAdd aria-hidden="true" />
              위젯 추가
            </Button>
            <Button size="sm" variant="outline" onClick={() => apply({ type: "reset" })}>
              기본 배치로 초기화
            </Button>
            <Button size="sm" variant="outline" onClick={cancel}>
              편집 취소
            </Button>
            <Button size="sm" onClick={save} disabled={saveLayout.isPending}>
              {saveLayout.isPending ? "배치 저장 중" : "배치 저장"}
            </Button>
          </motion.div>
        )}
      </AnimatePresence>
      {settings.isError && (
        <ErrorState title="저장된 위젯 배치를 불러오지 못했습니다" onRetry={() => void settings.refetch()}>
          기본 배치로 표시합니다. 다시 불러온 뒤 편집할 수 있습니다.
        </ErrorState>
      )}
      <div ref={containerRef} aria-label="운영 위젯" className="@container min-w-0">
        {settings.isPending ? (
          <LoadingState>위젯 배치를 불러오는 중...</LoadingState>
        ) : grid ? (
          <ReactGridLayout
            width={width}
            layout={toGridItems(layout)}
            gridConfig={{ cols: GRID_COLUMNS, rowHeight: ROW_HEIGHT, margin: [GAP, GAP], containerPadding: [0, 0] }}
            dragConfig={{ enabled: Boolean(editing), bounded: false, cancel: DRAG_CANCEL, threshold: 4 }}
            resizeConfig={{ enabled: Boolean(editing), handles: ["se"] }}
            compactor={verticalCompactor}
            onLayoutChange={(next) => apply({ type: "change", layout: next as WidgetPlacement[] })}
            className={cn(
              "[&_.react-grid-placeholder]:rounded-lg [&_.react-grid-placeholder]:border [&_.react-grid-placeholder]:border-dashed [&_.react-grid-placeholder]:border-ring [&_.react-grid-placeholder]:bg-muted [&_.react-grid-placeholder]:opacity-70",
              // 크기 조절 손잡이: 라이브러리 기본 그림 대신 토큰 색 모서리 선.
              "[&_.react-resizable-handle]:z-10 [&_.react-resizable-handle]:bg-none [&_.react-resizable-handle]:opacity-70 hover:[&_.react-resizable-handle]:opacity-100 [&_.react-resizable-handle]:after:border-muted-foreground",
            )}
          >
            {/* 자식 순서 = 읽는 순서(화면 순서와 같은 Tab·낭독 순서). 위치는 layout의 x, y로 정한다. */}
            {order.map((id) => (
              <div key={id} data-widget={id} className="group/item">
                {card(id)}
              </div>
            ))}
          </ReactGridLayout>
        ) : (
          <div className="grid gap-4 @2xl:grid-cols-2">
            {order.map((id) => (
              <div key={id} data-widget={id}>
                {card(id)}
              </div>
            ))}
          </div>
        )}
      </div>
      <WidgetCatalog
        open={catalogOpen}
        widgets={availableWidgets(layout)}
        onOpenChange={setCatalogOpen}
        onAdd={(id) => {
          apply({ type: "add", id });
          setCatalogOpen(false);
        }}
        onDragStart={startCatalogDrag}
      />
      <AnimatePresence>
        {dragPreview && (
          <motion.div
            key="drag-preview"
            aria-hidden="true"
            initial={{ opacity: 0, scale: 0.92, x: dragPreview.x, y: dragPreview.y }}
            animate={{ opacity: 1, scale: 1, x: dragPreview.x, y: dragPreview.y }}
            exit={{ opacity: 0, scale: 0.92 }}
            transition={{ x: { duration: 0 }, y: { duration: 0 }, default: spring }}
            className="pointer-events-none fixed top-0 left-0 z-(--z-drag) -translate-x-1/2 -translate-y-1/2"
          >
            <div className="grid h-widget-preview w-widget-drag overflow-hidden rounded-lg border bg-card shadow-widget-lift">
              <WidgetMiniature id={dragPreview.id} />
              <span className="border-t px-3 py-1.5 text-xs font-medium">{widgetLabels[dragPreview.id]}</span>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      <AnimatePresence>
        {ghost && (
          <motion.div
            key={`ghost-${ghost.id}`}
            aria-hidden="true"
            initial={{ opacity: 1, scale: 1, x: ghost.x, y: ghost.y, width: ghost.width, height: ghost.height }}
            animate={{ opacity: 0, scale: 0.9, x: ghost.x, y: ghost.y + 12, width: ghost.width, height: ghost.height }}
            transition={{ duration: 0.25, ease: [0.4, 0, 0.2, 1] }}
            onAnimationComplete={() => setGhost(null)}
            className="pointer-events-none fixed top-0 left-0 z-(--z-popover)"
          >
            <Card className="h-full gap-3 py-5 shadow-widget-edit">
              <CardHeader className="px-5">
                <h2 className="text-section-title">{widgetLabels[ghost.id]}</h2>
              </CardHeader>
            </Card>
          </motion.div>
        )}
      </AnimatePresence>
      <p role="status" className="text-sm text-muted-foreground">
        {status}
      </p>
      {saveFailed && (
        <Alert variant="destructive">
          <AlertDescription>배치를 저장하지 못했습니다. 편집 중인 배치를 그대로 두었으니 다시 저장해 주세요.</AlertDescription>
        </Alert>
      )}
    </div>
  );
};
