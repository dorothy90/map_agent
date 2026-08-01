export interface PluginSettings {
  serverUrl: string;
  apiToken: string;
}

export interface ChatRequest {
  query: string;
  session_id: string;
  resume_value?: string | Record<string, unknown> | null;
  user_id?: string;
  current_note_id?: string | null;
}

export interface SseEvent {
  type: string;
  [key: string]: unknown;
}

export interface RestInit {
  method?: string;
  headers?: Record<string, string>;
  body?: unknown;
}

export interface RestRequest {
  url: string;
  method?: string;
  headers: Record<string, string>;
  body?: string;
  throw: false;
}

export interface RestResponse {
  status: number;
  json?: unknown;
  text?: string;
}

export type RestTransport = (request: RestRequest) => Promise<RestResponse>;

export type StreamTransport = (
  settings: PluginSettings,
  body: ChatRequest,
  onEvent: (event: SseEvent) => void,
  signal?: AbortSignal,
) => Promise<void>;
