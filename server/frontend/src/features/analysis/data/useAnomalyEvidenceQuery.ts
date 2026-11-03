import { useAccountQuery } from "@/lib/query";
import { readAnomalyEvidence } from "./anomalyEvidence";

export const anomalyEvidenceQueryName = "anomaly-evidence";
export const useAnomalyEvidenceQuery = (eventId: string) =>
  useAccountQuery({
    name: anomalyEvidenceQueryName,
    params: [eventId],
    read: (signal) => readAnomalyEvidence(eventId, signal),
    enabled: Boolean(eventId),
  });
