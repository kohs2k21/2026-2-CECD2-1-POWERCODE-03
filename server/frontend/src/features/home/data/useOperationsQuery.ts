import { useQuery } from "@tanstack/react-query";
import { useAuthSession } from "../../../services/auth/AuthSessionProvider";
import { readOperationsOverview } from "./operations";
export const operationsQueryKey = (userId: string) => ["operations-overview", userId] as const;
export const useOperationsQuery = () => {
  const { user } = useAuthSession();
  return useQuery({
    queryKey: operationsQueryKey(user?.id ?? "anonymous"),
    queryFn: ({ signal }) => readOperationsOverview(signal),
    enabled: Boolean(user),
    staleTime: 30_000,
    retry: false,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: false,
  });
};
