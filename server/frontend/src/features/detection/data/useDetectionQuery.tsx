import { createContext, useContext, type PropsWithChildren } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuthSession } from "../../../services/auth/AuthSessionProvider";
import { getDefaultGateway } from "./gateway";
import type { DetectionGateway } from "./types";
const GatewayContext = createContext<DetectionGateway | null>(null);
export const DetectionGatewayProvider = ({
  gateway,
  children,
}: PropsWithChildren<{ gateway: DetectionGateway }>) => (
  <GatewayContext.Provider value={gateway}>{children}</GatewayContext.Provider>
);
export const useDetectionGateway = () => {
  const injected = useContext(GatewayContext);
  return {
    async read(signal?: AbortSignal) {
      return (injected ?? (await getDefaultGateway())).read(signal);
    },
    async request(...args: Parameters<DetectionGateway["request"]>) {
      return (injected ?? (await getDefaultGateway())).request(...args);
    },
  } satisfies DetectionGateway;
};
export const detectionQueryKey = (userId: string) =>
  ["detection-workbench", userId] as const;
export const useDetectionQuery = () => {
  const { user } = useAuthSession();
  const gateway = useDetectionGateway();
  return useQuery({
    queryKey: detectionQueryKey(user?.id ?? "anonymous"),
    queryFn: ({ signal }) => gateway.read(signal),
    enabled: user?.userType === "admin",
    staleTime: 30_000,
    retry: false,
  });
};
