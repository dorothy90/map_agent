import { defineConfig } from 'vitest/config'
import { integrationResolve } from './vitest.config'

export default defineConfig({
  resolve: integrationResolve,
  test: {
    environment: 'node',
    setupFiles: ['./src/platform/test-setup.ts'],
    include: ['tests-integration/**/*.itest.ts'],
    exclude: ['tests-integration/real-model.itest.ts'],
    globalSetup: process.argv.includes('list') ? [] : ['./tests-integration/global-setup.ts'],
    fileParallelism: false,
    maxWorkers: 1,
    testTimeout: 20_000,
    hookTimeout: 40_000,
  },
})
