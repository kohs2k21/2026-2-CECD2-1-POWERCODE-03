import { createContext, useContext, type PropsWithChildren } from "react";
import {
  useRealtimeAnomalies,
  type RealtimeAnomaliesResult,
} from "./hooks/useRealtimeAnomalies";

const RealtimeContext = createContext<RealtimeAnomaliesResult | null>(null);

export const RealtimeAnomalyProvider = ({ children }: PropsWithChildren) => {
  const realtime = useRealtimeAnomalies();
  return (
    <RealtimeContext.Provider value={realtime}>
      {children}
    </RealtimeContext.Provider>
  );
};

export const useSharedRealtimeAnomalies = () => {
  const realtime = useContext(RealtimeContext);
  if (!realtime) throw new Error("RealtimeAnomalyProvider is required");
  return realtime;
};
