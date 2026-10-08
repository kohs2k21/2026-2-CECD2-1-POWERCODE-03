import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type PropsWithChildren,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { AuthSession, AuthUser } from "../../types/auth";
import { fetchCurrentUser } from "../api/auth.api";
import {
  clearStoredToken,
  getStoredToken,
  storeToken,
  subscribeSessionExpiry,
  subscribeTokenStorage,
} from "./session";
import { useDraftStore } from "../../stores/draftStore";
import { notify } from "../../lib/notify";

type SessionState = {
  user: AuthUser | null;
  checking: boolean;
  error: Error | null;
  hasToken: boolean;
  explicitLogout: boolean;
  retry: () => void;
  login: (session: AuthSession) => void;
  logout: () => void;
};
const SessionContext = createContext<SessionState | null>(null);
export const AuthSessionProvider = ({ children }: PropsWithChildren) => {
  const cache = useQueryClient();
  const [session, setSession] = useState(() => ({
    token: getStoredToken(),
    generation: 0,
  }));
  const sessionRef = useRef(session);
  const hasToken = Boolean(session.token);
  const storageMatches = session.token === getStoredToken();
  const [explicitLogout, setExplicitLogout] = useState(false);
  const currentUser = useQuery({
    queryKey: ["current-user", session.generation],
    queryFn: ({ signal }) => fetchCurrentUser(signal),
    enabled: hasToken && storageMatches,
    staleTime: 60_000,
    retry: false,
  });
  const changeSession = (
    token: string | null,
    knownUser?: AuthUser,
    explicit = false,
  ) => {
    const next = { token, generation: sessionRef.current.generation + 1 };
    sessionRef.current = next;
    cache.clear();
    notify.clear();
    useDraftStore.getState().reset();
    setExplicitLogout(explicit);
    if (knownUser)
      cache.setQueryData(["current-user", next.generation], knownUser);
    setSession(next);
  };
  const logout = () => {
    clearStoredToken();
    changeSession(null, undefined, true);
  };
  useEffect(() => {
    const syncStorage = () => {
      const token = getStoredToken();
      if (token !== sessionRef.current.token) changeSession(token);
    };
    const stopExpiry = subscribeSessionExpiry(() => changeSession(null));
    const stopStorage = subscribeTokenStorage(syncStorage);
    // Also cover a storage change between the initial render and subscription.
    syncStorage();
    return () => {
      stopExpiry();
      stopStorage();
    };
  }, [cache]);
  const login = (session: AuthSession) => {
    storeToken(session.token);
    changeSession(session.token, session.user);
  };
  return (
    <SessionContext.Provider
      value={{
        user: hasToken && storageMatches ? (currentUser.data ?? null) : null,
        checking: hasToken && (!storageMatches || currentUser.isPending),
        error: hasToken && storageMatches ? currentUser.error : null,
        hasToken,
        explicitLogout,
        login,
        logout,
        retry: () => {
          void currentUser.refetch();
        },
      }}
    >
      {children}
    </SessionContext.Provider>
  );
};
export const useAuthSession = () => {
  const value = useContext(SessionContext);
  if (!value) throw new Error("AuthSessionProvider is required");
  return value;
};
