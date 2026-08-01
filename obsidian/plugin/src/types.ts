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

export interface HealthResponse {
  status: string;
  dependencies?: Record<string, string>;
}

export interface CitationData {
  doc_id: string;
  label: string;
  source_path?: string | null;
  download_url?: string;
}

export interface PluginArtifact {
  artifact_id: string;
  artifact_type: "html" | "image" | "markdown" | "pptx";
  mime?: string;
  title?: string;
  agent?: string;
  data?: string;
}

export interface HistoryMessage {
  role: "user" | "assistant";
  agent?: string;
  content?: string;
  artifacts?: PluginArtifact[];
  citations?: CitationData[];
  suggestion?: string;
  timestamp: string;
}

export interface PluginSessionSummary {
  session_id: string;
  last_query: string;
  turn_count: number;
  updated_at: string;
}

export interface PluginSessionHistory {
  session_id: string;
  turns: HistoryMessage[];
}

export interface PluginEvidence {
  doc_id: string;
  content?: string;
  cause?: string;
  action?: string;
  comment?: string;
  source_file?: string;
  date?: string;
  score: number;
  source_path?: string | null;
  download_url?: string;
}

export interface PluginSearchResult {
  concept_id: string | null;
  concept_path: string | null;
  concept_status: "materialized" | "source_only";
  product: string;
  fail_type: string;
  cause_oper: string;
  retrieval_mode: "hybrid" | "bm25_fallback";
  score: number;
  evidence: PluginEvidence[];
}

export interface PluginSearchResponse {
  query: string;
  retrieval_mode: "hybrid" | "bm25_fallback";
  results: PluginSearchResult[];
}

export interface PluginSearchRequest {
  query: string;
  product?: string;
  failType?: string;
  causeOper?: string;
  limit?: number;
}

export type ReviewStatus = "pending" | "approved" | "rejected" | "resolved";

export interface PluginReviewHistory {
  changed_at: string;
  from_status: ReviewStatus;
  to_status: ReviewStatus;
  reviewer: string;
  comment: string;
}

export interface PluginReview {
  id: string;
  review_type: string;
  status: ReviewStatus;
  target_concept_id: string;
  version: number;
  created: string;
  updated: string;
  body_markdown: string;
  metadata: Record<string, unknown>;
  history: PluginReviewHistory[];
}

export interface PluginReviewUpdate {
  status: "approved" | "rejected";
  reviewer: string;
  comment: string;
  expected_version: number;
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
