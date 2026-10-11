const tokenStorageKey = "token";
const expiredListeners = new Set<() => void>();
export const subscribeSessionExpiry = (listener: () => void) => {
  expiredListeners.add(listener);
  return () => {
    expiredListeners.delete(listener);
  };
};
export const expireSession = () => {
  clearStoredToken();
  expiredListeners.forEach((listener) => listener());
};

export const getStoredToken = (): string | null => {
  if (typeof window === "undefined") {
    return null;
  }

  return window.localStorage.getItem(tokenStorageKey);
};

export const storeToken = (token: string): void => {
  window.localStorage.setItem(tokenStorageKey, token);
};

export const clearStoredToken = (): void => {
  window.localStorage.removeItem(tokenStorageKey);
};

export const subscribeTokenStorage = (listener: () => void) => {
  const onStorage = (event: StorageEvent) => {
    if (
      (event.key === tokenStorageKey || event.key === null) &&
      (!event.storageArea || event.storageArea === window.localStorage)
    )
      listener();
  };
  window.addEventListener("storage", onStorage);
  return () => window.removeEventListener("storage", onStorage);
};
