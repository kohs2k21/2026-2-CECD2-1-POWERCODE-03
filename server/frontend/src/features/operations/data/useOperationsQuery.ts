import { useAccountQuery } from "@/lib/query";
import { readOperationsOverview } from "./operations";

export const operationsQueryName = "operations-overview";
export const useOperationsQuery = () =>
  useAccountQuery({ name: operationsQueryName, read: readOperationsOverview });
