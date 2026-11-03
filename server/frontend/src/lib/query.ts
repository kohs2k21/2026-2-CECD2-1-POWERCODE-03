import {
  QueryClient,
  useQuery,
  type QueryKey,
  type UseQueryResult,
} from "@tanstack/react-query";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import { getStoredToken } from "@/services/auth/session";

/**
 * 서버 조회 공통 규칙(navigation.md 상태 책임·ui.md 조회 상태)
 * - 조회 key에 계정 ID를 넣는다(계정별 캐시). 계정 변경 시 AuthSessionProvider가 캐시 전체를 비운다.
 * - queryFn은 AbortSignal을 받는다. 응답이 돌아왔을 때 토큰이 바뀌었으면 결과를 버린다(AbortError).
 * - 자동 재시도·창 포커스·재연결·주기 재조회 없음(404 포함 재시도 없음).
 * - 주기 조회는 진행 중 작업만 5초(RUNNING_POLL_MS). 끝나면 멈춘다.
 */
export const RUNNING_POLL_MS = 5_000;

export const queryDefaults = {
  retry: false,
  refetchOnWindowFocus: false,
  refetchOnReconnect: false,
  refetchInterval: false,
} as const;

export const createAppQueryClient = () =>
  new QueryClient({ defaultOptions: { queries: { ...queryDefaults, staleTime: 30_000 } } });

export const anonymousAccount = "anonymous";

/** 계정별 조회 key: [이름, 계정 ID, ...세부]. */
export const accountQueryKey = (name: string, userId: string | null | undefined, ...parts: unknown[]) =>
  [name, userId ?? anonymousAccount, ...parts] as const;

export class SessionChangedError extends DOMException {
  constructor() {
    super("Session changed", "AbortError");
  }
}

/** 요청 시작 시점의 토큰과 응답 시점의 토큰이 다르면 결과를 쓰지 않는다. */
export const sessionBound = async <T>(
  read: (signal?: AbortSignal) => Promise<T>,
  signal?: AbortSignal,
): Promise<T> => {
  const token = getStoredToken();
  const value = await read(signal);
  if (signal?.aborted || token !== getStoredToken()) throw new SessionChangedError();
  return value;
};

/** 진행 중일 때만 5초 주기 조회, 그 외 주기 조회 없음. */
export const pollWhileRunning =
  <T>(isRunning: (data: T | undefined) => boolean) =>
  (query: { state: { data: T | undefined } }) =>
    isRunning(query.state.data) ? RUNNING_POLL_MS : false;

type AccountQueryOptions<T> = {
  name: string;
  params?: unknown[];
  read: (signal?: AbortSignal) => Promise<T>;
  enabled?: boolean;
  adminOnly?: boolean;
  staleTime?: number;
  isRunning?: (data: T | undefined) => boolean;
};

/** 공통 Query 훅: 계정별 key·AbortSignal·세션 검증·자동 재조회 없음·진행 중만 5초 폴링. */
export const useAccountQuery = <T>({
  name,
  params = [],
  read,
  enabled = true,
  adminOnly = false,
  staleTime = 30_000,
  isRunning,
}: AccountQueryOptions<T>): UseQueryResult<T> & { queryKey: QueryKey } => {
  const { user } = useAuthSession();
  const queryKey = accountQueryKey(name, user?.id, ...params);
  const allowed = Boolean(user) && (!adminOnly || user?.userType === "admin");
  const result = useQuery({
    queryKey,
    queryFn: ({ signal }) => sessionBound(read, signal),
    enabled: enabled && allowed,
    staleTime,
    ...queryDefaults,
    refetchInterval: isRunning ? pollWhileRunning(isRunning) : false,
  });
  return Object.assign(result, { queryKey });
};
