import { createContext, useContext, type PropsWithChildren } from "react";
import { useDetectionQuery } from "../detection/data/useDetectionQuery";

const JobsContext = createContext<ReturnType<typeof useDetectionQuery> | null>(
  null,
);

// One observer owns task polling for both the popup and the full list.
export const NotificationsProvider = ({ children }: PropsWithChildren) => {
  const query = useDetectionQuery({ pollWhilePending: true });
  return <JobsContext.Provider value={query}>{children}</JobsContext.Provider>;
};

export const useNotificationJobs = () => {
  const query = useContext(JobsContext);
  if (!query) throw new Error("NotificationsProvider is required");
  return query;
};
