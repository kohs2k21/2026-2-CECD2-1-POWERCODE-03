import { useState, type DragEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { IconRefresh } from "@tabler/icons-react";
import { paths } from "@/app/routes";
import { SectionCard } from "@/components/layout/SectionCard";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import { useDraftStore } from "@/stores/draftStore";
import { inputErrorText, useSourcesQuery } from "../data/inputs";
import { useOperationsQuery } from "../data/useOperationsQuery";
import {
  availableWidgets,
  browserWidgetLayoutStore,
  cancelledLayoutMessage,
  defaultWidgetLayout,
  layoutReducer,
  sanitizeLayout,
  savedLayoutMessage,
  widgetLabels,
  type LayoutAction,
  type WidgetId,
  type WidgetLayoutStore,
} from "../data/widgetLayout";
import { WidgetBody, activeConfigurationText, type SourceState } from "./OverviewWidgets";

/** 개요 위젯 편집 중 배치는 화면 초안(draftStore, /operations)으로 둔다 → 탭 이동 유지, 이탈 시 확인. */
const draftPath = paths.operations;

/** 운영 현황 개요: 현재 운영 줄 + 위젯 편집(앞/뒤 이동·제거·추가·끌어서 놓기·저장·초기화·취소). */
export const OverviewTab = ({
  collectHref,
  layoutStore = browserWidgetLayoutStore,
}: {
  collectHref: string;
  layoutStore?: WidgetLayoutStore;
}) => {
  const { user } = useAuthSession();
  const accountId = user?.id ?? "";
  const role = user?.userType === "admin" ? "admin" : "user";
  const cache = useQueryClient();
  const overview = useOperationsQuery();
  const sources = useSourcesQuery();
  const [saved, setSaved] = useState<readonly WidgetId[]>(() => layoutStore.load(accountId) ?? [...defaultWidgetLayout]);
  const [status, setStatus] = useState("");
  const [saveFailed, setSaveFailed] = useState(false);
  const [dragging, setDragging] = useState<WidgetId | null>(null);
  const [over, setOver] = useState<WidgetId | null>(null);
  const draft = useDraftStore((state) => state.drafts[draftPath]);
  const editing = draft ? (sanitizeLayout(draft.value) ?? [...saved]) : null;
  const layout = editing ?? saved;

  const activeSource = overview.data?.activeConfiguration?.sourceId;
  const source: SourceState = sources.isPending
    ? { status: "loading" }
    : sources.isError
      ? { status: "error", title: inputErrorText(sources.error), retry: () => void sources.refetch() }
      : { status: "ready", id: sources.data?.find((item) => item.id === activeSource)?.id ?? sources.data?.[0]?.id ?? "" };

  const edit = (next: readonly WidgetId[]) => useDraftStore.getState().edit(draftPath, [...next]);
  const apply = (action: LayoutAction) => {
    if (!editing) return;
    const next = layoutReducer(editing, action);
    if (next !== editing) edit(next);
  };
  const startEdit = () => {
    setStatus("");
    setSaveFailed(false);
    edit(saved);
  };
  const save = () => {
    if (!editing) return;
    const ok = layoutStore.save(accountId, editing);
    setSaved([...editing]);
    useDraftStore.getState().discard(draftPath);
    setSaveFailed(!ok);
    setStatus(ok ? savedLayoutMessage(role, editing.length) : "");
  };
  const cancel = () => {
    useDraftStore.getState().discard(draftPath);
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

  const dragProps = (id: WidgetId) =>
    editing
      ? {
          draggable: true,
          onDragStart: (event: DragEvent) => {
            setDragging(id);
            try {
              event.dataTransfer.setData("text/plain", id);
            } catch {
              // 일부 브라우저는 dataTransfer 쓰기를 제한한다. 끌기 대상은 상태로 기억한다.
            }
          },
          onDragOver: (event: DragEvent) => {
            if (!dragging) return;
            event.preventDefault();
            setOver(id);
          },
          onDrop: (event: DragEvent) => {
            if (!dragging) return;
            event.preventDefault();
            apply({ type: "drop", id: dragging, before: id });
            setDragging(null);
            setOver(null);
          },
          onDragEnd: () => {
            setDragging(null);
            setOver(null);
          },
        }
      : {};

  const available = availableWidgets(layout);
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
            <Button variant="outline" size="sm" onClick={startEdit}>
              위젯 편집
            </Button>
          )}
        </div>
      </div>
      {editing && (
        <div role="group" aria-label="위젯 편집" className="flex flex-wrap items-center gap-2 rounded-lg border bg-card px-4 py-3 text-sm">
          <strong>위젯 편집 중</strong>
            <Button size="sm" onClick={save}>
              배치 저장
            </Button>
            <Button size="sm" variant="outline" onClick={() => apply({ type: "reset" })}>
              기본 배치로 초기화
            </Button>
            <Button size="sm" variant="outline" onClick={cancel}>
              편집 취소
            </Button>
        </div>
      )}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3" aria-label="운영 위젯">
        {layout.map((id, index) => (
          <div
            key={id}
            data-widget={id}
            className={cn("min-w-0", editing && "cursor-grab", over === id && dragging !== id && "rounded-xl ring-2 ring-ring")}
            {...dragProps(id)}
          >
            <SectionCard
              title={widgetLabels[id]}
              aria-label={widgetLabels[id]}
              className={cn("h-full", editing && "border-dashed border-ring")}
              action={
                editing && (
                  <>
                    <Button size="sm" variant="outline" disabled={index === 0} aria-label={`${widgetLabels[id]} 앞으로`}
                      onClick={() => apply({ type: "move", id, direction: -1 })}>
                      ◀ 앞으로
                    </Button>
                    <Button size="sm" variant="outline" disabled={index === layout.length - 1} aria-label={`${widgetLabels[id]} 뒤로`}
                      onClick={() => apply({ type: "move", id, direction: 1 })}>
                      뒤로 ▶
                    </Button>
                    <Button size="sm" variant="outline" aria-label={`${widgetLabels[id]} 제거`} onClick={() => apply({ type: "remove", id })}>
                      제거
                    </Button>
                  </>
                )
              }
            >
              <WidgetBody id={id} source={source} collectHref={collectHref} />
            </SectionCard>
          </div>
        ))}
      </div>
      {editing && (
        <SectionCard title="위젯 추가" aria-label="위젯 추가">
          {available.length ? (
            <ul className="grid gap-2">
              {available.map((id) => (
                <li key={id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                  <span>{widgetLabels[id]}</span>
                  <Button size="sm" variant="outline" aria-label={`${widgetLabels[id]} 추가`} onClick={() => apply({ type: "add", id })}>
                    추가
                  </Button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">추가할 수 있는 위젯을 모두 배치했습니다.</p>
          )}
        </SectionCard>
      )}
      <p role="status" className="text-sm text-muted-foreground">
        {status}
      </p>
      {saveFailed && (
        <Alert variant="destructive">
          <AlertDescription>배치를 브라우저에 저장하지 못했습니다. 현재 화면에서만 유지합니다.</AlertDescription>
        </Alert>
      )}
    </div>
  );
};
