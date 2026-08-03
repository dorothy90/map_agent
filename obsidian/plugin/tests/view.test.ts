// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("obsidian", () => {
  class FakeTextComponent {
    inputEl = document.createElement("input");

    constructor(parent: HTMLElement) {
      parent.append(this.inputEl);
    }

    setPlaceholder(value: string): this {
      this.inputEl.placeholder = value;
      return this;
    }

    setValue(value: string): this {
      this.inputEl.value = value;
      return this;
    }

    onChange(callback: (value: string) => unknown): this {
      this.inputEl.addEventListener("input", () => callback(this.inputEl.value));
      return this;
    }
  }

  class FakeButtonComponent {
    buttonEl = document.createElement("button");

    constructor(parent: HTMLElement) {
      parent.append(this.buttonEl);
    }

    setButtonText(value: string): this {
      this.buttonEl.textContent = value;
      return this;
    }

    setCta(): this {
      return this;
    }

    onClick(callback: () => unknown): this {
      this.buttonEl.addEventListener("click", callback);
      return this;
    }
  }

  class FakeSetting {
    settingEl = document.createElement("div");
    nameEl = document.createElement("div");
    descEl = document.createElement("div");
    controlEl = document.createElement("div");

    constructor(container: HTMLElement) {
      this.settingEl.append(this.nameEl, this.descEl, this.controlEl);
      container.append(this.settingEl);
    }

    setName(value: string): this {
      this.nameEl.textContent = value;
      return this;
    }

    setDesc(value: string): this {
      this.descEl.textContent = value;
      return this;
    }

    addText(callback: (component: FakeTextComponent) => unknown): this {
      callback(new FakeTextComponent(this.controlEl));
      return this;
    }

    addButton(callback: (component: FakeButtonComponent) => unknown): this {
      callback(new FakeButtonComponent(this.controlEl));
      return this;
    }
  }

  return {
    ItemView: class {
    app: unknown;
    containerEl = document.createElement("div");
    contentEl = document.createElement("div");

    constructor(leaf: { app?: unknown }) {
      this.app = leaf.app;
      this.containerEl.append(this.contentEl);
    }
    },
    PluginSettingTab: class {
      containerEl = document.createElement("div");

      constructor(
        public app: unknown,
        public plugin: unknown,
      ) {}
    },
    Setting: FakeSetting,
  };
});

import { ApiError } from "../src/api";
import { DEFAULT_SETTINGS, YieldWikiSettingTab } from "../src/settings";
import type {
  PluginRelatedResponse,
  PluginReview,
  PluginSearchResponse,
  SseEvent,
} from "../src/types";
import {
  YieldWikiView,
  type YieldWikiApiClient,
  type YieldWikiViewPlugin,
} from "../src/view";

const searchResult: PluginSearchResponse = {
  query: "oxide",
  retrieval_mode: "bm25_fallback",
  results: [
    {
      concept_id: "4SS_PRE_METAL_CLN_EASY",
      concept_path: "concepts/4SS_PRE_METAL_CLN_EASY.md",
      concept_status: "materialized",
      product: "4SS",
      fail_type: "EASY",
      cause_oper: "PRE_METAL_CLN",
      retrieval_mode: "bm25_fallback",
      score: 0.91,
      evidence: [
        {
          doc_id: "FH-1",
          content: "oxide residue was confirmed",
          source_path: "sources/FH-1.md",
          download_url: "https://evidence.example/FH-1.pdf",
          score: 0.9,
        },
      ],
    },
  ],
};

const pendingReview: PluginReview = {
  id: "review:source-removal:1",
  review_type: "source_removal",
  status: "pending",
  target_concept_id: "concept:4SS|PRE METAL CLN|EASY",
  version: 2,
  created: "2026-08-01T01:00:00Z",
  updated: "2026-08-01T02:00:00Z",
  body_markdown: "FH-1 근거를 검토하세요.",
  metadata: {},
  history: [
    {
      changed_at: "2026-08-01T01:30:00Z",
      from_status: "pending",
      to_status: "rejected",
      reviewer: "operator-0",
      comment: "원본 재확인",
    },
  ],
};

function fakeApi(): YieldWikiApiClient {
  return {
    health: vi.fn().mockResolvedValue({ status: "ok" }),
    listSessions: vi.fn().mockResolvedValue([]),
    getSession: vi.fn().mockResolvedValue({ session_id: "unused", turns: [] }),
    search: vi.fn().mockResolvedValue(searchResult),
    related: vi.fn().mockResolvedValue({
      note_path: "concepts/4SS_PRE_METAL_CLN_EASY.md",
      outgoing: [],
      backlinks: [],
    }),
    listReviews: vi.fn().mockResolvedValue([pendingReview]),
    createReview: vi.fn().mockResolvedValue(pendingReview),
    updateReview: vi.fn().mockResolvedValue({
      ...pendingReview,
      status: "approved",
      version: 3,
    }),
    streamChat: vi.fn().mockResolvedValue(undefined),
  };
}

function createTestView(options: {
  activeFile?: string;
  activeConceptId?: string;
  api?: YieldWikiApiClient;
} = {}): {
  view: YieldWikiView;
  api: YieldWikiApiClient;
  workspace: {
    getActiveFile: ReturnType<typeof vi.fn>;
    openLinkText: ReturnType<typeof vi.fn>;
    on: ReturnType<typeof vi.fn>;
    offref: ReturnType<typeof vi.fn>;
  };
  setActiveConcept: (path: string, conceptId: string) => void;
  emitFileOpen: () => void;
} {
  const api = options.api ?? fakeApi();
  let activeFile = options.activeFile
    ? { path: options.activeFile, extension: options.activeFile.split(".").pop() ?? "" }
    : null;
  let activeConceptId = options.activeConceptId;
  const fileOpenListeners = new Set<{ callback: () => void }>();
  const workspace = {
    getActiveFile: vi.fn(() => activeFile),
    openLinkText: vi.fn().mockResolvedValue(undefined),
    on: vi.fn((_event: string, callback: () => void) => {
      const eventRef = { callback };
      fileOpenListeners.add(eventRef);
      return eventRef;
    }),
    offref: vi.fn((eventRef: { callback: () => void }) => {
      fileOpenListeners.delete(eventRef);
    }),
  };
  const metadataCache = {
    getFileCache: vi.fn(() =>
      activeConceptId
        ? { frontmatter: { id: activeConceptId, type: "concept" } }
        : null,
    ),
  };
  const plugin = {
    app: { workspace, metadataCache },
    settings: { ...DEFAULT_SETTINGS },
  } as unknown as YieldWikiViewPlugin;
  const view = new YieldWikiView(
    { app: plugin.app } as never,
    plugin,
    api,
  );
  document.body.append(view.containerEl);
  return {
    view,
    api,
    workspace,
    setActiveConcept: (path: string, conceptId: string) => {
      activeFile = { path, extension: path.split(".").pop() ?? "" };
      activeConceptId = conceptId;
    },
    emitFileOpen: () => {
      for (const eventRef of [...fileOpenListeners]) eventRef.callback();
    },
  };
}

function tabLabels(view: YieldWikiView): string[] {
  return Array.from(view.containerEl.querySelectorAll('[role="tab"]')).map(
    (element) => element.textContent ?? "",
  );
}

function clickButton(container: HTMLElement, label: string): void {
  findButton(container, label).click();
}

function findButton(container: HTMLElement, label: string): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll("button")).find(
    (candidate) => candidate.textContent?.trim() === label,
  );
  if (!button) throw new Error(`Button not found: ${label}`);
  return button;
}

async function submitForm(form: HTMLFormElement): Promise<void> {
  form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  await Promise.resolve();
}

async function submitChat(view: YieldWikiView, query: string): Promise<void> {
  const input = view.containerEl.querySelector<HTMLInputElement>(
    '[data-testid="chat-input"]',
  );
  const form = input?.closest("form");
  if (!input || !form) throw new Error("Chat form not found");
  input.value = query;
  await submitForm(form);
  await vi.waitFor(() => {
    expect((view as never as { isStreaming: boolean }).isStreaming).toBe(false);
  });
}

async function openSearch(view: YieldWikiView, query: string): Promise<void> {
  clickButton(view.containerEl, "Search");
  const input = view.containerEl.querySelector<HTMLInputElement>(
    '[data-testid="search-input"]',
  );
  const form = input?.closest("form");
  if (!input || !form) throw new Error("Search form not found");
  input.value = query;
  await submitForm(form);
  await vi.waitFor(() => {
    expect(view.containerEl.textContent).toContain("4SS_PRE_METAL_CLN_EASY");
  });
}

beforeEach(() => {
  document.body.replaceChildren();
});

describe("YieldWikiView", () => {
  it("shows Chat, Search, and Review tabs", async () => {
    const { view } = createTestView();

    await view.onOpen();

    expect(tabLabels(view)).toEqual(["Chat", "Search", "Review"]);
  });

  it("restores the latest Backend session and its readable history", async () => {
    const api = fakeApi();
    vi.mocked(api.listSessions).mockResolvedValue([
      {
        session_id: "older",
        last_query: "old",
        turn_count: 1,
        updated_at: "2026-07-31T23:00:00Z",
      },
      {
        session_id: "latest",
        last_query: "new",
        turn_count: 2,
        updated_at: "2026-08-01T02:00:00Z",
      },
    ]);
    vi.mocked(api.getSession).mockResolvedValue({
      session_id: "latest",
      turns: [
        {
          role: "user",
          content: "oxide 원인은?",
          timestamp: "2026-08-01T02:00:00Z",
        },
        {
          role: "assistant",
          agent: "fail_history_agent",
          content: "세정 공정을 확인하세요.",
          timestamp: "2026-08-01T02:00:01Z",
        },
      ],
    });
    const { view } = createTestView({ api });

    await view.onOpen();

    expect(api.getSession).toHaveBeenCalledWith("latest");
    expect(view.containerEl.textContent).toContain("oxide 원인은?");
    expect(view.containerEl.textContent).toContain("세정 공정을 확인하세요.");
    expect(view.containerEl.textContent).toContain("연결됨");
  });

  it("sends the active Markdown path only when note context is enabled", async () => {
    const { view, api } = createTestView({
      activeFile: "concepts/4SS_PRE_METAL_CLN_EASY.md",
    });
    await view.onOpen();

    await submitChat(view, "원인은?");
    expect(api.streamChat).toHaveBeenLastCalledWith(
      expect.objectContaining({
        current_note_id: "concepts/4SS_PRE_METAL_CLN_EASY.md",
      }),
      expect.any(Function),
      expect.any(AbortSignal),
    );

    const toggle = view.containerEl.querySelector<HTMLInputElement>(
      '[data-testid="context-toggle"]',
    );
    if (!toggle) throw new Error("Context toggle not found");
    toggle.click();
    expect(toggle.checked).toBe(false);
    expect((view as never as { contextEnabled: boolean }).contextEnabled).toBe(false);
    await submitChat(view, "다시 확인해줘");

    const lastRequest = vi.mocked(api.streamChat).mock.calls.at(-1)?.[0];
    expect(lastRequest).not.toHaveProperty("current_note_id");
  });

  it("does not send a non-Markdown active file as note context", async () => {
    const { view, api } = createTestView({ activeFile: "assets/map.png" });
    await view.onOpen();

    await submitChat(view, "맵을 봐줘");

    expect(vi.mocked(api.streamChat).mock.calls[0][0]).not.toHaveProperty(
      "current_note_id",
    );
  });

  it("renders partial streaming output and safely handles every supported event", async () => {
    const api = fakeApi();
    let emit: ((event: SseEvent) => void) | undefined;
    let finish: (() => void) | undefined;
    vi.mocked(api.streamChat).mockImplementation((_request, onEvent) => {
      emit = onEvent;
      onEvent({ type: "status", message: "근거 조회 중" });
      onEvent({ type: "thinking", content: "가능성 정리" });
      onEvent({ type: "token", content: "산화막 " });
      return new Promise<void>((resolve) => {
        finish = resolve;
      });
    });
    const { view } = createTestView({ api });
    await view.onOpen();

    const input = view.containerEl.querySelector<HTMLInputElement>(
      '[data-testid="chat-input"]',
    );
    const form = input?.closest("form");
    if (!input || !form) throw new Error("Chat form not found");
    input.value = "원인은?";
    await submitForm(form);
    expect(view.containerEl.textContent).toContain("근거 조회 중");
    expect(view.containerEl.textContent).toContain("가능성 정리");
    expect(view.containerEl.textContent).toContain("산화막");

    emit?.({
      type: "message",
      agent: "fail_history_agent",
      content: "산화막 잔류가 확인됐습니다.",
      citations: [
        {
          doc_id: "FH-1",
          label: "<img src=x onerror=alert(1)>",
          source_path: "sources/FH-1.md",
          download_url: "https://evidence.example/FH-1.pdf",
        },
      ],
    });
    emit?.({
      type: "artifact",
      artifact_id: "artifact-1",
      artifact_type: "markdown",
      title: "<script>alert(1)</script>",
      agent: "fail_history_agent",
      data: "<img src=x onerror=alert(1)>",
    });
    emit?.({ type: "suggestion", content: "세정 이력을 비교하세요." });
    emit?.({
      type: "interrupt",
      interrupt_type: "missing_param",
      param: "product",
      message: "제품을 선택하세요.",
      options: [{ value: "4SS", label: "4SS" }],
      fields: [],
    });
    emit?.({ type: "error", message: "일부 도구가 실패했습니다." });
    emit?.({ type: "stream_end", total_steps: 3, elapsed: 1.2 });
    finish?.();
    await vi.waitFor(() => expect(view.containerEl.textContent).toContain("1.2초"));

    expect(view.containerEl.textContent).toContain("산화막 잔류가 확인됐습니다.");
    expect(view.containerEl.textContent).toContain("세정 이력을 비교하세요.");
    expect(view.containerEl.textContent).toContain("제품을 선택하세요.");
    expect(view.containerEl.textContent).toContain("일부 도구가 실패했습니다.");
    expect(view.containerEl.querySelector("script")).toBeNull();
    expect(view.containerEl.querySelector("img")).toBeNull();
    const external = view.containerEl.querySelector<HTMLAnchorElement>(
      'a[href="https://evidence.example/FH-1.pdf"]',
    );
    expect(external).toMatchObject({ target: "_blank", rel: "noopener noreferrer" });
  });

  it("resolves a Backend-relative PPT artifact and rejects unsafe relative schemes", async () => {
    const api = fakeApi();
    vi.mocked(api.streamChat).mockImplementationOnce(async (_request, onEvent) => {
      for (const [artifactId, data] of [
        ["safe", "/download/pptx/yield-report.pptx"],
        ["protocol-relative", "//attacker.example/report.pptx"],
        ["javascript", "javascript:alert(1)"],
        ["data", "data:text/html,unsafe"],
      ]) {
        onEvent({
          type: "artifact",
          artifact_id: artifactId,
          artifact_type: "pptx",
          title: artifactId,
          data,
        });
      }
    });
    const { view } = createTestView({ api });
    await view.onOpen();

    await submitChat(view, "PPT를 만들어줘");

    const artifactLinks = Array.from(
      view.containerEl.querySelectorAll<HTMLAnchorElement>(".yield-wiki-artifact a"),
    );
    expect(artifactLinks.map((link) => link.href)).toEqual([
      "http://localhost:8001/download/pptx/yield-report.pptx",
    ]);
    expect(artifactLinks[0]).toMatchObject({
      target: "_blank",
      rel: "noopener noreferrer",
    });
  });

  it("waits for an explicit action before resuming an interrupt", async () => {
    const api = fakeApi();
    vi.mocked(api.streamChat).mockImplementationOnce(async (_request, onEvent) => {
      onEvent({
        type: "interrupt",
        interrupt_type: "missing_param",
        param: "product",
        message: "제품을 선택하세요.",
        options: [{ value: "4SS", label: "4SS" }],
        fields: [],
      });
    });
    const { view } = createTestView({ api });
    await view.onOpen();
    await submitChat(view, "원인은?");

    expect(api.streamChat).toHaveBeenCalledTimes(1);
    clickButton(view.containerEl, "4SS");
    await vi.waitFor(() => expect(api.streamChat).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.streamChat).mock.calls[1][0]).toMatchObject({
      query: "4SS",
      resume_value: { product: "4SS" },
    });
  });

  it("renders a manual response form when an interrupt has no structured fields", async () => {
    const api = fakeApi();
    vi.mocked(api.streamChat).mockImplementationOnce(async (_request, onEvent) => {
      onEvent({
        type: "interrupt",
        interrupt_type: "plan_review",
        param: "plan_review",
        message: "분석 계획을 승인하시겠습니까?",
        options: [],
        fields: [],
      });
    });
    const { view } = createTestView({ api });
    await view.onOpen();
    await submitChat(view, "원인은?");

    const response = view.containerEl.querySelector<HTMLInputElement>(
      '[data-testid="interrupt-response"]',
    );
    const form = response?.closest("form");
    expect(response).not.toBeNull();
    if (!response || !form) throw new Error("Interrupt response form not found");
    response.value = "그대로 진행";
    await submitForm(form);
    await vi.waitFor(() => expect(api.streamChat).toHaveBeenCalledTimes(2));

    expect(vi.mocked(api.streamChat).mock.calls[1][0]).toMatchObject({
      query: "그대로 진행",
      resume_value: "그대로 진행",
    });
  });

  it("does not resume structured interrupts until every required field is valid", async () => {
    const api = fakeApi();
    vi.mocked(api.streamChat).mockImplementationOnce(async (_request, onEvent) => {
      onEvent({
        type: "interrupt",
        interrupt_type: "missing_param",
        param: "product",
        message: "제품과 공정을 입력하세요.",
        options: [],
        fields: [
          {
            slot: "product",
            label: "Product",
            type: "choice",
            options: [{ value: "4SS", label: "4SS" }],
          },
          {
            slot: "cause_oper",
            label: "Operation",
            validation_hint: "PRE METAL CLN",
          },
        ],
      });
    });
    const { view } = createTestView({ api });
    await view.onOpen();
    await submitChat(view, "원인은?");

    const form = view.containerEl.querySelector<HTMLFormElement>(
      ".yield-wiki-interrupt-form",
    );
    const select = form?.querySelector<HTMLSelectElement>('select[name="product"]');
    const operation = form?.querySelector<HTMLInputElement>('input[name="cause_oper"]');
    if (!form || !select || !operation) throw new Error("Structured interrupt form not found");
    expect(select.options[0]).toMatchObject({ value: "", disabled: true, selected: true });

    await submitForm(form);
    expect(api.streamChat).toHaveBeenCalledTimes(1);

    select.value = "4SS";
    await submitForm(form);
    expect(api.streamChat).toHaveBeenCalledTimes(1);

    operation.value = "PRE METAL CLN";
    await submitForm(form);
    await vi.waitFor(() => expect(api.streamChat).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.streamChat).mock.calls[1][0]).toMatchObject({
      resume_value: { product: "4SS", cause_oper: "PRE METAL CLN" },
    });
  });

  it("keeps partial output after transport failure and retries only on click", async () => {
    const api = fakeApi();
    vi.mocked(api.streamChat)
      .mockImplementationOnce(async (_request, onEvent) => {
        onEvent({ type: "token", content: "부분 답변" });
        throw new Error("socket closed");
      })
      .mockResolvedValueOnce(undefined);
    const { view } = createTestView({ api });
    await view.onOpen();

    await submitChat(view, "원인은?");

    expect(view.containerEl.textContent).toContain("부분 답변");
    expect(view.containerEl.textContent).toContain("socket closed");
    expect(api.streamChat).toHaveBeenCalledTimes(1);
    clickButton(view.containerEl, "다시 시도");
    await vi.waitFor(() => expect(api.streamChat).toHaveBeenCalledTimes(2));
  });

  it("aborts an in-flight stream without rerendering after the view closes", async () => {
    const api = fakeApi();
    vi.mocked(api.streamChat).mockImplementation((_request, _onEvent, signal) => {
      return new Promise<void>((_resolve, reject) => {
        signal?.addEventListener(
          "abort",
          () => {
            const error = new Error("aborted");
            error.name = "AbortError";
            reject(error);
          },
          { once: true },
        );
      });
    });
    const { view } = createTestView({ api });
    await view.onOpen();
    const input = view.containerEl.querySelector<HTMLInputElement>(
      '[data-testid="chat-input"]',
    );
    const form = input?.closest("form");
    if (!input || !form) throw new Error("Chat form not found");
    input.value = "원인은?";
    await submitForm(form);
    await vi.waitFor(() => expect(api.streamChat).toHaveBeenCalledTimes(1));

    await view.onClose();
    await vi.waitFor(() => expect(view.contentEl.childElementCount).toBe(0));
    expect((view as never as { isStreaming: boolean }).isStreaming).toBe(false);
  });

  it("preserves inactive Search and Review DOM while Chat streams in the background", async () => {
    const api = fakeApi();
    let emit: ((event: SseEvent) => void) | undefined;
    let finish: (() => void) | undefined;
    vi.mocked(api.streamChat).mockImplementation((_request, onEvent) => {
      emit = onEvent;
      return new Promise<void>((resolve) => {
        finish = resolve;
      });
    });
    const { view } = createTestView({ api });
    await view.onOpen();
    const chatInput = view.containerEl.querySelector<HTMLInputElement>(
      '[data-testid="chat-input"]',
    );
    const chatForm = chatInput?.closest("form");
    if (!chatInput || !chatForm) throw new Error("Chat form not found");
    chatInput.value = "원인은?";
    await submitForm(chatForm);
    await vi.waitFor(() => expect(api.streamChat).toHaveBeenCalledTimes(1));

    clickButton(view.containerEl, "Search");
    const searchPanel = view.containerEl.querySelector<HTMLElement>(".yield-wiki-panel");
    const searchInput = view.containerEl.querySelector<HTMLInputElement>(
      '[data-testid="search-input"]',
    );
    const product = view.containerEl.querySelector<HTMLInputElement>('input[name="product"]');
    const failType = view.containerEl.querySelector<HTMLInputElement>('input[name="failType"]');
    const causeOper = view.containerEl.querySelector<HTMLInputElement>('input[name="causeOper"]');
    if (!searchPanel || !searchInput || !product || !failType || !causeOper) {
      throw new Error("Search fields not found");
    }
    searchInput.value = "oxide";
    product.value = "4SS";
    failType.value = "EASY";
    causeOper.value = "PRE METAL CLN";
    causeOper.focus();
    searchPanel.scrollTop = 37;
    emit?.({ type: "status", message: "검색 중" });
    emit?.({ type: "token", content: "background", agent: "planner" });

    expect(view.containerEl.querySelector('input[name="causeOper"]')).toBe(causeOper);
    expect(searchInput.value).toBe("oxide");
    expect(product.value).toBe("4SS");
    expect(failType.value).toBe("EASY");
    expect(causeOper.value).toBe("PRE METAL CLN");
    expect(document.activeElement).toBe(causeOper);
    expect(searchPanel.scrollTop).toBe(37);
    expect(
      view.containerEl.querySelector('[role="tab"][aria-selected="true"]')?.textContent,
    ).toBe("Search");

    clickButton(view.containerEl, "Review");
    await vi.waitFor(() => expect(api.listReviews).toHaveBeenCalledTimes(1));
    const reviewPanel = view.containerEl.querySelector<HTMLElement>(".yield-wiki-panel");
    const comment = view.containerEl.querySelector<HTMLTextAreaElement>(
      '[data-testid="review-comment"]',
    );
    if (!reviewPanel || !comment) throw new Error("Review comment not found");
    comment.value = "검토 중인 메모";
    comment.focus();
    reviewPanel.scrollTop = 29;
    emit?.({ type: "token", content: " token", agent: "planner" });

    expect(view.containerEl.querySelector('[data-testid="review-comment"]')).toBe(comment);
    expect(comment.value).toBe("검토 중인 메모");
    expect(document.activeElement).toBe(comment);
    expect(reviewPanel.scrollTop).toBe(29);
    expect(
      view.containerEl.querySelector('[role="tab"][aria-selected="true"]')?.textContent,
    ).toBe("Review");

    finish?.();
    await vi.waitFor(() => {
      expect((view as never as { isStreaming: boolean }).isStreaming).toBe(false);
    });
  });

  it("ignores stale events and finalization after starting a new session and stream", async () => {
    const api = fakeApi();
    const emitters: Array<(event: SseEvent) => void> = [];
    const completions: Array<{
      resolve: () => void;
      reject: (error: Error) => void;
    }> = [];
    vi.mocked(api.streamChat).mockImplementation((_request, onEvent) => {
      emitters.push(onEvent);
      return new Promise<void>((resolve, reject) => {
        completions.push({ resolve, reject });
      });
    });
    const { view } = createTestView({ api });
    await view.onOpen();
    const firstInput = view.containerEl.querySelector<HTMLInputElement>(
      '[data-testid="chat-input"]',
    );
    const firstForm = firstInput?.closest("form");
    if (!firstInput || !firstForm) throw new Error("First Chat form not found");
    firstInput.value = "첫 요청";
    await submitForm(firstForm);
    await vi.waitFor(() => expect(api.streamChat).toHaveBeenCalledTimes(1));

    clickButton(view.containerEl, "새 대화");
    const secondInput = view.containerEl.querySelector<HTMLInputElement>(
      '[data-testid="chat-input"]',
    );
    const secondForm = secondInput?.closest("form");
    if (!secondInput || !secondForm) throw new Error("Second Chat form not found");
    secondInput.value = "새 요청";
    await submitForm(secondForm);
    await vi.waitFor(() => expect(api.streamChat).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.streamChat).mock.calls[0][2]?.aborted).toBe(true);

    emitters[1]({ type: "token", content: "새 답변", agent: "planner" });
    emitters[0]({ type: "token", content: "오래된 답변", agent: "planner" });
    completions[0].reject(new Error("오래된 스트림 실패"));
    await Promise.resolve();
    await Promise.resolve();

    expect(view.containerEl.textContent).toContain("새 답변");
    expect(view.containerEl.textContent).not.toContain("오래된 답변");
    expect(view.containerEl.textContent).not.toContain("오래된 스트림 실패");
    expect((view as never as { isStreaming: boolean }).isStreaming).toBe(true);

    emitters[1]({
      type: "message",
      content: "새 답변 완료",
      agent: "planner",
      citations: [],
    });
    emitters[1]({ type: "stream_end", total_steps: 1, elapsed: 0.4 });
    completions[1].resolve();
    await vi.waitFor(() => {
      expect((view as never as { isStreaming: boolean }).isStreaming).toBe(false);
    });
    expect(view.containerEl.textContent).toContain("새 답변 완료");
  });

  it("labels keyword fallback and opens a Concept result", async () => {
    const { view, api, workspace } = createTestView();
    await view.onOpen();

    await openSearch(view, "oxide");

    expect(api.search).toHaveBeenCalledWith(expect.objectContaining({ query: "oxide" }));
    expect(view.containerEl.textContent).toContain("키워드 검색으로 대체됨");
    expect(view.containerEl.textContent).toContain("Product");
    expect(view.containerEl.textContent).toContain("Fail");
    expect(view.containerEl.textContent).toContain("Operation");
    expect(view.containerEl.textContent).toContain("Sources 1");
    clickButton(view.containerEl, "4SS_PRE_METAL_CLN_EASY");
    expect(workspace.openLinkText).toHaveBeenCalledWith(
      "concepts/4SS_PRE_METAL_CLN_EASY",
      "",
      false,
    );
  });

  it("distinguishes source-only evidence and exposes only actual links", async () => {
    const api = fakeApi();
    vi.mocked(api.search).mockResolvedValue({
      query: "source",
      retrieval_mode: "hybrid",
      results: [
        {
          concept_id: null,
          concept_path: null,
          concept_status: "source_only",
          product: "4SS",
          fail_type: "EASY",
          cause_oper: "ETCH",
          retrieval_mode: "hybrid",
          score: 0.5,
          evidence: [
            { doc_id: "FH-1", content: "no links", score: 0.5 },
            {
              doc_id: "FH-2",
              content: "source note",
              source_path: "sources/FH-2.md",
              download_url: "javascript:alert(1)",
              score: 0.4,
            },
          ],
        },
      ],
    });
    const { view } = createTestView({ api });
    await view.onOpen();

    clickButton(view.containerEl, "Search");
    const input = view.containerEl.querySelector<HTMLInputElement>(
      '[data-testid="search-input"]',
    );
    const form = input?.closest("form");
    if (!input || !form) throw new Error("Search form not found");
    input.value = "source";
    await submitForm(form);
    await vi.waitFor(() => expect(view.containerEl.textContent).toContain("Source only"));

    expect(view.containerEl.querySelectorAll("a")).toHaveLength(0);
    expect(view.containerEl.textContent).toContain("FH-1");
    expect(view.containerEl.textContent).toContain("FH-2");
    expect(view.containerEl.textContent).toContain("Source 노트");
  });

  it("loads Related Notes for the active Concept and opens a result", async () => {
    const api = fakeApi();
    const related: PluginRelatedResponse = {
      note_path: "concepts/4SS_PRE_METAL_CLN_EASY.md",
      outgoing: [
        { path: "sources/FH-1.md", label: "FH-1 Source", node_type: "source" },
      ],
      backlinks: [
        { path: "reviews/operator.md", label: "Operator Review", node_type: "review" },
      ],
    };
    vi.mocked(api.related).mockResolvedValue(related);
    const { view, workspace } = createTestView({
      api,
      activeFile: "concepts/4SS_PRE_METAL_CLN_EASY.md",
      activeConceptId: "concept:4SS|PRE METAL CLN|EASY",
    });
    await view.onOpen();

    clickButton(view.containerEl, "Review");
    await vi.waitFor(() => expect(api.listReviews).toHaveBeenCalledTimes(1));
    clickButton(view.containerEl, "Related Notes");
    await vi.waitFor(() => expect(api.related).toHaveBeenCalledTimes(1));

    expect(api.related).toHaveBeenCalledWith(
      "concepts/4SS_PRE_METAL_CLN_EASY.md",
    );
    expect(view.containerEl.textContent).toContain("Outgoing");
    expect(view.containerEl.textContent).toContain("Backlinks");
    clickButton(view.containerEl, "FH-1 Source");
    expect(workspace.openLinkText).toHaveBeenCalledWith(
      "sources/FH-1",
      "",
      false,
    );
  });

  it("creates a Review for the active Concept and refreshes pending reviews", async () => {
    const api = fakeApi();
    const { view } = createTestView({
      api,
      activeFile: "concepts/4SS_PRE_METAL_CLN_EASY.md",
      activeConceptId: "concept:4SS|PRE METAL CLN|EASY",
    });
    await view.onOpen();
    clickButton(view.containerEl, "Review");
    await vi.waitFor(() => expect(api.listReviews).toHaveBeenCalledTimes(1));
    const reviewer = view.containerEl.querySelector<HTMLInputElement>(
      '[data-testid="review-create-reviewer"]',
    );
    const comment = view.containerEl.querySelector<HTMLTextAreaElement>(
      '[data-testid="review-create-comment"]',
    );
    if (!reviewer || !comment) throw new Error("Review create fields not found");
    reviewer.value = "operator-1";
    comment.value = "근거를 다시 확인해 주세요.";

    clickButton(view.containerEl, "Review 생성");
    await vi.waitFor(() => expect(api.createReview).toHaveBeenCalledTimes(1));

    expect(api.createReview).toHaveBeenCalledWith({
      target_concept_id: "concept:4SS|PRE METAL CLN|EASY",
      reviewer: "operator-1",
      comment: "근거를 다시 확인해 주세요.",
      review_type: "operator_feedback",
    });
    await vi.waitFor(() => expect(api.listReviews).toHaveBeenCalledTimes(2));
  });

  it("refreshes Concept tools on file-open and unregisters the listener", async () => {
    const api = fakeApi();
    vi.mocked(api.related).mockResolvedValue({
      note_path: "concepts/A.md",
      outgoing: [
        { path: "sources/A.md", label: "A-only source", node_type: "source" },
      ],
      backlinks: [],
    });
    const { view, workspace, setActiveConcept, emitFileOpen } = createTestView({
      api,
      activeFile: "concepts/A.md",
      activeConceptId: "concept:A",
    });
    await view.onOpen();
    clickButton(view.containerEl, "Review");
    await vi.waitFor(() => expect(api.listReviews).toHaveBeenCalledTimes(1));
    clickButton(view.containerEl, "Related Notes");
    await vi.waitFor(() => expect(view.containerEl.textContent).toContain("A-only source"));

    const reviewerA = view.containerEl.querySelector<HTMLInputElement>(
      '[data-testid="review-create-reviewer"]',
    );
    const commentA = view.containerEl.querySelector<HTMLTextAreaElement>(
      '[data-testid="review-create-comment"]',
    );
    if (!reviewerA || !commentA) throw new Error("Concept A Review form not found");
    reviewerA.value = "operator-a";
    commentA.value = "A-only draft";

    setActiveConcept("concepts/B.md", "concept:B");
    emitFileOpen();

    expect(workspace.on).toHaveBeenCalledWith("file-open", expect.any(Function));
    expect(view.containerEl.textContent).toContain("concept:B");
    expect(view.containerEl.textContent).not.toContain("concept:A");
    expect(view.containerEl.textContent).not.toContain("A-only source");
    const reviewerB = view.containerEl.querySelector<HTMLInputElement>(
      '[data-testid="review-create-reviewer"]',
    );
    const commentB = view.containerEl.querySelector<HTMLTextAreaElement>(
      '[data-testid="review-create-comment"]',
    );
    if (!reviewerB || !commentB) throw new Error("Concept B Review form not found");
    expect(reviewerB.value).toBe("");
    expect(commentB.value).toBe("");
    reviewerB.value = "operator-b";
    commentB.value = "B review";
    clickButton(view.containerEl, "Review 생성");
    await vi.waitFor(() => expect(api.createReview).toHaveBeenCalledTimes(1));
    expect(api.createReview).toHaveBeenCalledWith({
      target_concept_id: "concept:B",
      reviewer: "operator-b",
      comment: "B review",
      review_type: "operator_feedback",
    });

    const eventRef = workspace.on.mock.results[0]?.value as
      | { callback: () => void }
      | undefined;
    await view.onClose();
    expect(workspace.offref).toHaveBeenCalledWith(eventRef);
    eventRef?.callback();
    expect(view.contentEl.childElementCount).toBe(0);
  });

  it("ignores delayed Concept A Review-create success after switching to B", async () => {
    const api = fakeApi();
    let resolveCreate: ((review: PluginReview) => void) | undefined;
    const delayedCreate = new Promise<PluginReview>((resolve) => {
      resolveCreate = resolve;
    });
    vi.mocked(api.createReview).mockReturnValue(delayedCreate);
    const { view, setActiveConcept, emitFileOpen } = createTestView({
      api,
      activeFile: "concepts/A.md",
      activeConceptId: "concept:A",
    });
    await view.onOpen();
    clickButton(view.containerEl, "Review");
    await vi.waitFor(() => expect(api.listReviews).toHaveBeenCalledTimes(1));
    const reviewerA = view.containerEl.querySelector<HTMLInputElement>(
      '[data-testid="review-create-reviewer"]',
    );
    const commentA = view.containerEl.querySelector<HTMLTextAreaElement>(
      '[data-testid="review-create-comment"]',
    );
    if (!reviewerA || !commentA) throw new Error("Concept A Review form not found");
    reviewerA.value = "operator-a";
    commentA.value = "A review";
    clickButton(view.containerEl, "Review 생성");
    await vi.waitFor(() => expect(api.createReview).toHaveBeenCalledTimes(1));

    setActiveConcept("concepts/B.md", "concept:B");
    emitFileOpen();
    const reviewerB = view.containerEl.querySelector<HTMLInputElement>(
      '[data-testid="review-create-reviewer"]',
    );
    const commentB = view.containerEl.querySelector<HTMLTextAreaElement>(
      '[data-testid="review-create-comment"]',
    );
    if (!reviewerB || !commentB) throw new Error("Concept B Review form not found");
    reviewerB.value = "operator-b draft";
    commentB.value = "B draft must remain";

    resolveCreate?.(pendingReview);
    await delayedCreate;
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(api.listReviews).toHaveBeenCalledTimes(1);
    expect(view.containerEl.textContent).not.toContain("Review를 생성했습니다.");
    expect(
      view.containerEl.querySelector('[data-testid="review-create-reviewer"]'),
    ).toBe(reviewerB);
    expect(reviewerB.value).toBe("operator-b draft");
    expect(commentB.value).toBe("B draft must remain");
    expect(findButton(view.containerEl, "Review 생성").disabled).toBe(false);
  });

  it("ignores delayed Concept A Review-create failure after switching to B", async () => {
    const api = fakeApi();
    let rejectCreate: ((error: Error) => void) | undefined;
    const delayedCreate = new Promise<PluginReview>((_resolve, reject) => {
      rejectCreate = reject;
    });
    vi.mocked(api.createReview).mockReturnValue(delayedCreate);
    const { view, setActiveConcept, emitFileOpen } = createTestView({
      api,
      activeFile: "concepts/A.md",
      activeConceptId: "concept:A",
    });
    await view.onOpen();
    clickButton(view.containerEl, "Review");
    await vi.waitFor(() => expect(api.listReviews).toHaveBeenCalledTimes(1));
    const reviewerA = view.containerEl.querySelector<HTMLInputElement>(
      '[data-testid="review-create-reviewer"]',
    );
    const commentA = view.containerEl.querySelector<HTMLTextAreaElement>(
      '[data-testid="review-create-comment"]',
    );
    if (!reviewerA || !commentA) throw new Error("Concept A Review form not found");
    reviewerA.value = "operator-a";
    commentA.value = "A review";
    clickButton(view.containerEl, "Review 생성");
    await vi.waitFor(() => expect(api.createReview).toHaveBeenCalledTimes(1));

    setActiveConcept("concepts/B.md", "concept:B");
    emitFileOpen();
    const reviewerB = view.containerEl.querySelector<HTMLInputElement>(
      '[data-testid="review-create-reviewer"]',
    );
    const commentB = view.containerEl.querySelector<HTMLTextAreaElement>(
      '[data-testid="review-create-comment"]',
    );
    if (!reviewerB || !commentB) throw new Error("Concept B Review form not found");
    reviewerB.value = "operator-b draft";
    commentB.value = "B draft must remain";

    rejectCreate?.(new Error("CONCEPT-A-FAILURE-SENTINEL"));
    await delayedCreate.catch(() => undefined);
    await Promise.resolve();

    expect(view.containerEl.textContent).not.toContain("CONCEPT-A-FAILURE-SENTINEL");
    expect(
      view.containerEl.querySelector('[data-testid="review-create-reviewer"]'),
    ).toBe(reviewerB);
    expect(reviewerB.value).toBe("operator-b draft");
    expect(commentB.value).toBe("B draft must remain");
    expect(findButton(view.containerEl, "Review 생성").disabled).toBe(false);
  });

  it("ignores delayed Concept A Review-list refresh after switching to B", async () => {
    const api = fakeApi();
    let resolveRefresh: ((reviews: PluginReview[]) => void) | undefined;
    const delayedRefresh = new Promise<PluginReview[]>((resolve) => {
      resolveRefresh = resolve;
    });
    vi.mocked(api.listReviews)
      .mockResolvedValueOnce([pendingReview])
      .mockReturnValueOnce(delayedRefresh);
    const { view, setActiveConcept, emitFileOpen } = createTestView({
      api,
      activeFile: "concepts/A.md",
      activeConceptId: "concept:A",
    });
    await view.onOpen();
    clickButton(view.containerEl, "Review");
    await vi.waitFor(() => expect(api.listReviews).toHaveBeenCalledTimes(1));
    const reviewerA = view.containerEl.querySelector<HTMLInputElement>(
      '[data-testid="review-create-reviewer"]',
    );
    const commentA = view.containerEl.querySelector<HTMLTextAreaElement>(
      '[data-testid="review-create-comment"]',
    );
    if (!reviewerA || !commentA) throw new Error("Concept A Review form not found");
    reviewerA.value = "operator-a";
    commentA.value = "A review";
    clickButton(view.containerEl, "Review 생성");
    await vi.waitFor(() => expect(api.listReviews).toHaveBeenCalledTimes(2));

    setActiveConcept("concepts/B.md", "concept:B");
    emitFileOpen();
    const reviewerB = view.containerEl.querySelector<HTMLInputElement>(
      '[data-testid="review-create-reviewer"]',
    );
    const commentB = view.containerEl.querySelector<HTMLTextAreaElement>(
      '[data-testid="review-create-comment"]',
    );
    if (!reviewerB || !commentB) throw new Error("Concept B Review form not found");
    reviewerB.value = "operator-b draft";
    commentB.value = "B draft must remain";

    resolveRefresh?.([
      {
        ...pendingReview,
        id: "review:concept-a-refresh",
        target_concept_id: "concept:A",
        body_markdown: "CONCEPT-A-REFRESH-SENTINEL",
      },
    ]);
    await delayedRefresh;
    await Promise.resolve();
    await Promise.resolve();

    expect(view.containerEl.textContent).not.toContain(
      "CONCEPT-A-REFRESH-SENTINEL",
    );
    expect(view.containerEl.textContent).not.toContain("Review를 생성했습니다.");
    expect(view.containerEl.textContent).not.toContain("Review 불러오는 중");
    expect(
      view.containerEl.querySelector('[data-testid="review-create-reviewer"]'),
    ).toBe(reviewerB);
    expect(reviewerB.value).toBe("operator-b draft");
    expect(commentB.value).toBe("B draft must remain");
    expect(findButton(view.containerEl, "Review 생성").disabled).toBe(false);
  });

  it("keeps Review creation locked across repeated same-Concept file-open", async () => {
    const api = fakeApi();
    let resolveCreate: ((review: PluginReview) => void) | undefined;
    const delayedCreate = new Promise<PluginReview>((resolve) => {
      resolveCreate = resolve;
    });
    vi.mocked(api.createReview).mockReturnValue(delayedCreate);
    const { view, emitFileOpen } = createTestView({
      api,
      activeFile: "concepts/A.md",
      activeConceptId: "concept:A",
    });
    await view.onOpen();
    clickButton(view.containerEl, "Review");
    await vi.waitFor(() => expect(api.listReviews).toHaveBeenCalledTimes(1));
    const reviewer = view.containerEl.querySelector<HTMLInputElement>(
      '[data-testid="review-create-reviewer"]',
    );
    const comment = view.containerEl.querySelector<HTMLTextAreaElement>(
      '[data-testid="review-create-comment"]',
    );
    if (!reviewer || !comment) throw new Error("Review create fields not found");
    reviewer.value = "operator-a";
    comment.value = "single Review";
    clickButton(view.containerEl, "Review 생성");
    await vi.waitFor(() => expect(api.createReview).toHaveBeenCalledTimes(1));

    emitFileOpen();
    const duplicateReviewer = view.containerEl.querySelector<HTMLInputElement>(
      '[data-testid="review-create-reviewer"]',
    );
    const duplicateComment = view.containerEl.querySelector<HTMLTextAreaElement>(
      '[data-testid="review-create-comment"]',
    );
    if (!duplicateReviewer || !duplicateComment) {
      throw new Error("Repeated Concept Review form not found");
    }
    duplicateReviewer.value = "operator-a-duplicate";
    duplicateComment.value = "must not submit";
    const duplicateSubmit = findButton(view.containerEl, "Review 생성");
    expect(duplicateSubmit.disabled).toBe(true);
    duplicateSubmit.click();
    expect(api.createReview).toHaveBeenCalledTimes(1);

    resolveCreate?.(pendingReview);
    await delayedCreate;
    await vi.waitFor(() => expect(api.listReviews).toHaveBeenCalledTimes(2));
    expect(api.createReview).toHaveBeenCalledTimes(1);
  });

  it("reloads a Review after a version conflict without resending the update", async () => {
    const api = fakeApi();
    vi.mocked(api.updateReview).mockRejectedValue(new ApiError(409, "conflict"));
    const { view } = createTestView({ api });
    await view.onOpen();
    clickButton(view.containerEl, "Review");
    await vi.waitFor(() => expect(api.listReviews).toHaveBeenCalledTimes(1));

    const reviewer = view.containerEl.querySelector<HTMLInputElement>(
      '[data-testid="reviewer-input"]',
    );
    const comment = view.containerEl.querySelector<HTMLTextAreaElement>(
      '[data-testid="review-comment"]',
    );
    if (!reviewer || !comment) throw new Error("Review inputs not found");
    reviewer.value = "operator-1";
    comment.value = "근거 확인";
    clickButton(view.containerEl, "승인");
    await vi.waitFor(() => expect(api.listReviews).toHaveBeenCalledTimes(2));

    expect(api.updateReview).toHaveBeenCalledTimes(1);
    expect(api.updateReview).toHaveBeenCalledWith(pendingReview.id, {
      status: "approved",
      reviewer: "operator-1",
      comment: "근거 확인",
      expected_version: 2,
    });
    expect(view.containerEl.textContent).toContain("다른 사용자가 먼저 변경했습니다");
    expect(view.containerEl.textContent).toContain("pending → rejected");
    expect(view.containerEl.textContent).toContain("operator-0");
    expect(view.containerEl.textContent).toContain("원본 재확인");
  });

  it("guards both Review decisions against repeated clicks while one PATCH is active", async () => {
    const api = fakeApi();
    let finishUpdate: ((review: PluginReview) => void) | undefined;
    vi.mocked(api.updateReview).mockImplementation(
      () =>
        new Promise<PluginReview>((resolve) => {
          finishUpdate = resolve;
        }),
    );
    const { view } = createTestView({ api });
    await view.onOpen();
    clickButton(view.containerEl, "Review");
    await vi.waitFor(() => expect(api.listReviews).toHaveBeenCalledTimes(1));
    const reviewer = view.containerEl.querySelector<HTMLInputElement>(
      '[data-testid="reviewer-input"]',
    );
    const comment = view.containerEl.querySelector<HTMLTextAreaElement>(
      '[data-testid="review-comment"]',
    );
    if (!reviewer || !comment) throw new Error("Review inputs not found");
    reviewer.value = "operator-1";
    comment.value = "근거 확인";
    const approve = findButton(view.containerEl, "승인");
    const reject = findButton(view.containerEl, "반려");

    approve.click();
    reject.click();
    approve.click();

    expect(api.updateReview).toHaveBeenCalledTimes(1);
    expect(approve.disabled).toBe(true);
    expect(reject.disabled).toBe(true);
    finishUpdate?.({ ...pendingReview, status: "approved", version: 3 });
    await vi.waitFor(() => expect(api.listReviews).toHaveBeenCalledTimes(2));
  });

  it("keeps stale Review data and reports refresh failure after a 409 conflict", async () => {
    const api = fakeApi();
    vi.mocked(api.listReviews)
      .mockResolvedValueOnce([pendingReview])
      .mockRejectedValueOnce(new Error("refresh offline"));
    vi.mocked(api.updateReview).mockRejectedValue(new ApiError(409, "conflict"));
    const { view } = createTestView({ api });
    await view.onOpen();
    clickButton(view.containerEl, "Review");
    await vi.waitFor(() => expect(api.listReviews).toHaveBeenCalledTimes(1));
    const reviewer = view.containerEl.querySelector<HTMLInputElement>(
      '[data-testid="reviewer-input"]',
    );
    if (!reviewer) throw new Error("Reviewer input not found");
    reviewer.value = "operator-1";

    clickButton(view.containerEl, "승인");
    await vi.waitFor(() => expect(api.listReviews).toHaveBeenCalledTimes(2));

    expect(api.updateReview).toHaveBeenCalledTimes(1);
    expect(view.containerEl.textContent).toContain("다른 사용자가 먼저 변경했습니다");
    expect(view.containerEl.textContent).toContain("새로고침 실패");
    expect(view.containerEl.textContent).toContain("refresh offline");
    expect(view.containerEl.textContent).not.toContain("최신 Review를 불러왔습니다");
    expect(view.containerEl.textContent).toContain("FH-1 근거를 검토하세요.");
  });
});

describe("YieldWikiSettingTab", () => {
  it("keeps only local server/token settings and masks the token input", async () => {
    const saveSettings = vi.fn().mockResolvedValue(undefined);
    const plugin = {
      settings: { serverUrl: "http://localhost:8001", apiToken: "secret-value" },
      saveSettings,
    };
    const tab = new YieldWikiSettingTab({} as never, plugin as never);

    tab.display();

    const inputs = tab.containerEl.querySelectorAll("input");
    expect(inputs).toHaveLength(2);
    expect(inputs[1].type).toBe("password");
    inputs[0].value = "http://localhost:9000";
    inputs[0].dispatchEvent(new Event("input"));
    await vi.waitFor(() => expect(saveSettings).toHaveBeenCalledTimes(1));
    expect(plugin.settings).toEqual({
      serverUrl: "http://localhost:9000",
      apiToken: "secret-value",
    });
  });
});
