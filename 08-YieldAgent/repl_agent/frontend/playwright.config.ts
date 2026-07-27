import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";

const backendDirectory = fileURLToPath(new URL("../..", import.meta.url));

export default defineConfig({
  timeout: 180_000,
  use: {
    baseURL: "http://127.0.0.1:5174",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "npm run dev -- --host 127.0.0.1 --port 5174",
      url: "http://127.0.0.1:5174",
      reuseExistingServer: false,
    },
    {
      command: "../.venv/bin/python -m uvicorn agent_server:app --host 127.0.0.1 --port 8001",
      cwd: backendDirectory,
      url: "http://127.0.0.1:8001/repl/health",
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
