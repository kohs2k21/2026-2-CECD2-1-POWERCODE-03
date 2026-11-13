import { useState } from "react";
import { Link } from "react-router-dom";
import { IconArrowRight, IconCheck } from "@tabler/icons-react";
import { paths } from "@/app/routes";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { riskLevelVariant, stampText } from "@/features/analysis/labels";
import { cn } from "@/lib/utils";
import { emptyLabels, notificationRisk, notificationText, riskText, showsRisk, viewLabels } from "./notificationLabels";
import { notificationViews, type NotificationItem, type NotificationView } from "./notificationsApi";
import { useMarkNotificationsRead, useNotificationPage, useUnreadCount } from "./useNotifications";

/** 항목 상세 주소: 판정 → 이상 분석 판정 상세(판정 ID 또는 최종 전 작업 ID), 사건 → 사건 상세(흐름 첫 사건 ID). */
export const notificationHref = (item: NotificationItem) =>
  item.type === "decision" ? paths.analysisDecision(item.decision.detailId) : paths.analysisIncident(item.incident.id);

const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));

const NotificationRow = ({ item, onRead, onNavigate, busy }: { item: NotificationItem; onRead: (item: NotificationItem) => void; onNavigate: (item: NotificationItem) => void; busy: boolean }) => {
  const { headline, subject } = notificationText(item);
  const risk = notificationRisk(item);
  return (
    <li data-notification={item.key} className={cn("grid gap-1.5 rounded-md border px-3 py-2 text-sm", item.unread ? "border-ring/40 bg-accent/40" : "border-transparent")}>
      <span className="font-medium">{headline}</span>
      <span className="break-all text-muted-foreground">{subject}</span>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Badge variant="outline">이상 분석</Badge>
        {showsRisk(item) && (
          <Badge variant={risk.riskStatus === "rated" && risk.riskLevel ? riskLevelVariant[risk.riskLevel] : "outline"}>{riskText(risk)}</Badge>
        )}
        <span>{stampText(item.notifiedAt)}</span>
        {item.unread && <span className="font-medium text-foreground">읽지 않음</span>}
      </div>
      <div className="flex flex-wrap justify-end gap-2">
        {item.unread && (
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => onRead(item)}>
            <IconCheck aria-hidden="true" />
            읽음 처리
          </Button>
        )}
        <Button asChild variant="outline" size="sm">
          <Link to={notificationHref(item)} onClick={() => onNavigate(item)}>
            이동
            <IconArrowRight aria-hidden="true" />
          </Link>
        </Button>
      </div>
    </li>
  );
};

/** 패널 한 쪽. 마지막 쪽이면 더 보기(다음 커서)를 단다. */
const NotificationPage = ({ view, cursor, last, onMore, onRead, onNavigate, busy }: {
  view: NotificationView; cursor: string | null; last: boolean; onMore: (cursor: string) => void;
  onRead: (item: NotificationItem) => void; onNavigate: (item: NotificationItem) => void; busy: boolean;
}) => {
  const page = useNotificationPage(view, cursor, true);
  if (page.isPending) return <LoadingState className="py-3">알림을 불러오는 중...</LoadingState>;
  if (page.isError) return <ErrorState title="알림을 불러오지 못했습니다" onRetry={() => void page.refetch()}>{errorText(page.error)}</ErrorState>;
  const data = page.data;
  return (
    <>
      {cursor === null && !data.items.length && <EmptyState className="py-4">{emptyLabels[view]}</EmptyState>}
      {data.items.length > 0 && (
        <ul className="grid gap-1.5">
          {data.items.map((item) => <NotificationRow key={item.key} item={item} onRead={onRead} onNavigate={onNavigate} busy={busy} />)}
        </ul>
      )}
      {last && data.nextCursor && (
        <Button variant="ghost" size="sm" className="justify-self-center" onClick={() => onMore(data.nextCursor!)}>
          더 보기
        </Button>
      )}
      {last && data.window.truncated && (
        <p className="text-xs text-muted-foreground">최근 판정 이벤트 {data.window.events.toLocaleString("ko-KR")}건 안의 알림만 보입니다.</p>
      )}
    </>
  );
};

/**
 * 알림 패널 본문(contracts/notifications.md): 서버가 만든 알림(사건 단위 + 묶이지 않은 판정)을 사용자 알림 설정으로 거른 목록.
 * 읽지 않음/전체 보기, 모두 읽음, 항목 읽음 처리, 이동(이동하면 그 항목 읽음). 미확인 수·읽음 상태는 서버 저장값만 쓴다.
 */
export const NotificationPanels = ({ onNavigate }: { onNavigate?: () => void }) => {
  const [view, setView] = useState<NotificationView>("unread");
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const unread = useUnreadCount();
  const mark = useMarkNotificationsRead();
  const reset = () => setCursors([null]);
  const readOne = (item: NotificationItem) => mark.mutate({ items: [{ key: item.key, seq: item.seq }] }, { onSuccess: reset });
  const go = (item: NotificationItem) => {
    if (item.unread) mark.mutate({ items: [{ key: item.key, seq: item.seq }] });
    onNavigate?.();
  };
  const readAll = () => unread.data && mark.mutate({ through: unread.data.latestSeq }, { onSuccess: reset });
  return (
    <section className="grid gap-3" aria-label="알림 목록">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div role="group" aria-label="알림 보기" className="inline-flex rounded-md border p-0.5">
          {notificationViews.map((value) => (
            <Button
              key={value}
              variant={view === value ? "secondary" : "ghost"}
              size="sm"
              aria-pressed={view === value}
              className="h-7"
              onClick={() => {
                setView(value);
                reset();
              }}
            >
              {viewLabels[value]}
            </Button>
          ))}
        </div>
        <Button variant="outline" size="sm" onClick={readAll} disabled={!unread.data?.unreadCount || mark.isPending}>
          모두 읽음
        </Button>
      </div>
      {mark.isError && <p className="text-xs text-destructive-text" role="alert">읽음 상태를 저장하지 못했습니다. {errorText(mark.error)}</p>}
      <div className="grid gap-2">
        {cursors.map((cursor, index) => (
          <NotificationPage
            key={`${view}:${cursor ?? "first"}`}
            view={view}
            cursor={cursor}
            last={index === cursors.length - 1}
            onMore={(next) => setCursors((list) => [...list, next])}
            onRead={readOne}
            onNavigate={go}
            busy={mark.isPending}
          />
        ))}
      </div>
    </section>
  );
};
