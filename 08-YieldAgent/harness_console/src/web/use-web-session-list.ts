import { useCallback, useEffect, useRef, useState } from 'react'
import { listSessions } from '@/api/sessions'
import { setSessions, setSessionsLoading, setSessionsLoadError } from '@/store/session'

const retryDelays = [1000, 4000, 15000]

function isTemporaryFailure(error: unknown) {
  if (error instanceof TypeError || (error instanceof DOMException && error.name === 'TimeoutError')) return true
  const status = error && typeof error === 'object' && 'status' in error ? error.status : undefined
  return typeof status === 'number' && (status >= 500 || status === 408 || status === 429)
}

/** H use-session-list-actions:328-520, narrowed to a single profile without cron refresh. */
export function useWebSessionList() {
  const [hasMoreSessions, setHasMoreSessions] = useState(false)
  const limit = useRef(40)
  const generation = useRef(0)
  const retryTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const needsRecovery = useRef(false)

  const refreshSessions = useCallback(async () => {
    const request = ++generation.current
    clearTimeout(retryTimer.current)
    setSessionsLoadError(false)

    async function load(attempt: number) {
      needsRecovery.current = false
      setSessionsLoading(true)
      try {
        const result = await listSessions(limit.current, 1, 'exclude', 'recent')
        if (request !== generation.current) return
        setSessions(result.sessions)
        setHasMoreSessions(result.sessions.length < result.total)
        setSessionsLoadError(false)
      } catch (error) {
        if (request !== generation.current) return
        setSessionsLoadError(true)
        needsRecovery.current = isTemporaryFailure(error)
        if (needsRecovery.current && attempt < retryDelays.length) {
          retryTimer.current = setTimeout(() => { void load(attempt + 1) }, retryDelays[attempt])
        }
      } finally {
        if (request === generation.current) setSessionsLoading(false)
      }
    }
    await load(0)
  }, [])

  useEffect(() => {
    const recover = () => { if (needsRecovery.current) void refreshSessions() }
    window.addEventListener('online', recover)
    window.addEventListener('focus', recover)
    return () => {
      window.removeEventListener('online', recover)
      window.removeEventListener('focus', recover)
      clearTimeout(retryTimer.current)
      generation.current++
    }
  }, [refreshSessions])

  const loadMoreSessions = useCallback(async () => { limit.current += 40; await refreshSessions() }, [refreshSessions])
  return {refreshSessions, loadMoreSessions, hasMoreSessions}
}
