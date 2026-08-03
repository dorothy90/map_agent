import * as http from "node:http";
import * as https from "node:https";

import type {
  ChatRequest,
  HealthResponse,
  PluginRelatedResponse,
  PluginReview,
  PluginReviewCreate,
  PluginReviewUpdate,
  PluginSearchRequest,
  PluginSearchResponse,
  PluginSessionHistory,
  PluginSessionSummary,
  PluginSettings,
  ReviewStatus,
  RestInit,
  RestResponse,
  RestTransport,
  SseEvent,
  StreamTransport,
} from "./types";

function obsidianRequest(request: Parameters<RestTransport>[0]): ReturnType<RestTransport> {
  const obsidian = require("obsidian") as { requestUrl: RestTransport };
  return obsidian.requestUrl(request);
}

export type ApiErrorCode =
  | "unauthorized"
  | "not_found"
  | "conflict"
  | "bad_gateway"
  | "http_error";

const ERROR_CODES: Partial<Record<number, ApiErrorCode>> = {
  401: "unauthorized",
  404: "not_found",
  409: "conflict",
  502: "bad_gateway",
};

export class ApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = ERROR_CODES[status] ?? "http_error";
  }
}

function pluginUrl(serverUrl: string, path: string): string {
  const base = serverUrl.replace(/\/+$/, "");
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${base}/api/wiki/plugin${suffix}`;
}

function errorMessage(response: RestResponse): string {
  if (
    response.json &&
    typeof response.json === "object" &&
    "detail" in response.json &&
    typeof response.json.detail === "string"
  ) {
    return response.json.detail;
  }
  return `Request failed with HTTP ${response.status}`;
}

function apiError(response: RestResponse): ApiError {
  return new ApiError(response.status, errorMessage(response));
}

export class YieldWikiApi {
  constructor(
    private readonly settings: PluginSettings,
    private readonly request: RestTransport = obsidianRequest,
    private readonly stream: StreamTransport = nodeSseStream,
  ) {}

  async rest<T>(path: string, init: RestInit = {}): Promise<T> {
    const body =
      init.body === undefined
        ? undefined
        : typeof init.body === "string"
          ? init.body
          : JSON.stringify(init.body);
    const headers: Record<string, string> = {
      ...init.headers,
      Authorization: `Bearer ${this.settings.apiToken}`,
    };
    if (body !== undefined && headers["Content-Type"] === undefined) {
      headers["Content-Type"] = "application/json";
    }
    const response = await this.request({
      url: pluginUrl(this.settings.serverUrl, path),
      method: init.method,
      headers,
      body,
      throw: false,
    });
    if (response.status < 200 || response.status >= 300) {
      throw apiError(response);
    }
    return response.json as T;
  }

  health(): Promise<HealthResponse> {
    return this.rest("/health");
  }

  listSessions(): Promise<PluginSessionSummary[]> {
    return this.rest("/sessions");
  }

  getSession(sessionId: string): Promise<PluginSessionHistory> {
    return this.rest(`/sessions/${encodeURIComponent(sessionId)}`);
  }

  search(request: PluginSearchRequest): Promise<PluginSearchResponse> {
    const params = new URLSearchParams({ q: request.query });
    if (request.product) params.set("product", request.product);
    if (request.failType) params.set("fail_type", request.failType);
    if (request.causeOper) params.set("cause_oper", request.causeOper);
    params.set("limit", String(request.limit ?? 20));
    return this.rest(`/search?${params.toString()}`);
  }

  related(notePath: string): Promise<PluginRelatedResponse> {
    return this.rest(`/related/${encodeURIComponent(notePath)}`);
  }

  listReviews(status: ReviewStatus = "pending"): Promise<PluginReview[]> {
    return this.rest(`/reviews?status=${encodeURIComponent(status)}`);
  }

  createReview(create: PluginReviewCreate): Promise<PluginReview> {
    return this.rest("/reviews", { method: "POST", body: create });
  }

  updateReview(
    reviewId: string,
    update: PluginReviewUpdate,
  ): Promise<PluginReview> {
    return this.rest(`/reviews/${encodeURIComponent(reviewId)}`, {
      method: "PATCH",
      body: update,
    });
  }

  streamChat(
    body: ChatRequest,
    onEvent: (event: SseEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    return this.stream(this.settings, body, onEvent, signal);
  }
}

function emitFrame(frame: string, emit: (event: SseEvent) => void): void {
  const data = frame
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  if (data) emit(JSON.parse(data) as SseEvent);
}

export function consumeSseBuffer(
  buffer: string,
  emit: (event: SseEvent) => void,
): string {
  const frames = buffer.split(/\r?\n\r?\n/);
  const remainder = frames.pop() ?? "";
  for (const frame of frames) emitFrame(frame, emit);
  return remainder;
}

function abortError(): Error {
  const error = new Error("Request aborted");
  error.name = "AbortError";
  return error;
}

function responseError(status: number, body: string): ApiError {
  let json: unknown;
  try {
    json = JSON.parse(body);
  } catch {
    json = undefined;
  }
  return apiError({ status, json });
}

export function nodeSseStream(
  settings: PluginSettings,
  body: ChatRequest,
  onEvent: (event: SseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  if (signal?.aborted) return Promise.reject(abortError());

  return new Promise<void>((resolve, reject) => {
    const url = new URL(pluginUrl(settings.serverUrl, "/chat"));
    const transport = url.protocol === "https:" ? https : http;
    let settled = false;

    const finish = (error?: unknown): void => {
      if (settled) return;
      settled = true;
      signal?.removeEventListener("abort", onAbort);
      if (error === undefined) resolve();
      else reject(error);
    };

    const request = transport.request(
      url,
      {
        method: "POST",
        headers: {
          Accept: "text/event-stream",
          Authorization: `Bearer ${settings.apiToken}`,
          "Content-Type": "application/json",
        },
      },
      (response) => {
        response.setEncoding("utf8");
        const status = response.statusCode ?? 0;
        if (status < 200 || status >= 300) {
          let errorBody = "";
          response.on("data", (chunk: string) => {
            errorBody += chunk;
          });
          response.on("end", () => finish(responseError(status, errorBody)));
          response.on("error", finish);
          return;
        }

        let buffer = "";
        response.on("data", (chunk: string) => {
          try {
            buffer = consumeSseBuffer(buffer + chunk, onEvent);
          } catch (error) {
            finish(error);
            request.destroy();
          }
        });
        response.on("end", () => {
          if (settled) return;
          try {
            if (buffer.trim()) emitFrame(buffer, onEvent);
            finish();
          } catch (error) {
            finish(error);
          }
        });
        response.on("error", finish);
      },
    );

    const onAbort = (): void => {
      request.destroy(abortError());
    };

    request.on("error", finish);
    signal?.addEventListener("abort", onAbort, { once: true });
    request.end(JSON.stringify(body));
  });
}

export type { RestTransport } from "./types";
