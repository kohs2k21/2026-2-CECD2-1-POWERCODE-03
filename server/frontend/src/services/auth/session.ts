const tokenStorageKey = "token";

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
