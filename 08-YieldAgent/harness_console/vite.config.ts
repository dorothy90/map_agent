import { hermesSystemFonts } from './src/web/font-policy';
import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
const resolve = { alias: {
  '@radix-ui/react-focus-scope': fileURLToPath(new URL('./node_modules/radix-ui/node_modules/@radix-ui/react-focus-scope/dist/index.mjs', import.meta.url)),
  '@hermes/shared/billing': fileURLToPath(new URL('./src/protocol/pr9-extra/billing-types.ts', import.meta.url)),
  '@hermes/shared/color': fileURLToPath(new URL('./src/protocol/pr9-extra/color.ts', import.meta.url)),
  '@hermes/shared/skin': fileURLToPath(new URL('./src/protocol/pr9-extra/skin.ts', import.meta.url)),
  '@hermes/shared/translucency': fileURLToPath(new URL('./src/protocol/pr9-extra/translucency.ts', import.meta.url)),
  '@hermes/shared/ansi': fileURLToPath(new URL('./src/protocol/pr9-ansi.ts', import.meta.url)),
  '@hermes/shared/i18n': fileURLToPath(new URL('./src/protocol/i18n.ts', import.meta.url)),
  '@hermes/shared': fileURLToPath(new URL('./src/protocol/pr9-full.ts', import.meta.url)),
  '@': fileURLToPath(new URL('./src/hermes', import.meta.url)),
} };
const gatewayTarget = process.env.HARNESS_GATEWAY_TARGET || 'http://127.0.0.1:8001';
export default defineConfig({
  resolve,
  plugins: [hermesSystemFonts(), react(), tailwindcss()],
  build: { rollupOptions: { output: { manualChunks: { markdown: ['react-markdown', 'remark-gfm', 'rehype-highlight'] } } } },
  server: {
    host: process.env.HARNESS_WEB_HOST || '0.0.0.0', port: 5175, strictPort: true,
    proxy: {
      '/api/auth/ws-ticket': { target: gatewayTarget, changeOrigin: true },
      '/harness/mcp': { target: gatewayTarget, changeOrigin: true },
      '/harness/skills': { target: gatewayTarget, changeOrigin: true },
      '/harness/products': { target: gatewayTarget, changeOrigin: true },
      '/harness/sessions': { target: gatewayTarget, changeOrigin: true },
      '/harness/results': { target: gatewayTarget, changeOrigin: true },
      '/harness/artifacts': { target: gatewayTarget, changeOrigin: true },
      '/api/ws': { target: gatewayTarget, ws: true, changeOrigin: true },
      '/gateway-m1': { target: gatewayTarget, ws: true, changeOrigin: true },
      ...Object.fromEntries(['/harness', '/health'].map(path => [path, {
      target: 'http://127.0.0.1:8001', changeOrigin: true, timeout: 0, proxyTimeout: 0,
      configure(proxy) { proxy.on('proxyRes', response => { if (response.headers['content-type']?.includes('text/event-stream')) { response.headers['cache-control'] = 'no-cache, no-transform'; response.headers['x-accel-buffering'] = 'no'; } }); },
    } satisfies import('vite').ProxyOptions])),
    },
  },
});
