/**
 * Typed access to the EOD Billing API.
 *
 * The backend returns a structured body for every failure, so this layer keeps
 * that structure rather than collapsing it to a string. The screens show the
 * server's own hint and per-row errors, not a generic "request failed".
 */

import type {
  AnalyticsResponse,
  ClinicDays,
  FullReportResponse,
  Health,
  Narrative,
  ReconciliationResponse,
  RejectedRow,
} from "./types";

const BASE_URL = (
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000"
).replace(/\/$/, "");

const PREFIX = "/api/v1";

const LOCAL_HOSTS = ["localhost", "127.0.0.1", "[::1]", ""];

/**
 * True when a deployed build is pointing at the visitor's own machine.
 *
 * Vite substitutes VITE_API_BASE_URL at build time, so forgetting to set it in
 * the host's project settings compiles the localhost default into the bundle.
 * The build succeeds and the deploy reports green, then every visitor gets a
 * connection failure against a machine that isn't ours. Worth detecting: the
 * honest message names a deployment setting, the generic one tells a reviewer
 * to go start a server.
 */
function isMisconfiguredDeployment(): boolean {
  if (typeof window === "undefined") return false;
  if (LOCAL_HOSTS.includes(window.location.hostname)) return false;
  return /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:|\/|$)/.test(BASE_URL);
}

/** An error carrying whatever the API told us about it. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly hint?: string;
  readonly rows: RejectedRow[];

  constructor(
    status: number,
    code: string,
    message: string,
    hint?: string,
    rows: RejectedRow[] = [],
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.hint = hint;
    this.rows = rows;
  }

  /** The day has not been ingested — an empty state, not a fault. */
  get isNotFound(): boolean {
    return this.status === 404;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${PREFIX}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    // Two different failures land here and they need different advice.
    if (isMisconfiguredDeployment()) {
      throw new ApiError(
        0,
        "api_url_not_configured",
        "This deployment was built without an API address, so it is calling localhost.",
        "Set VITE_API_BASE_URL to the backend's public URL and redeploy — Vite reads it at build time, so a restart alone will not pick it up.",
      );
    }
    // A dead backend is the single most likely local failure; name it plainly
    // rather than surfacing the browser's opaque "Failed to fetch".
    throw new ApiError(
      0,
      "network_error",
      `Could not reach the API at ${BASE_URL}`,
      "Start the backend, or check VITE_API_BASE_URL.",
    );
  }

  if (response.status === 204) return undefined as T;

  const body = await response.json().catch(() => null);

  if (!response.ok) {
    throw new ApiError(
      response.status,
      body?.error ?? "http_error",
      body?.message ?? `Request failed with status ${response.status}`,
      body?.hint,
      body?.errors ?? [],
    );
  }

  return body as T;
}

export const api = {
  health: () => request<Health>("/health"),

  days: (clinicId: string) => request<ClinicDays>(`/clinics/${clinicId}/days`),

  clinics: () => request<{ clinics: Array<{ clinic_id: string; name: string }> }>("/clinics"),

  reconciliation: (clinicId: string, date: string) =>
    request<ReconciliationResponse>(`/reports/${clinicId}/${date}/reconciliation`),

  analytics: (clinicId: string, date: string, limit = 5) =>
    request<AnalyticsResponse>(`/reports/${clinicId}/${date}/analytics?limit=${limit}`),

  fullReport: (clinicId: string, date: string) =>
    request<FullReportResponse>(`/reports/${clinicId}/${date}`),

  /** 404 means none has been generated yet, which the screen treats as empty. */
  cachedNarrative: (clinicId: string, date: string) =>
    request<Narrative>(`/reports/${clinicId}/${date}/narrative`),

  generateNarrative: (clinicId: string, date: string, force = false) =>
    request<Narrative>(
      `/reports/${clinicId}/${date}/narrative${force ? "?force=true" : ""}`,
      { method: "POST" },
    ),
};

export { BASE_URL };
