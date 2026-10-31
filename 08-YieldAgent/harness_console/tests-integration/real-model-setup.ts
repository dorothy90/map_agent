import type { TestProject } from 'vitest/node'
import { setupFixture } from './global-setup'

export default async function setup(project: TestProject) {
  if (process.env.PR8_REAL_MODEL !== '1') throw new Error('PR8_REAL_MODEL opt-in is required')
  return setupFixture(project, true)
}
