import createClient, { type Middleware } from "openapi-fetch";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];

const TOKEN_KEY = "novara.token";

/**
 * The access token lives in localStorage so a reload keeps the session. Anything that can run
 * script on this origin could read it, which is why the app never renders HTML from data
 * (React escapes everything, no dangerouslySetInnerHTML) and production serves a strict CSP
 * (infrastructure/deployment/nginx.conf). Logout revokes the session server-side.
 */
export const tokenStore = {
  get: (): string | null => {
    try {
      return localStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  },
  set: (token: string) => {
    try {
      localStorage.setItem(TOKEN_KEY, token);
    } catch {
      /* storage unavailable: the session lasts until reload */
    }
  },
  clear: () => {
    try {
      localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* ignore */
    }
  },
};

export const UNAUTHORIZED_EVENT = "novara:unauthorized";

const auth: Middleware = {
  onRequest({ request }) {
    const token = tokenStore.get();
    if (token) request.headers.set("Authorization", `Bearer ${token}`);
    return request;
  },
  onResponse({ response }) {
    if (response.status === 401 && !response.url.includes("/auth/login")) {
      tokenStore.clear();
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    }
    return response;
  },
};

export const api = createClient<paths>({ baseUrl: "" });
api.use(auth);

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly fields: Record<string, string> = {},
    public readonly rows: { row: number; errors: string[] }[] = [],
  ) {
    super(message);
  }
}

type Detail = string | { loc?: (string | number)[]; msg?: string }[] | { errors?: { row: number; errors: string[] }[] };

/** Turn FastAPI's error bodies (plain, validation or import errors) into one ApiError. */
export function toApiError(status: number, body: unknown): ApiError {
  const detail = (body as { detail?: Detail } | undefined)?.detail;
  if (typeof detail === "string") return new ApiError(status, detail);
  if (Array.isArray(detail)) {
    const fields: Record<string, string> = {};
    for (const d of detail) {
      const field = (d.loc ?? []).filter((p) => p !== "body").join(".");
      if (field && d.msg) fields[field] = d.msg.replace(/^Value error, /, "");
    }
    return new ApiError(status, Object.values(fields)[0] ?? "invalid input", fields);
  }
  if (detail && typeof detail === "object" && "errors" in detail && Array.isArray(detail.errors)) {
    return new ApiError(status, "the file has errors", {}, detail.errors);
  }
  return new ApiError(status, status >= 500 ? "server error" : `request failed (${status})`);
}

/** Return data or throw ApiError - so react-query sees failures as errors. */
export async function unwrap<T>(promise: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await promise;
  if (!response.ok || error !== undefined) throw toApiError(response.status, error);
  return data as T;
}
