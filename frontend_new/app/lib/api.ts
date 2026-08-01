/**
 * The single API client (CLAUDE.md §8: no scattered fetch calls).
 * All business logic lives behind /api/v1 (R4) — this file only transports.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000/api/v1";

export type ApiError = {
  code: string;
  message: string;
  details?: Record<string, unknown>;
  request_id?: string;
};

export class ApiException extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: Record<string, unknown>;

  constructor(status: number, error: ApiError) {
    super(error.message);
    this.name = "ApiException";
    this.code = error.code;
    this.status = status;
    this.details = error.details ?? {};
  }
}

const TOKEN_KEY = "liger.access_token";
const REFRESH_KEY = "liger.refresh_token";

export const tokens = {
  get access() {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem(TOKEN_KEY);
  },
  get refresh() {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem(REFRESH_KEY);
  },
  set(access: string, refresh: string) {
    window.localStorage.setItem(TOKEN_KEY, access);
    window.localStorage.setItem(REFRESH_KEY, refresh);
  },
  clear() {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(REFRESH_KEY);
  },
};

type RequestOptions = {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  /** R6 — required on money-touching mutations. */
  idempotencyKey?: string;
  raw?: string;
  contentType?: string;
  signal?: AbortSignal;
};

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {};
  const access = tokens.access;
  if (access) headers.Authorization = `Bearer ${access}`;
  if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;

  let body: BodyInit | undefined;
  if (options.raw !== undefined) {
    body = options.raw;
    headers["Content-Type"] = options.contentType ?? "text/csv";
  } else if (options.body !== undefined) {
    body = JSON.stringify(options.body);
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(`${API_BASE}${path}`, {
    method: options.method ?? "GET",
    headers,
    body,
    signal: options.signal,
  });

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const payload = text ? JSON.parse(text) : {};

  if (!response.ok) {
    const error: ApiError = payload.error ?? {
      code: "INTERNAL_ERROR",
      message: `Request failed (${response.status})`,
    };
    throw new ApiException(response.status, error);
  }
  return payload as T;
}

/** Fresh key per submit attempt so a retry replays rather than duplicates (R6). */
export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>(path, { signal }),
  post: <T>(path: string, body?: unknown, idempotencyKey?: string) =>
    request<T>(path, { method: "POST", body, idempotencyKey }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  postRaw: <T>(path: string, raw: string, contentType = "text/csv") =>
    request<T>(path, { method: "POST", raw, contentType }),
};
