import { useEffect, useState } from "react";
import { IconBell } from "@tabler/icons-react";
import { useLocation } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { NotificationPanels } from "./NotificationPanels";

/** 상단 알림 패널(와이어프레임 #93 bell). 별도 알림 페이지는 폐지(/notifications → /analysis). */
export const NotificationsBell = ({ triggerClassName }: { triggerClassName?: string }) => {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setOpen(false), [location.key]);
  const close = () => setOpen(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="알림" className={triggerClassName}>
          <IconBell aria-hidden="true" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="grid w-96 max-w-[calc(100vw-2rem)] gap-3" aria-label="알림 목록">
        <h2 className="text-section-title">알림</h2>
        <div className="max-h-[60vh] overflow-y-auto">
          <NotificationPanels limit={5} onNavigate={close} />
        </div>
      </PopoverContent>
    </Popover>
  );
};
