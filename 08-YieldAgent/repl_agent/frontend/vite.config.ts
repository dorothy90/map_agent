import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// /repl/* 요청을 backend(기존 agent_server, port 8001)로 프록시.
// agent_server.py 가 이미 CORS 로 localhost:5173 을 허용하지만, 프록시로 보내면
// CORS 고려 없이도 동작하므로 개발 편의상 프록시 사용.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
  server: {
    port: 5173,
    proxy: {
      "/repl": {
        target: "http://127.0.0.1:8001",
        changeOrigin: true,
      },
    },
  },
});
