import { defineConfig } from 'vitest/config'
import { fileURLToPath } from 'node:url'
import uiTests from './pr9-ui-tests.json'
import excludedTests from './pr9-excluded-tests.json'
import excludedCases from './pr9-excluded-cases.json'

const escapedCases = Object.keys(excludedCases).map(name => name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
const supportedCases = new RegExp(`^(?!.*(?:${escapedCases.join('|')})).*$`, 's')

// Radix menu and radix-ui carry separate focus-scope copies in the installed lock.
// Share one focus stack so nested dialogs do not repeatedly steal each other's focus.
const resolve = { alias: {
  '../../../../../../tests/fixtures/session-resume-active-turn.json': fileURLToPath(new URL('./src/hermes/test/fixtures/session-resume-active-turn.json', import.meta.url)),
  '@radix-ui/react-focus-scope': fileURLToPath(new URL('./node_modules/radix-ui/node_modules/@radix-ui/react-focus-scope/dist/index.mjs', import.meta.url)),
  '@hermes/shared/billing': fileURLToPath(new URL('./src/protocol/pr9-extra/billing-types.ts', import.meta.url)),
  '@hermes/shared/color': fileURLToPath(new URL('./src/protocol/pr9-extra/color.ts', import.meta.url)),
  '@hermes/shared/skin': fileURLToPath(new URL('./src/protocol/pr9-extra/skin.ts', import.meta.url)),
  '@hermes/shared/translucency': fileURLToPath(new URL('./src/protocol/pr9-extra/translucency.ts', import.meta.url)),
  '@hermes/shared/ansi': fileURLToPath(new URL('./src/protocol/pr9-ansi.ts', import.meta.url)),
  '@hermes/shared/i18n': fileURLToPath(new URL('./src/protocol/i18n.ts', import.meta.url)),
  '@hermes/shared': fileURLToPath(new URL('./src/protocol/pr9-full.ts', import.meta.url)),
  '@': fileURLToPath(new URL('./src/hermes', import.meta.url)),
} }

// pr9-full re-exports the unchanged PR-8 protocol/index contract plus UI symbols.
export const integrationResolve = resolve

export default defineConfig({
  resolve,
  test: { maxWorkers: 4, projects: [
    { resolve, test: {
      name: 'node', environment: 'node',
      include: ['src/web/**/*.test.ts', 'src/yield/**/*.test.ts', 'src/protocol/**/*.test.ts', 'src/hermes/**/*.test.ts', 'src/platform/**/*.test.ts'],
      exclude: [...uiTests, ...Object.keys(excludedTests)],
      setupFiles: ['./src/platform/test-setup.ts', './src/platform/hermes-node-setup.ts', './src/platform/pr9-ui-offline-setup.ts'],
    } },
    { resolve, test: {
      testNamePattern: supportedCases,
      name: 'ui', environment: 'jsdom', server: { deps: { inline: [/radix-ui/] } }, include: [...uiTests, 'src/hermes/app/contrib/hooks/completed-reply-refresh.test.tsx', 'src/hermes/components/confirm-host.test.tsx', 'src/web/**/*.test.tsx', 'src/yield/**/*.test.tsx'],
      setupFiles: ['./src/hermes/vitest.setup.ts', './src/platform/pr9-ui-offline-setup.ts', './src/platform/pr9-upstream-ui-bridge-setup.ts'], globals: true,
      env: { NODE_ENV: 'development' }, testTimeout: 15_000, hookTimeout: 30_000,
      css: { include: [/status-stack\.css$/] },
    } },
  ] },
})
