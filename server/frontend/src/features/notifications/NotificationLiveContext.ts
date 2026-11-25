import { createContext } from "react";
import type { NotificationLiveState } from "./notificationLive";

export type NotificationLiveValue = {
  state: NotificationLiveState;
  refresh(): void;
  reset(): void;
  dismiss(): void;
};
export const NotificationLiveContext = createContext<NotificationLiveValue | null>(null);
