import { Plugin } from "obsidian";

import {
  DEFAULT_SETTINGS,
  YieldWikiSettingTab,
  type YieldWikiSettings,
} from "./settings";
import { YIELD_WIKI_VIEW_TYPE, YieldWikiView } from "./view";

export default class YieldWikiPlugin extends Plugin {
  settings: YieldWikiSettings = { ...DEFAULT_SETTINGS };

  async onload(): Promise<void> {
    await this.loadSettings();
    this.registerView(
      YIELD_WIKI_VIEW_TYPE,
      (leaf) => new YieldWikiView(leaf, this),
    );
    this.addRibbonIcon("microscope", "Yield Wiki 열기", () => {
      void this.activateView();
    });
    this.addCommand({
      id: "open-yield-wiki",
      name: "Yield Wiki 열기",
      callback: () => this.activateView(),
    });
    this.addSettingTab(new YieldWikiSettingTab(this.app, this));
  }

  onunload(): void {
    this.app.workspace.detachLeavesOfType(YIELD_WIKI_VIEW_TYPE);
  }

  async saveSettings(): Promise<void> {
    await this.saveData({
      serverUrl: this.settings.serverUrl,
      apiToken: this.settings.apiToken,
    });
  }

  private async loadSettings(): Promise<void> {
    const data = (await this.loadData()) as Partial<YieldWikiSettings> | null;
    this.settings = {
      serverUrl:
        typeof data?.serverUrl === "string"
          ? data.serverUrl
          : DEFAULT_SETTINGS.serverUrl,
      apiToken:
        typeof data?.apiToken === "string" ? data.apiToken : DEFAULT_SETTINGS.apiToken,
    };
  }

  private async activateView(): Promise<void> {
    const workspace = this.app.workspace;
    const leaf =
      workspace.getLeavesOfType(YIELD_WIKI_VIEW_TYPE)[0] ??
      workspace.getRightLeaf(false);
    if (!leaf) return;
    await leaf.setViewState({ type: YIELD_WIKI_VIEW_TYPE, active: true });
    await workspace.revealLeaf(leaf);
  }
}
