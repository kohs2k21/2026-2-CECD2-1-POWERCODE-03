import { useEffect, useState } from "react";
import { IconBell } from "@tabler/icons-react";
import { Link, useLocation } from "react-router-dom";
import { Button } from "../../components/ui/button";
import { DialogFooter } from "../../components/ui/dialog";
import { Modal } from "../../components/ui/Modal";
import { NotificationPanels } from "./NotificationPanels";

export const NotificationsBell = () => {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setOpen(false), [location.key]);
  const close = () => setOpen(false);
  return (
    <Modal
      isOpen={open}
      onOpenChange={setOpen}
      title="알림"
      size="sm"
      trigger={
        <Button
          variant="ghost"
          size="icon"
          className="top-nav-icon-btn"
          aria-label="알림"
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-current={
            location.pathname === "/notifications" ? "page" : undefined
          }
        >
          <IconBell size={20} aria-hidden="true" />
        </Button>
      }
    >
      <div className="notification-popup-list">
        <NotificationPanels limit={5} onNavigate={close} />
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={close}>
          닫기
        </Button>
        <Button asChild>
          <Link to="/notifications?filter=all" onClick={close}>
            전체 보기
          </Link>
        </Button>
      </DialogFooter>
    </Modal>
  );
};
