import { App, Plugin, PluginSettingTab, Setting } from "obsidian";

import { ApiError, YieldWikiApi } from "./api";
import type { PluginSettings } from "./types";

export type YieldWikiSettings = PluginSettings;

export const DEFAULT_SETTINGS: YieldWikiSettings = {
  serverUrl: "http://localhost:8001",
  apiToken: "",
};

export interface YieldWikiSettingsPlugin extends Plugin {
  settings: YieldWikiSettings;
  saveSettings(): Promise<void>;
}

function connectionMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 401) {
    return "인증 실패 · API 토큰을 확인하세요.";
  }
  return error instanceof Error
    ? `연결 실패 · ${error.message}`
    : "Backend에 연결할 수 없습니다.";
}

export class YieldWikiSettingTab extends PluginSettingTab {
  constructor(
    app: App,
    private readonly yieldWikiPlugin: YieldWikiSettingsPlugin,
  ) {
    super(app, yieldWikiPlugin);
  }

  display(): void {
    const { containerEl } = this;
    containerEl.replaceChildren();

    new Setting(containerEl)
      .setName("Server URL")
      .setDesc("Yield Wiki Backend 주소")
      .addText((text) => {
        text
          .setPlaceholder(DEFAULT_SETTINGS.serverUrl)
          .setValue(this.yieldWikiPlugin.settings.serverUrl)
          .onChange(async (value) => {
            this.yieldWikiPlugin.settings.serverUrl = value.trim();
            await this.yieldWikiPlugin.saveSettings();
          });
        text.inputEl.setAttribute("aria-label", "Server URL");
      });

    new Setting(containerEl)
      .setName("API token")
      .setDesc("Plugin local data에만 저장됩니다.")
      .addText((text) => {
        text.inputEl.type = "password";
        text.inputEl.autocomplete = "off";
        text
          .setPlaceholder("Bearer token")
          .setValue(this.yieldWikiPlugin.settings.apiToken)
          .onChange(async (value) => {
            this.yieldWikiPlugin.settings.apiToken = value;
            await this.yieldWikiPlugin.saveSettings();
          });
        text.inputEl.setAttribute("aria-label", "API token");
      });

    const resultEl = document.createElement("div");
    resultEl.className = "yield-wiki-settings-status";
    resultEl.setAttribute("role", "status");
    resultEl.textContent = "연결 상태를 확인할 수 있습니다.";

    new Setting(containerEl)
      .setName("Backend 연결")
      .setDesc("인증과 Backend 응답을 확인합니다.")
      .addButton((button) =>
        button
          .setButtonText("연결 테스트")
          .setCta()
          .onClick(async () => {
            resultEl.textContent = "연결 확인 중…";
            button.setDisabled(true);
            try {
              await new YieldWikiApi(this.yieldWikiPlugin.settings).health();
              resultEl.textContent = "연결됨";
            } catch (error) {
              resultEl.textContent = connectionMessage(error);
            } finally {
              button.setDisabled(false);
            }
          }),
      );
    containerEl.append(resultEl);
  }
}
