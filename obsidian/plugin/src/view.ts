import { App, ItemView, WorkspaceLeaf } from "obsidian";

import { ApiError, YieldWikiApi } from "./api";
import type { YieldWikiSettings } from "./settings";
import type {
  ChatRequest,
  HealthResponse,
  PluginReview,
  PluginReviewUpdate,
  PluginSearchRequest,
  PluginSearchResponse,
  PluginSearchResult,
  PluginSessionHistory,
  PluginSessionSummary,
  ReviewStatus,
  SseEvent,
} from "./types";

export const YIELD_WIKI_VIEW_TYPE = "yield-wiki-view";

type ActiveTab = "chat" | "search" | "review";
type ConnectionState = "checking" | "connected" | "unauthorized" | "offline";
type ResumeValue = string | Record<string, unknown>;

interface ChatMessageEntry {
  kind: "user" | "assistant";
  text: string;
  agent?: string;
  streaming?: boolean;
  citations?: CitationView[];
}

interface ChatArtifactEntry {
  kind: "artifact";
  title: string;
  artifactType: string;
  agent: string;
  data: string;
}

interface ChatSuggestionEntry {
  kind: "suggestion";
  text: string;
}

interface ChatErrorEntry {
  kind: "error";
  text: string;
}

interface ChatInterruptEntry {
  kind: "interrupt";
  message: string;
  interruptType: string;
  param: string;
  options: unknown[];
  fields: Record<string, unknown>[];
  answered?: string;
}

type ChatEntry =
  | ChatMessageEntry
  | ChatArtifactEntry
  | ChatSuggestionEntry
  | ChatErrorEntry
  | ChatInterruptEntry;

function isChatMessageEntry(entry: ChatEntry): entry is ChatMessageEntry {
  return entry.kind === "user" || entry.kind === "assistant";
}

interface CitationView {
  docId: string;
  label: string;
  sourcePath?: string;
  downloadUrl?: string;
}

export interface YieldWikiApiClient {
  health(): Promise<HealthResponse>;
  listSessions(): Promise<PluginSessionSummary[]>;
  getSession(sessionId: string): Promise<PluginSessionHistory>;
  search(request: PluginSearchRequest): Promise<PluginSearchResponse>;
  listReviews(status?: ReviewStatus): Promise<PluginReview[]>;
  updateReview(reviewId: string, update: PluginReviewUpdate): Promise<PluginReview>;
  streamChat(
    body: ChatRequest,
    onEvent: (event: SseEvent) => void,
    signal?: AbortSignal,
  ): Promise<void>;
}

export interface YieldWikiViewPlugin {
  app: App;
  settings: YieldWikiSettings;
}

function createElement<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function createButton(
  label: string,
  className: string,
  onClick: () => void,
): HTMLButtonElement {
  const button = createElement("button", className, label);
  button.type = "button";
  button.addEventListener("click", onClick);
  return button;
}

function recordValue(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === "object"
    ? (value as Record<string, unknown>)
    : undefined;
}

function stringValue(record: Record<string, unknown>, key: string): string {
  const value = record[key];
  return typeof value === "string" ? value : "";
}

function numberValue(record: Record<string, unknown>, key: string): number {
  const value = record[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function recordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.map(recordValue).filter((item): item is Record<string, unknown> => !!item)
    : [];
}

function safeExternalUrl(value: string): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : undefined;
  } catch {
    return undefined;
  }
}

function createExternalLink(label: string, url: string): HTMLAnchorElement | undefined {
  const safeUrl = safeExternalUrl(url);
  if (!safeUrl) return undefined;
  const link = createElement("a", "yield-wiki-link", label);
  link.href = safeUrl;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return link;
}

function createSessionId(): string {
  return crypto.randomUUID();
}

function formattedError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "API 토큰을 확인하세요.";
    if (error.status === 404) return "요청한 항목이 더 이상 존재하지 않습니다.";
    if (error.status === 502) return "Backend 의존 서비스가 응답하지 않습니다.";
  }
  return error instanceof Error ? error.message : "요청을 완료하지 못했습니다.";
}

function newestSession(sessions: PluginSessionSummary[]): PluginSessionSummary | undefined {
  return [...sessions].sort((left, right) => {
    const rightTime = Date.parse(right.updated_at);
    const leftTime = Date.parse(left.updated_at);
    return (Number.isNaN(rightTime) ? 0 : rightTime) -
      (Number.isNaN(leftTime) ? 0 : leftTime);
  })[0];
}

function citationsFromEvent(event: Record<string, unknown>): CitationView[] {
  const citations = Array.isArray(event.citations) ? event.citations : [];
  return citations.flatMap((value): CitationView[] => {
    const citation = recordValue(value);
    if (!citation) return [];
    const docId = stringValue(citation, "doc_id");
    if (!docId) return [];
    const sourcePath = stringValue(citation, "source_path");
    const downloadUrl = stringValue(citation, "download_url");
    return [
      {
        docId,
        label: stringValue(citation, "label") || docId,
        sourcePath: sourcePath || undefined,
        downloadUrl: safeExternalUrl(downloadUrl),
      },
    ];
  });
}

function optionParts(option: unknown): { value: string; label: string; hint: string } {
  if (typeof option === "string") return { value: option, label: option, hint: "" };
  const record = recordValue(option) ?? {};
  const value = stringValue(record, "value") || stringValue(record, "label");
  return {
    value,
    label: stringValue(record, "label") || value,
    hint: stringValue(record, "hint"),
  };
}

export async function openVaultPath(app: App, path: string): Promise<void> {
  const linkText = path.replace(/\.md$/i, "");
  await app.workspace.openLinkText(linkText, "", false);
}

export class YieldWikiView extends ItemView {
  private activeTab: ActiveTab = "chat";
  private chatAbort?: AbortController;
  private chatEvents: SseEvent[] = [];
  private searchResults: PluginSearchResult[] = [];
  private reviews: PluginReview[] = [];

  private readonly api: YieldWikiApiClient;
  private viewOpen = false;
  private sessionId = createSessionId();
  private connectionState: ConnectionState = "checking";
  private connectionDetail = "Backend 확인 중…";
  private contextEnabled = true;
  private chatEntries: ChatEntry[] = [];
  private isStreaming = false;
  private chatStatus = "";
  private thinkingText = "";
  private streamSummary = "";
  private transportError = "";
  private lastChatRequest?: ChatRequest;
  private searchQuery = "";
  private searchMode?: "hybrid" | "bm25_fallback";
  private searchLoading = false;
  private searchError = "";
  private reviewsLoading = false;
  private reviewError = "";

  constructor(
    leaf: WorkspaceLeaf,
    private readonly yieldWikiPlugin: YieldWikiViewPlugin,
    api?: YieldWikiApiClient,
  ) {
    super(leaf);
    this.api = api ?? new YieldWikiApi(yieldWikiPlugin.settings);
  }

  getViewType(): string {
    return YIELD_WIKI_VIEW_TYPE;
  }

  getDisplayText(): string {
    return "Yield Wiki";
  }

  getIcon(): string {
    return "microscope";
  }

  async onOpen(): Promise<void> {
    this.viewOpen = true;
    this.render();
    await this.restoreSession();
  }

  async onClose(): Promise<void> {
    this.viewOpen = false;
    this.chatAbort?.abort();
    this.contentEl.replaceChildren();
  }

  private async restoreSession(): Promise<void> {
    this.connectionState = "checking";
    this.connectionDetail = "Backend 확인 중…";
    this.render();
    try {
      const sessions = await this.api.listSessions();
      const latest = newestSession(sessions);
      if (latest) {
        this.sessionId = latest.session_id;
        const history = await this.api.getSession(latest.session_id);
        this.chatEntries = this.entriesFromHistory(history);
      }
      this.connectionState = "connected";
      this.connectionDetail = "연결됨";
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        this.connectionState = "unauthorized";
        this.connectionDetail = "인증 실패 · 설정에서 API 토큰을 확인하세요.";
      } else {
        this.connectionState = "offline";
        this.connectionDetail = `연결 실패 · ${formattedError(error)}`;
      }
    }
    this.render();
  }

  private entriesFromHistory(history: PluginSessionHistory): ChatEntry[] {
    const entries: ChatEntry[] = [];
    for (const turn of history.turns) {
      entries.push({
        kind: turn.role,
        text: turn.content ?? "",
        agent: turn.agent,
        citations: (turn.citations ?? []).map((citation) => ({
          docId: citation.doc_id,
          label: citation.label || citation.doc_id,
          sourcePath: citation.source_path || undefined,
          downloadUrl: safeExternalUrl(citation.download_url ?? ""),
        })),
      });
      for (const artifact of turn.artifacts ?? []) {
        entries.push({
          kind: "artifact",
          title: artifact.title ?? artifact.artifact_id,
          artifactType: artifact.artifact_type,
          agent: artifact.agent ?? "",
          data: artifact.data ?? "",
        });
      }
      if (turn.suggestion) entries.push({ kind: "suggestion", text: turn.suggestion });
    }
    return entries;
  }

  private render(): void {
    if (!this.viewOpen) return;
    this.contentEl.replaceChildren();
    const sidebar = createElement("div", "yield-wiki-sidebar");
    sidebar.append(this.renderHeader(), this.renderTabs());

    const panel = createElement("section", "yield-wiki-panel");
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-label", this.activeTab);
    if (this.activeTab === "chat") this.renderChat(panel);
    if (this.activeTab === "search") this.renderSearch(panel);
    if (this.activeTab === "review") this.renderReviews(panel);
    sidebar.append(panel);
    this.contentEl.append(sidebar);
  }

  private renderHeader(): HTMLElement {
    const header = createElement("header", "yield-wiki-header");
    const identity = createElement("div", "yield-wiki-identity");
    identity.append(
      createElement("strong", "yield-wiki-title", "Yield Wiki"),
      createElement("span", "yield-wiki-session", this.sessionId.slice(0, 8)),
    );
    const connection = createElement(
      "span",
      `yield-wiki-connection yield-wiki-connection--${this.connectionState}`,
    );
    connection.setAttribute("role", "status");
    connection.append(
      createElement("span", "yield-wiki-connection-dot"),
      createElement("span", "yield-wiki-connection-text", this.connectionDetail),
    );
    const newChat = createButton("새 대화", "yield-wiki-quiet-button", () => {
      this.startNewSession();
    });
    header.append(identity, connection, newChat);
    return header;
  }

  private renderTabs(): HTMLElement {
    const tabs = createElement("div", "yield-wiki-tabs");
    tabs.setAttribute("role", "tablist");
    const labels: Array<[ActiveTab, string]> = [
      ["chat", "Chat"],
      ["search", "Search"],
      ["review", "Review"],
    ];
    for (const [tab, label] of labels) {
      const button = createButton(label, "yield-wiki-tab", () => {
        this.activeTab = tab;
        this.render();
        if (tab === "review" && this.reviews.length === 0 && !this.reviewsLoading) {
          void this.loadReviews();
        }
      });
      button.setAttribute("role", "tab");
      button.setAttribute("aria-selected", String(this.activeTab === tab));
      if (this.activeTab === tab) button.classList.add("yield-wiki-tab--active");
      tabs.append(button);
    }
    return tabs;
  }

  private renderChat(panel: HTMLElement): void {
    const feed = createElement("div", "yield-wiki-chat-feed");
    feed.setAttribute("aria-live", "polite");
    if (this.chatEntries.length === 0) {
      const empty = createElement("div", "yield-wiki-empty");
      empty.append(
        createElement("strong", "yield-wiki-empty-title", "근거에서 시작하세요"),
        createElement(
          "p",
          "yield-wiki-empty-copy",
          "현재 노트를 문맥으로 사용하거나 수율 이슈를 직접 질문하세요.",
        ),
      );
      feed.append(empty);
    } else {
      this.chatEntries.forEach((entry) => feed.append(this.renderChatEntry(entry)));
    }

    if (this.chatStatus || this.thinkingText || this.isStreaming || this.streamSummary) {
      const progress = createElement("div", "yield-wiki-stream-state");
      if (this.isStreaming) {
        const indicator = createElement("span", "yield-wiki-stream-indicator");
        indicator.setAttribute("aria-label", "응답 생성 중");
        progress.append(indicator);
      }
      if (this.chatStatus) {
        progress.append(createElement("span", "yield-wiki-stream-status", this.chatStatus));
      }
      if (this.thinkingText) {
        const thinking = createElement("details", "yield-wiki-thinking");
        thinking.append(
          createElement("summary", "yield-wiki-thinking-label", "Thinking"),
          createElement("p", "yield-wiki-thinking-copy", this.thinkingText),
        );
        progress.append(thinking);
      }
      if (this.streamSummary) {
        progress.append(createElement("span", "yield-wiki-stream-summary", this.streamSummary));
      }
      feed.append(progress);
    }

    if (this.transportError && this.lastChatRequest) {
      const retry = createElement("div", "yield-wiki-retry");
      retry.append(
        createElement("span", "yield-wiki-error-text", this.transportError),
        createButton("다시 시도", "yield-wiki-secondary-button", () => {
          if (this.lastChatRequest) void this.runChat(this.lastChatRequest, false);
        }),
      );
      feed.append(retry);
    }

    panel.append(feed, this.renderChatComposer());
  }

  private renderChatEntry(entry: ChatEntry): HTMLElement {
    if (isChatMessageEntry(entry)) {
      const message = createElement(
        "article",
        `yield-wiki-message yield-wiki-message--${entry.kind}`,
      );
      const meta = entry.kind === "user" ? "You" : entry.agent || "Yield Agent";
      message.append(
        createElement("span", "yield-wiki-message-meta", meta),
        createElement("p", "yield-wiki-message-copy", entry.text),
      );
      if (entry.streaming) message.classList.add("yield-wiki-message--streaming");
      if (entry.citations?.length) {
        const citations = createElement("div", "yield-wiki-citations");
        for (const citation of entry.citations) {
          citations.append(this.renderCitation(citation));
        }
        message.append(citations);
      }
      return message;
    }

    if (entry.kind === "artifact") {
      const artifact = createElement("details", "yield-wiki-artifact");
      const summary = createElement("summary", "yield-wiki-artifact-title");
      summary.append(
        createElement("span", "yield-wiki-artifact-name", entry.title || "Artifact"),
        createElement(
          "span",
          "yield-wiki-mono yield-wiki-artifact-meta",
          [entry.artifactType, entry.agent].filter(Boolean).join(" · "),
        ),
      );
      artifact.append(summary);
      const artifactLink =
        entry.artifactType === "pptx" ? createExternalLink("원본 열기", entry.data) : undefined;
      if (artifactLink) artifact.append(artifactLink);
      else if (entry.data) artifact.append(createElement("pre", "yield-wiki-artifact-data", entry.data));
      return artifact;
    }

    if (entry.kind === "suggestion") {
      const suggestion = createElement("aside", "yield-wiki-suggestion");
      suggestion.append(
        createElement("span", "yield-wiki-eyebrow", "Suggestion"),
        createElement("p", "yield-wiki-suggestion-copy", entry.text),
      );
      return suggestion;
    }

    if (entry.kind === "error") {
      return createElement("div", "yield-wiki-inline-error", entry.text);
    }

    return this.renderInterrupt(entry);
  }

  private renderCitation(citation: CitationView): HTMLElement {
    const item = createElement("span", "yield-wiki-citation");
    if (citation.sourcePath) {
      item.append(
        createButton(citation.label, "yield-wiki-link-button yield-wiki-mono", () => {
          if (citation.sourcePath) {
            void openVaultPath(this.yieldWikiPlugin.app, citation.sourcePath);
          }
        }),
      );
    } else {
      item.append(createElement("span", "yield-wiki-mono", citation.label));
    }
    if (citation.downloadUrl) {
      const external = createExternalLink("원본", citation.downloadUrl);
      if (external) item.append(external);
    }
    return item;
  }

  private renderInterrupt(entry: ChatInterruptEntry): HTMLElement {
    const card = createElement("section", "yield-wiki-interrupt");
    card.append(
      createElement("span", "yield-wiki-eyebrow", "Action required"),
      createElement("p", "yield-wiki-interrupt-copy", entry.message || "추가 입력이 필요합니다."),
    );
    if (entry.answered) {
      card.append(createElement("p", "yield-wiki-interrupt-answer", `응답 · ${entry.answered}`));
      return card;
    }

    if (entry.options.length) {
      const options = createElement("div", "yield-wiki-interrupt-options");
      for (const rawOption of entry.options) {
        const option = optionParts(rawOption);
        if (!option.value) continue;
        const optionButton = createButton(option.label, "yield-wiki-secondary-button", () => {
          const resume = entry.param ? { [entry.param]: option.value } : option.value;
          this.resumeInterrupt(entry, resume, option.label);
        });
        if (option.hint) optionButton.title = option.hint;
        options.append(optionButton);
      }
      card.append(options);
    }

    if (entry.fields.length) {
      const form = createElement("form", "yield-wiki-interrupt-form");
      for (const field of entry.fields) {
        const slot = stringValue(field, "slot");
        if (!slot) continue;
        const label = createElement("label", "yield-wiki-field");
        label.append(
          createElement("span", "yield-wiki-field-label", stringValue(field, "label") || slot),
        );
        const fieldOptions = Array.isArray(field.options) ? field.options : [];
        if (fieldOptions.length) {
          const select = createElement("select", "yield-wiki-select");
          select.name = slot;
          select.append(createElement("option", undefined, "선택"));
          for (const rawOption of fieldOptions) {
            const option = optionParts(rawOption);
            const element = createElement("option", undefined, option.label);
            element.value = option.value;
            select.append(element);
          }
          label.append(select);
        } else {
          const input = createElement("input", "yield-wiki-input");
          input.name = slot;
          input.placeholder = stringValue(field, "validation_hint") || slot;
          label.append(input);
        }
        form.append(label);
      }
      form.append(createButton("응답 보내기", "yield-wiki-primary-button", () => undefined));
      const submitButton = form.querySelector<HTMLButtonElement>("button");
      if (submitButton) submitButton.type = "submit";
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        const values: Record<string, string> = {};
        new FormData(form).forEach((value, key) => {
          const normalized = String(value).trim();
          if (normalized) values[key] = normalized;
        });
        if (!Object.keys(values).length) return;
        const label = Object.entries(values)
          .map(([key, value]) => `${key}=${value}`)
          .join(", ");
        this.resumeInterrupt(entry, values, label);
      });
      card.append(form);
    } else {
      const form = createElement("form", "yield-wiki-interrupt-form");
      const input = createElement("input", "yield-wiki-input");
      input.placeholder = "응답을 입력하세요";
      input.setAttribute("aria-label", "Interrupt response");
      input.dataset.testid = "interrupt-response";
      const submit = createElement("button", "yield-wiki-primary-button", "응답 보내기");
      submit.type = "submit";
      form.append(input, submit);
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        const value = input.value.trim();
        if (!value) return;
        this.resumeInterrupt(entry, value, value);
      });
      card.append(form);
    }
    return card;
  }

  private renderChatComposer(): HTMLElement {
    const wrapper = createElement("div", "yield-wiki-composer");
    const context = createElement("label", "yield-wiki-context-toggle");
    const toggle = createElement("input");
    toggle.type = "checkbox";
    toggle.checked = this.contextEnabled;
    toggle.disabled = this.isStreaming;
    toggle.dataset.testid = "context-toggle";
    const updateContext = () => {
      this.contextEnabled = toggle.checked;
    };
    toggle.addEventListener("click", updateContext);
    toggle.addEventListener("input", updateContext);
    toggle.addEventListener("change", updateContext);
    context.append(toggle, createElement("span", undefined, "현재 Markdown 노트 사용"));

    const form = createElement("form", "yield-wiki-chat-form");
    const input = createElement("input", "yield-wiki-input yield-wiki-chat-input");
    input.type = "text";
    input.placeholder = this.pendingInterrupt() ? "응답을 입력하세요" : "근거에 대해 질문하세요";
    input.setAttribute("aria-label", "Chat message");
    input.dataset.testid = "chat-input";
    input.disabled = this.isStreaming;
    const submit = createElement("button", "yield-wiki-primary-button", "보내기");
    submit.type = "submit";
    submit.disabled = this.isStreaming;
    form.append(input, submit);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const query = input.value.trim();
      if (!query || this.isStreaming) return;
      input.value = "";
      const interrupt = this.pendingInterrupt();
      if (interrupt) {
        this.resumeInterrupt(interrupt, query, query);
      } else {
        void this.submitChat(query);
      }
    });
    wrapper.append(context, form);
    return wrapper;
  }

  private pendingInterrupt(): ChatInterruptEntry | undefined {
    return [...this.chatEntries]
      .reverse()
      .find(
        (entry): entry is ChatInterruptEntry => entry.kind === "interrupt" && !entry.answered,
      );
  }

  private startNewSession(): void {
    this.chatAbort?.abort();
    this.sessionId = createSessionId();
    this.chatEntries = [];
    this.chatEvents = [];
    this.chatStatus = "";
    this.thinkingText = "";
    this.streamSummary = "";
    this.transportError = "";
    this.lastChatRequest = undefined;
    this.isStreaming = false;
    this.render();
  }

  private async submitChat(query: string): Promise<void> {
    const request: ChatRequest = { query, session_id: this.sessionId };
    const activeFile = this.yieldWikiPlugin.app.workspace.getActiveFile();
    if (
      this.contextEnabled &&
      activeFile &&
      (activeFile.extension.toLowerCase() === "md" || /\.md$/i.test(activeFile.path))
    ) {
      request.current_note_id = activeFile.path;
    }
    await this.runChat(request, true);
  }

  private resumeInterrupt(
    interrupt: ChatInterruptEntry,
    resumeValue: ResumeValue,
    label: string,
  ): void {
    if (this.isStreaming || interrupt.answered) return;
    interrupt.answered = label;
    const request: ChatRequest = {
      query: label,
      session_id: this.sessionId,
      resume_value: resumeValue,
    };
    void this.runChat(request, true);
  }

  private async runChat(request: ChatRequest, appendUser: boolean): Promise<void> {
    if (this.isStreaming) return;
    this.isStreaming = true;
    this.lastChatRequest = { ...request };
    this.transportError = "";
    this.chatStatus = "응답 준비 중";
    this.thinkingText = "";
    this.streamSummary = "";
    if (appendUser) this.chatEntries.push({ kind: "user", text: request.query });
    this.chatAbort = new AbortController();
    this.render();

    try {
      await this.api.streamChat(
        request,
        (event) => this.handleChatEvent(event),
        this.chatAbort.signal,
      );
    } catch (error) {
      if (!(error instanceof Error && error.name === "AbortError")) {
        const message = formattedError(error);
        this.transportError = message;
        this.chatEntries.push({ kind: "error", text: message });
      }
    } finally {
      this.isStreaming = false;
      for (const entry of this.chatEntries) {
        if (entry.kind === "assistant") entry.streaming = false;
      }
      this.render();
    }
  }

  private handleChatEvent(event: SseEvent): void {
    this.chatEvents.push(event);
    const record = event as Record<string, unknown>;
    switch (event.type) {
      case "status":
        this.chatStatus = stringValue(record, "message");
        break;
      case "thinking":
        this.thinkingText += stringValue(record, "content");
        break;
      case "token":
        this.appendToken(
          stringValue(record, "content"),
          stringValue(record, "agent") || stringValue(record, "node"),
        );
        break;
      case "message":
        this.finalizeMessage(
          stringValue(record, "content"),
          stringValue(record, "agent"),
          citationsFromEvent(record),
        );
        this.thinkingText = "";
        break;
      case "artifact":
        this.chatEntries.push({
          kind: "artifact",
          title: stringValue(record, "title") || stringValue(record, "artifact_id"),
          artifactType: stringValue(record, "artifact_type"),
          agent: stringValue(record, "agent"),
          data: stringValue(record, "data"),
        });
        break;
      case "suggestion":
        this.chatEntries.push({ kind: "suggestion", text: stringValue(record, "content") });
        this.thinkingText = "";
        break;
      case "interrupt":
        this.chatEntries.push({
          kind: "interrupt",
          message: stringValue(record, "message"),
          interruptType: stringValue(record, "interrupt_type"),
          param: stringValue(record, "param"),
          options: Array.isArray(record.options) ? record.options : [],
          fields: recordArray(record.fields),
        });
        this.thinkingText = "";
        break;
      case "error": {
        const message = stringValue(record, "message") || "Backend 오류가 발생했습니다.";
        this.chatEntries.push({ kind: "error", text: message });
        this.transportError = message;
        break;
      }
      case "stream_end": {
        const steps = numberValue(record, "total_steps");
        const elapsed = numberValue(record, "elapsed");
        this.chatStatus = "";
        this.thinkingText = "";
        this.streamSummary = [
          steps ? `${steps} steps` : "",
          elapsed ? `${elapsed.toFixed(1)}초` : "완료",
        ]
          .filter(Boolean)
          .join(" · ");
        for (const entry of this.chatEntries) {
          if (entry.kind === "assistant") entry.streaming = false;
        }
        break;
      }
    }
    this.render();
  }

  private appendToken(content: string, agent: string): void {
    if (!content) return;
    const last = this.chatEntries.at(-1);
    if (last?.kind === "assistant" && last.streaming && last.agent === agent) {
      last.text += content;
      return;
    }
    this.chatEntries.push({ kind: "assistant", text: content, agent, streaming: true });
  }

  private finalizeMessage(content: string, agent: string, citations: CitationView[]): void {
    const last = this.chatEntries.at(-1);
    if (last?.kind === "assistant" && last.streaming) {
      last.text = content || last.text;
      last.agent = agent || last.agent;
      last.streaming = false;
      last.citations = citations;
      return;
    }
    this.chatEntries.push({ kind: "assistant", text: content, agent, citations });
  }

  private renderSearch(panel: HTMLElement): void {
    const form = createElement("form", "yield-wiki-search-form");
    const query = createElement("input", "yield-wiki-input");
    query.type = "search";
    query.value = this.searchQuery;
    query.placeholder = "Concept 또는 근거 검색";
    query.setAttribute("aria-label", "Search query");
    query.dataset.testid = "search-input";
    const submit = createElement("button", "yield-wiki-primary-button", "검색");
    submit.type = "submit";
    submit.disabled = this.searchLoading;
    form.append(query, submit);

    const filters = createElement("details", "yield-wiki-search-filters");
    filters.append(createElement("summary", "yield-wiki-search-filter-label", "필터"));
    const filterFields = createElement("div", "yield-wiki-filter-grid");
    for (const [name, label] of [
      ["product", "Product"],
      ["failType", "Fail"],
      ["causeOper", "Operation"],
    ] as const) {
      const field = createElement("label", "yield-wiki-field");
      field.append(createElement("span", "yield-wiki-field-label", label));
      const input = createElement("input", "yield-wiki-input yield-wiki-mono");
      input.name = name;
      field.append(input);
      filterFields.append(field);
    }
    filters.append(filterFields);
    form.append(filters);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const value = query.value.trim();
      if (!value || this.searchLoading) return;
      const data = new FormData(form);
      void this.performSearch({
        query: value,
        product: String(data.get("product") ?? "").trim(),
        failType: String(data.get("failType") ?? "").trim(),
        causeOper: String(data.get("causeOper") ?? "").trim(),
      });
    });
    panel.append(form);

    const results = createElement("div", "yield-wiki-search-results");
    results.setAttribute("aria-live", "polite");
    if (this.searchMode === "bm25_fallback") {
      results.append(
        createElement("div", "yield-wiki-warning", "키워드 검색으로 대체됨"),
      );
    }
    if (this.searchError) {
      results.append(createElement("div", "yield-wiki-inline-error", this.searchError));
    } else if (this.searchLoading) {
      results.append(createElement("p", "yield-wiki-muted", "검색 중…"));
    } else if (this.searchQuery && this.searchResults.length === 0) {
      results.append(
        createElement("p", "yield-wiki-muted", "일치하는 Concept 또는 Source가 없습니다."),
      );
    } else {
      this.searchResults.forEach((result) => results.append(this.renderSearchResult(result)));
    }
    panel.append(results);
  }

  private async performSearch(request: PluginSearchRequest): Promise<void> {
    this.searchQuery = request.query;
    this.searchLoading = true;
    this.searchError = "";
    this.render();
    try {
      const response = await this.api.search(request);
      this.searchResults = response.results;
      this.searchMode = response.retrieval_mode;
    } catch (error) {
      this.searchResults = [];
      this.searchMode = undefined;
      this.searchError = formattedError(error);
    } finally {
      this.searchLoading = false;
      this.render();
    }
  }

  private renderSearchResult(result: PluginSearchResult): HTMLElement {
    const card = createElement("article", "yield-wiki-result");
    const top = createElement("div", "yield-wiki-result-top");
    if (result.concept_status === "materialized" && result.concept_path) {
      top.append(
        createButton(
          result.concept_id || result.concept_path,
          "yield-wiki-concept-link yield-wiki-mono",
          () => {
            if (result.concept_path) {
              void openVaultPath(this.yieldWikiPlugin.app, result.concept_path);
            }
          },
        ),
      );
    } else {
      top.append(
        createElement("span", "yield-wiki-source-only", "Source only · Concept 미생성"),
      );
    }
    top.append(createElement("span", "yield-wiki-score", result.score.toFixed(2)));
    card.append(top);

    const rail = createElement("dl", "yield-wiki-evidence-rail");
    const metadata: Array<[string, string]> = [
      ["Product", result.product],
      ["Fail", result.fail_type],
      ["Operation", result.cause_oper],
      ["Sources", String(result.evidence.length)],
    ];
    for (const [label, value] of metadata) {
      const item = createElement("div", "yield-wiki-rail-item");
      item.append(
        createElement("dt", "yield-wiki-rail-label", `${label} `),
        createElement("dd", "yield-wiki-rail-value yield-wiki-mono", value),
      );
      rail.append(item);
    }
    card.append(rail);

    const evidenceList = createElement("div", "yield-wiki-evidence-list");
    for (const evidence of result.evidence) {
      const evidenceEl = createElement("section", "yield-wiki-evidence");
      const header = createElement("div", "yield-wiki-evidence-header");
      header.append(createElement("span", "yield-wiki-mono", evidence.doc_id));
      const links = createElement("span", "yield-wiki-evidence-links");
      if (evidence.source_path) {
        links.append(
          createButton("Source 노트", "yield-wiki-link-button", () => {
            if (evidence.source_path) {
              void openVaultPath(this.yieldWikiPlugin.app, evidence.source_path);
            }
          }),
        );
      }
      const external = createExternalLink("원본", evidence.download_url ?? "");
      if (external) links.append(external);
      header.append(links);
      evidenceEl.append(header);
      const evidenceText = [
        evidence.content,
        evidence.cause && `Cause · ${evidence.cause}`,
        evidence.action && `Action · ${evidence.action}`,
        evidence.comment && `Comment · ${evidence.comment}`,
      ].filter((value): value is string => !!value);
      for (const line of evidenceText) {
        evidenceEl.append(createElement("p", "yield-wiki-evidence-copy", line));
      }
      evidenceList.append(evidenceEl);
    }
    card.append(evidenceList);
    return card;
  }

  private renderReviews(panel: HTMLElement): void {
    const header = createElement("div", "yield-wiki-review-heading");
    header.append(
      createElement("div", undefined, `${this.reviews.length} pending`),
      createButton("새로고침", "yield-wiki-quiet-button", () => {
        void this.loadReviews();
      }),
    );
    panel.append(header);
    const content = createElement("div", "yield-wiki-review-list");
    content.setAttribute("aria-live", "polite");
    if (this.reviewError) {
      content.append(createElement("div", "yield-wiki-inline-error", this.reviewError));
    }
    if (this.reviewsLoading) {
      content.append(createElement("p", "yield-wiki-muted", "Review 불러오는 중…"));
    } else if (this.reviews.length === 0) {
      content.append(createElement("p", "yield-wiki-muted", "대기 중인 Review가 없습니다."));
    } else {
      this.reviews.forEach((review) => content.append(this.renderReview(review)));
    }
    panel.append(content);
  }

  private renderReview(review: PluginReview): HTMLElement {
    const card = createElement("article", "yield-wiki-review");
    const top = createElement("div", "yield-wiki-review-top");
    top.append(
      createElement("span", "yield-wiki-review-type", review.review_type),
      createElement("span", "yield-wiki-mono yield-wiki-review-version", `v${review.version}`),
    );
    card.append(
      top,
      createElement("div", "yield-wiki-mono yield-wiki-review-target", review.target_concept_id),
      createElement("p", "yield-wiki-review-body", review.body_markdown),
    );

    if (review.history.length) {
      const history = createElement("details", "yield-wiki-review-history");
      history.append(createElement("summary", undefined, `이력 ${review.history.length}`));
      const list = createElement("ol", "yield-wiki-history-list");
      for (const item of review.history) {
        const row = createElement("li", "yield-wiki-history-item");
        row.append(
          createElement(
            "span",
            "yield-wiki-history-state",
            `${item.from_status} → ${item.to_status}`,
          ),
          createElement(
            "span",
            "yield-wiki-history-meta",
            `${item.changed_at} · ${item.reviewer}`,
          ),
        );
        if (item.comment) {
          row.append(createElement("p", "yield-wiki-history-comment", item.comment));
        }
        list.append(row);
      }
      history.append(list);
      card.append(history);
    }

    const fields = createElement("div", "yield-wiki-review-fields");
    const reviewerLabel = createElement("label", "yield-wiki-field");
    reviewerLabel.append(createElement("span", "yield-wiki-field-label", "Reviewer"));
    const reviewer = createElement("input", "yield-wiki-input");
    reviewer.required = true;
    reviewer.placeholder = "operator-id";
    reviewer.dataset.testid = "reviewer-input";
    reviewerLabel.append(reviewer);
    const commentLabel = createElement("label", "yield-wiki-field");
    commentLabel.append(createElement("span", "yield-wiki-field-label", "Comment"));
    const comment = createElement("textarea", "yield-wiki-textarea");
    comment.rows = 2;
    comment.placeholder = "판단 근거를 남기세요";
    comment.dataset.testid = "review-comment";
    commentLabel.append(comment);
    fields.append(reviewerLabel, commentLabel);
    card.append(fields);

    const actions = createElement("div", "yield-wiki-review-actions");
    const decide = (status: "approved" | "rejected") => {
      const reviewerValue = reviewer.value.trim();
      if (!reviewerValue) {
        reviewer.reportValidity();
        return;
      }
      void this.updateReview(review, status, reviewerValue, comment.value.trim());
    };
    actions.append(
      createButton("반려", "yield-wiki-secondary-button yield-wiki-danger-button", () => {
        decide("rejected");
      }),
      createButton("승인", "yield-wiki-primary-button", () => {
        decide("approved");
      }),
    );
    card.append(actions);
    return card;
  }

  private async loadReviews(preserveMessage = ""): Promise<void> {
    this.reviewsLoading = true;
    if (!preserveMessage) this.reviewError = "";
    this.render();
    try {
      this.reviews = await this.api.listReviews("pending");
      this.reviewError = preserveMessage;
    } catch (error) {
      this.reviewError = preserveMessage || formattedError(error);
    } finally {
      this.reviewsLoading = false;
      this.render();
    }
  }

  private async updateReview(
    review: PluginReview,
    status: "approved" | "rejected",
    reviewer: string,
    comment: string,
  ): Promise<void> {
    this.reviewError = "";
    try {
      await this.api.updateReview(review.id, {
        status,
        reviewer,
        comment,
        expected_version: review.version,
      });
      await this.loadReviews();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        await this.loadReviews("다른 사용자가 먼저 변경했습니다 · 최신 Review를 불러왔습니다.");
        return;
      }
      this.reviewError = formattedError(error);
      this.render();
    }
  }
}
