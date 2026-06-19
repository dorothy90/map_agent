import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// agent_server(:8001) 로 프록시 — 프록시 경유라 CORS 무관.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      "/chat": "http://127.0.0.1:8002",
      "/session": "http://127.0.0.1:8002",
      "/sessions": "http://127.0.0.1:8002",
      "/download": "http://127.0.0.1:8002",
      "/mining": "http://127.0.0.1:8002",
    },
  },
});
