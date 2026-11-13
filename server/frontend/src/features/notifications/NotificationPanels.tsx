import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { IconArrowRight, IconCheck } from "@tabler/icons-react";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import { paths } from "@/app/routes";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { riskLevelVariant, stampText } from "@/features/analysis/labels";
import { emptyLabels, notificationRisk, notificationText, protocolResolutionLabels, riskText, showsRisk, viewLabels } from "./notificationLabels";
import { notificationViews, type NotificationItem, type NotificationView, type ProtocolNotification } from "./notificationsApi";
import { useKeepProtocolAlert, useMarkNotificationsRead, useNotificationPage, useUnreadCount } from "./useNotifications";
import { ConfirmDialog } from "@/features/detection/rules/RuleDialogs";

/** 항목 상세 주소: 판정 → 이상 분석 판정 상세(판정 ID 또는 최종 전 작업 ID), 사건 → 사건 상세(흐름 첫 사건 ID), 프로토콜 → 최근 판정 상세. */
export const notificationHref = (item: NotificationItem) =>
  item.type === "decision" ? paths.analysisDecision(item.decision.detailId)
    : item.type === "incident" ? paths.analysisIncident(item.incident.id) : paths.analysisDecision(item.protocol.decisionId);
/** 프로토콜 상세로 넘기는 값: 알림의 경로를 허용 경로 초안에 더해 연다(저장은 상세의 확인창 → 룰 세트 새 버전). */
export type ProtocolAlertState = { returnTo?: string; alertPath?: { channels: string[]; decisionId: string } };

/** [이상 유지] 확인 문구(되돌릴 수 없음·모든 관리자에게 적용). */
export const keepConfirmMessage = ({ protocol }: ProtocolNotification) =>
  `${protocol.interfaceId}${protocol.channels ? ` · ${protocol.channels.join(" → ")}` : ""} 알림을 이상으로 유지합니다. 같은 경로는 다시 알리지 않으며 되돌릴 수 없습니다(모든 관리자에게 적용).`;

/** [이상 유지] 확인창(되돌릴 수 없는 전역 기록). 알림 패널(작은 창) 밖에서 열 수 있게 따로 둔다. 실패는 확인창 안에 그대로 보인다. */
export const KeepConfirmDialog = ({ item, onClose }: { item: ProtocolNotification; onClose: () => void }) => {
  const keep = useKeepProtocolAlert();
  return (
    <ConfirmDialog
      title="이상 유지" actionText="이상 유지"
      message={
        <>
          {keepConfirmMessage(item)}
          {keep.isError && <span role="alert" className="mt-2 block text-destructive-text">이상 유지를 저장하지 못했습니다. {errorText(keep.error)}</span>}
        </>
      }
      onCancel={onClose}
      onConfirm={() => { if (!keep.isPending) keep.mutate({ interfaceId: item.protocol.interfaceId, pathKey: item.protocol.pathKey }, { onSuccess: onClose }); }}
    />
  );
};

/**
 * 프로토콜 알림 명령(관리자, 처리 전만): [정상 프로토콜에 추가] = 그 인터페이스 프로토콜 상세를 경로를 더한 초안으로 연다(경로 없는 미등록 알림은 상세만 —
 * protocol-rules.md 5.3 "routes: [] 알림은 상세로 이동"), [이상 유지] = 확인창 뒤 기록만(판정은 이상 그대로, 같은 묶음 다시 알리지 않음).
 */
const ProtocolActions = ({ item, onDone, onKeep }: { item: ProtocolNotification; onDone: () => void; onKeep: (item: ProtocolNotification) => void }) => {
  const navigate = useNavigate();
  const { protocol } = item;
  if (protocol.resolution) return <Badge variant="secondary" data-protocol-resolution={protocol.resolution}>{protocolResolutionLabels[protocol.resolution]}</Badge>;
  const add = () => {
    const state: ProtocolAlertState = { returnTo: paths.detectionRuleProtocols,
      ...(protocol.channels ? { alertPath: { channels: protocol.channels, decisionId: protocol.decisionId } } : {}) };
    onDone();
    navigate(paths.detectionRuleProtocol(protocol.interfaceId), { state });
  };
  return (
    <>
      <Button variant="outline" size="sm" onClick={add}>정상 프로토콜에 추가</Button>
      <Button variant="outline" size="sm" onClick={() => onKeep(item)}>이상 유지</Button>
    </>
  );
};

const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));

const NotificationRow = ({ item, onRead, onNavigate, onKeep, busy }: {
  item: NotificationItem; onRead: (item: NotificationItem) => void; onNavigate: (item: NotificationItem) => void; onKeep: (item: ProtocolNotification) => void; busy: boolean;
}) => {
  const { headline, subject } = notificationText(item);
  const risk = notificationRisk(item);
  const { user } = useAuthSession();
  return (
    <li data-notification={item.key} className="grid gap-1.5 rounded-md border border-foreground bg-card px-3 py-2 text-sm">
      <span className="font-medium">{headline}</span>
      <span className="break-all text-muted-foreground">{subject}</span>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Badge variant="outline">{item.type === "protocol" ? "프로토콜 설정" : "이상 분석"}</Badge>
        {showsRisk(item) && (
          <Badge variant={risk.riskStatus === "rated" && risk.riskLevel ? riskLevelVariant[risk.riskLevel] : "outline"}>{riskText(risk)}</Badge>
        )}
        <span>{stampText(item.notifiedAt)}</span>
        {item.unread && <span className="font-medium text-foreground">읽지 않음</span>}
      </div>
      <div className="flex flex-wrap justify-end gap-2">
        {item.type === "protocol" && user?.userType === "admin" && <ProtocolActions item={item} onDone={() => onNavigate(item)} onKeep={onKeep} />}
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
const NotificationPage = ({ view, cursor, last, onMore, onRead, onNavigate, onKeep, busy }: {
  view: NotificationView; cursor: string | null; last: boolean; onMore: (cursor: string) => void;
  onRead: (item: NotificationItem) => void; onNavigate: (item: NotificationItem) => void; onKeep: (item: ProtocolNotification) => void; busy: boolean;
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
          {data.items.map((item) => <NotificationRow key={item.key} item={item} onRead={onRead} onNavigate={onNavigate} onKeep={onKeep} busy={busy} />)}
        </ul>
      )}
      {last && data.nextCursor && (
        <Button variant="ghost" size="sm" className="justify-self-center" onClick={() => onMore(data.nextCursor!)}>
          더 보기
        </Button>
      )}
      {last && data.window.truncated && data.window.truncatedBy === "events" && (
        <p className="text-xs text-muted-foreground">최근 판정 이벤트 {data.window.events.toLocaleString("ko-KR")}건 안의 알림만 보입니다.</p>
      )}
    </>
  );
};

/**
 * 알림 패널 본문(contracts/notifications.md): 서버가 만든 알림(사건 단위 + 묶이지 않은 판정)을 사용자 알림 설정으로 거른 목록.
 * 읽지 않음/전체 보기, 모두 읽음, 항목 읽음 처리, 이동(이동하면 그 항목 읽음). 미확인 수·읽음 상태는 서버 저장값만 쓴다.
 */
export const NotificationPanels = ({ onNavigate, onKeepRequest }: {
  onNavigate?: () => void;
  /** [이상 유지] 확인창을 패널 밖(알림 버튼)에서 열 때. 없으면 패널이 직접 연다. */
  onKeepRequest?: (item: ProtocolNotification) => void;
}) => {
  const [view, setView] = useState<NotificationView>("unread");
  const [keeping, setKeeping] = useState<ProtocolNotification | null>(null);
  const keep = onKeepRequest ?? setKeeping;
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
            onKeep={keep}
            busy={mark.isPending}
          />
        ))}
      </div>
      {keeping && <KeepConfirmDialog item={keeping} onClose={() => setKeeping(null)} />}
    </section>
  );
};
