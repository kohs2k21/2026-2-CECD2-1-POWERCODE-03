import { useContext, useEffect, useState } from "react";
import { IconBell } from "@tabler/icons-react";
import { Link, useLocation } from "react-router-dom";
import { paths } from "@/app/routes";
import { Button } from "@/components/ui/button";
import { useIsMobile } from "@/hooks/useIsMobile";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { CountBadge } from "./CountBadge";
import { badgeText, bellLabel } from "./notificationLabels";
import { KeepConfirmDialog, NotificationPanels } from "./NotificationPanels";
import type { ProtocolNotification } from "./notificationsApi";
import { useUnreadCount } from "./useNotifications";
import { NotificationLiveContext } from "./NotificationLiveContext";
import { NotificationLiveProvider } from "./NotificationLiveProvider";

/**
 * 상단 알림 버튼(와이어프레임 #93 bell): 배지 = 서버 미확인 수(99 초과 99+), 누르면 알림 패널(PC). 알림 페이지 `/notifications`는 휴대폰 첫 화면(navigation.md 휴대폰 폭).
 * 배지는 화면 이동·패널 열기·새 판정 이벤트(SSE, 10초에 한 번까지)·화면이 읽는 작업 조회에서 작업이 끝났을 때 다시 조회한다.
 * 휴대폰 폭(useIsMobile)에서는 작은 창 대신 알림 페이지(`/notifications`) 링크다(배지·이름 같음, 그 페이지에서는 aria-current=page).
 */
export const NotificationsBell = ({ triggerClassName }: { triggerClassName?: string }) => {
  const live = useContext(NotificationLiveContext);
  // 분리 렌더·기존 화면 시험도 지원하되, 실제 AppLayout에서는 공통 소유자만 사용한다.
  return live ? <NotificationsBellContent triggerClassName={triggerClassName} />
    : <NotificationLiveProvider><NotificationsBellContent triggerClassName={triggerClassName} /></NotificationLiveProvider>;
};

const NotificationsBellContent = ({ triggerClassName }: { triggerClassName?: string }) => {
  const [open, setOpen] = useState(false);
  // [이상 유지] 확인창은 작은 창 밖에 둔다(확인창을 누르면 작은 창이 닫히므로 그 안에 두면 확인창도 사라진다).
  const [keeping, setKeeping] = useState<ProtocolNotification | null>(null);
  const location = useLocation();
  const unread = useUnreadCount();
  useEffect(() => setOpen(false), [location.key]);
  const close = () => setOpen(false);
  const count = unread.data?.unreadCount;
  const label = unread.isError ? "알림 · 읽지 않은 수 확인 불가" : bellLabel(count);
  const mobile = useIsMobile();
  const badge = (Boolean(count) || unread.isError) && (
    <CountBadge data-notification-badge="" muted={unread.isError}>
      {unread.isError ? "?" : badgeText(count!)}
    </CountBadge>
  );
  if (mobile)
    return (
      <Button asChild variant="ghost" size="icon" className={cn("relative", triggerClassName)}>
        <Link to={paths.notifications} aria-label={label} title={label} aria-current={location.pathname === paths.notifications ? "page" : undefined}>
          <IconBell aria-hidden="true" />
          {badge}
        </Link>
      </Button>
    );
  return (
    <>
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) void unread.refetch();
      }}
    >
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={label} title={label} className={cn("relative", triggerClassName)}>
          <IconBell aria-hidden="true" />
          {badge}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="grid w-96 max-w-[calc(100vw-2rem)] gap-3" aria-label="알림">
        <h2 className="text-section-title">알림</h2>
        <div className="max-h-[60vh] overflow-y-auto pr-1">
          {open && <NotificationPanels onNavigate={close} onKeepRequest={(item) => { setKeeping(item); close(); }} />}
        </div>
      </PopoverContent>
    </Popover>
    {keeping && <KeepConfirmDialog item={keeping} onClose={() => setKeeping(null)} />}
    </>
  );
};
