import { useEffect } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import { useAuthSession } from "../../services/auth/AuthSessionProvider";
import { DetectionTabs } from "../detection/components/DetectionTabs";
import { NotificationPanels } from "./NotificationPanels";
import {
  notificationFilters,
  notificationFilterHref,
  resolveNotificationFilter,
} from "./notificationPresentation";

export const NotificationsPage = () => {
  const { user } = useAuthSession();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const role = user?.userType ?? "user";
  const active = resolveNotificationFilter(params.get("filter"), role);
  useEffect(() => {
    if (params.get("filter") === active) return;
    setParams(notificationFilterHref(params, active).slice(1), {
      replace: true,
      state: location.state,
    });
  }, [active, params, setParams, location.state]);
  return (
    <section className="notifications-page">
      <h1>알림</h1>
      <DetectionTabs
        ariaLabel="알림 분류"
        activeId={active}
        items={notificationFilters
          .filter((item) => role === "admin" || item.id !== "tasks")
          .map((item) => ({
            ...item,
            to: notificationFilterHref(params, item.id),
          }))}
      />
      <NotificationPanels filter={active} />
    </section>
  );
};
