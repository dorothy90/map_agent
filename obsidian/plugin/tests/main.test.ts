// @vitest-environment jsdom

import { describe, expect, it, vi } from "vitest";

vi.mock("obsidian", () => {
  class Plugin {
    app: unknown;
    manifest: unknown;
    loadData = vi.fn();
    saveData = vi.fn().mockResolvedValue(undefined);
    registerView = vi.fn();
    addRibbonIcon = vi.fn();
    addCommand = vi.fn();
    addSettingTab = vi.fn();

    constructor(app: unknown, manifest: unknown) {
      this.app = app;
      this.manifest = manifest;
    }
  }

  return {
    Plugin,
    ItemView: class {
      containerEl = document.createElement("div");
      contentEl = document.createElement("div");

      constructor() {
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
    Setting: class {},
  };
});

import YieldWikiPlugin from "../src/main";
import { YIELD_WIKI_VIEW_TYPE } from "../src/view";

describe("YieldWikiPlugin", () => {
  it("loads only local settings and registers one right-sidebar entry", async () => {
    const leaf = { setViewState: vi.fn().mockResolvedValue(undefined) };
    const workspace = {
      getLeavesOfType: vi.fn().mockReturnValue([]),
      getRightLeaf: vi.fn().mockReturnValue(leaf),
      revealLeaf: vi.fn().mockResolvedValue(undefined),
      detachLeavesOfType: vi.fn(),
    };
    const plugin = new YieldWikiPlugin(
      { workspace } as never,
      {
        id: "yield-wiki",
        name: "Yield Wiki",
        version: "0.1.0",
        minAppVersion: "1.8.0",
        description: "",
        author: "",
      } as never,
    );
    vi.mocked(plugin.loadData).mockResolvedValue({
      serverUrl: "http://localhost:9000",
      apiToken: "local-token",
      ignored: "must-not-be-saved",
    });

    await plugin.onload();

    expect(plugin.settings).toEqual({
      serverUrl: "http://localhost:9000",
      apiToken: "local-token",
    });
    expect(plugin.registerView).toHaveBeenCalledWith(
      YIELD_WIKI_VIEW_TYPE,
      expect.any(Function),
    );
    expect(plugin.addRibbonIcon).toHaveBeenCalledWith(
      "microscope",
      "Yield Wiki 열기",
      expect.any(Function),
    );
    expect(plugin.addCommand).toHaveBeenCalledWith(
      expect.objectContaining({ id: "open-yield-wiki", name: "Yield Wiki 열기" }),
    );
    expect(plugin.addSettingTab).toHaveBeenCalledTimes(1);

    const command = vi.mocked(plugin.addCommand).mock.calls[0][0];
    await command.callback?.();
    expect(workspace.getRightLeaf).toHaveBeenCalledWith(false);
    expect(leaf.setViewState).toHaveBeenCalledWith({
      type: YIELD_WIKI_VIEW_TYPE,
      active: true,
    });
    expect(workspace.revealLeaf).toHaveBeenCalledWith(leaf);

    await plugin.saveSettings();
    expect(plugin.saveData).toHaveBeenCalledWith({
      serverUrl: "http://localhost:9000",
      apiToken: "local-token",
    });
  });
});
