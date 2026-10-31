import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import type { TestProject } from 'vitest/node'

export default async function setup(project: TestProject) {
  return setupFixture(project)
}

export async function setupFixture(project: TestProject, realModel = false) {
  if (realModel && process.env.PR8_REAL_MODEL !== '1') throw new Error('PR8_REAL_MODEL opt-in is required')
  if (!process.env.HARNESS_MONGO_URI) throw new Error('HARNESS_MONGO_URI is required; integration tests are opt-in')
  if (typeof WebSocket === 'undefined') throw new Error('Integration tests require Node with native WebSocket and fetch')
  const python = process.env.PR8_PYTHON ?? fileURLToPath(new URL('../../../.venv/bin/python', import.meta.url))
  const child = spawn(python, [fileURLToPath(new URL(realModel ? './pr8_real_model_server.py' : './pr8_gateway_server.py', import.meta.url))], {
    cwd: fileURLToPath(new URL('../../', import.meta.url)),
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' }, detached: realModel, stdio: ['pipe', 'pipe', 'pipe'],
  })
  // Do not relay child stderr: driver diagnostics may contain connection details.
  child.stderr.resume()
  const workers = new Set<number>()
  let buffer = ''
  const ready = new Promise<{ port: number; db: string }>((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('PR8 fixture startup timed out')), 30_000)
    child.once('error', () => { clearTimeout(timeout); reject(new Error('Unable to start PR8 fixture Python')) })
    child.once('exit', () => { clearTimeout(timeout); reject(new Error('PR8 fixture exited before ready')) })
    child.stdout.on('data', chunk => {
      buffer += String(chunk)
      let newline: number
      while ((newline = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, newline)
        buffer = buffer.slice(newline + 1)
        try {
          const value = JSON.parse(line)
          if (realModel && Number.isInteger(value.worker) && value.worker > 1) {
            workers.add(value.worker)
            continue
          }
          if (!Number.isInteger(value.port) || !/^zz_pr8_[a-f0-9]{32}$/.test(value.db)) throw new Error()
          clearTimeout(timeout)
          resolve(value)
        } catch { clearTimeout(timeout); reject(new Error('Invalid PR8 fixture ready record')) }
      }
      if (buffer.length > 4096) { buffer = ''; reject(new Error('Invalid PR8 fixture output')) }
    })
  })
  const killGroup = (pid: number | undefined, signal: NodeJS.Signals) => {
    if (pid === undefined) return
    try { process.kill(-pid, signal) } catch { /* group already exited */ }
  }
  const stop = async () => {
    if (realModel) {
      // EOF first lets ctrl.close() stop native workers and lifespan drop its DB.
      // Native workers own separate process groups, so track and reap those too.
      let term: ReturnType<typeof setTimeout> | undefined
      let kill: ReturnType<typeof setTimeout> | undefined
      try {
        if (child.exitCode === null && child.signalCode === null) {
          const exited = new Promise<void>(resolve => child.once('exit', () => resolve()))
          child.stdin.end()
          term = setTimeout(() => killGroup(child.pid, 'SIGTERM'), 30_000)
          kill = setTimeout(() => {
            for (const pid of workers) killGroup(pid, 'SIGKILL')
            killGroup(child.pid, 'SIGKILL')
          }, 45_000)
          await exited
        }
      } finally {
        clearTimeout(term); clearTimeout(kill)
        for (const pid of workers) killGroup(pid, 'SIGKILL')
        killGroup(child.pid, 'SIGKILL')
      }
      return
    }
    if (child.exitCode !== null) return
    const exited = new Promise<void>(resolve => child.once('exit', () => resolve()))
    child.stdin.end()
    const timer = setTimeout(() => child.kill('SIGTERM'), 10_000)
    try { await exited } finally { clearTimeout(timer) }
  }
  try {
    const info = await ready
    project.provide('fixtureOrigin', `http://127.0.0.1:${info.port}`)
    project.provide('fixtureDatabase', info.db)
  } catch (error) { await stop(); throw error }
  return stop
}

declare module 'vitest' {
  export interface ProvidedContext { fixtureOrigin: string; fixtureDatabase: string }
}
