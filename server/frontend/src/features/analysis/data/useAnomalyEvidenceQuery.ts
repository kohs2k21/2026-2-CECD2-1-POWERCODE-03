import { useQuery } from "@tanstack/react-query";
import { useAuthSession } from "../../../services/auth/AuthSessionProvider";
import { readAnomalyEvidence } from "./anomalyEvidence";
export const anomalyEvidenceQueryKey = (userId: string, eventId: string) =>
  ["anomaly-evidence", userId, eventId] as const;
export const useAnomalyEvidenceQuery = (eventId: string) => {
  const { user } = useAuthSession();
  return useQuery({
    queryKey: anomalyEvidenceQueryKey(user?.id ?? "anonymous", eventId),
    queryFn: ({ signal }) => readAnomalyEvidence(eventId, signal),
    enabled: Boolean(user && eventId),
    retry: false,
    staleTime: 30000,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchInterval: false,
    refetchOnMount: false,
  });
};
