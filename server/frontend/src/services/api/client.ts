const apiBaseUrl =
  (import.meta as ImportMeta & { env?: { VITE_API_BASE_URL?: string } }).env
    ?.VITE_API_BASE_URL ?? "";

type RequestOptions = RequestInit & {
  params?: Record<string, string | number | boolean | undefined>;
  includeAuth?: boolean;
};

const buildUrl = (path: string, params?: RequestOptions["params"]) => {
  const url = new URL(path, apiBaseUrl || window.location.origin);

  Object.entries(params ?? {}).forEach(([key, value]) => {
    if (value !== undefined) {
      url.searchParams.set(key, String(value));
    }
  });

  return apiBaseUrl ? url.toString() : url.pathname + url.search;
};

const getRequestHeaders = (
  headers: HeadersInit | undefined,
  includeAuth: boolean,
): Headers => {
  const requestHeaders = new Headers(headers);

  if (includeAuth) {
    const token = window.localStorage.getItem("token");
    if (token) {
      requestHeaders.set("Authorization", "Bearer " + token);
    }
  }

  return requestHeaders;
};

const getErrorMessage = async (
  response: Response,
  method: string,
  path: string,
): Promise<Error> => {
  let message = method + " " + path + " failed: " + response.status;

  try {
    const payload: unknown = await response.json();
    if (
      payload &&
      typeof payload === "object" &&
      "message" in payload &&
      typeof payload.message === "string"
    ) {
      message = payload.message;
    }
  } catch {
    // Keep the status-based message when the server did not return JSON.
  }

  return new Error(message);
};

const request = async <T>(
  method: string,
  path: string,
  options: RequestOptions = {},
  body?: unknown,
): Promise<T> => {
  const {
    params,
    includeAuth = true,
    headers,
    ...requestInit
  } = options;

  const requestHeaders = getRequestHeaders(headers, includeAuth);
  const fetchInit: RequestInit = {
    ...requestInit,
    method,
    headers: requestHeaders,
  };

  if (body !== undefined) {
    requestHeaders.set("Content-Type", "application/json");
    fetchInit.body = JSON.stringify(body);
  }

  const response = await fetch(buildUrl(path, params), fetchInit);
  if (!response.ok) {
    throw await getErrorMessage(response, method, path);
  }

  return response.json() as Promise<T>;
};

export const httpClient = {
  get<T>(path: string, options: RequestOptions = {}): Promise<T> {
    return request<T>("GET", path, options);
  },

  post<T>(
    path: string,
    body: unknown,
    options: RequestOptions = {},
  ): Promise<T> {
    return request<T>("POST", path, options, body);
  },

  patch<T>(
    path: string,
    body: unknown,
    options: RequestOptions = {},
  ): Promise<T> {
    return request<T>("PATCH", path, options, body);
  },
};
