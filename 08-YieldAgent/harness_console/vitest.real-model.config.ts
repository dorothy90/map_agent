import { defineConfig } from 'vitest/config'
import { integrationResolve } from './vitest.config'

export default defineConfig({
  resolve: integrationResolve,
  test: {
    environment: 'node',
    setupFiles: ['./src/platform/test-setup.ts'],
    include: ['tests-integration/real-model.itest.ts'],
    globalSetup: process.argv.includes('list') ? [] : ['./tests-integration/real-model-setup.ts'],
    fileParallelism: false,
    maxWorkers: 1,
    retry: 0,
    testTimeout: 300_000,
    hookTimeout: 60_000,
    onConsoleLog: () => false,
  },
})
