import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useState } from 'react'

import type { GatewayRequester } from '@/app/contrib/types'
import {
  $busyInputConfig,
  type BusyInputMode,
  busyInputOwnerKey,
  normalizeBusyInputMode
} from '@/store/busy-input-mode'
import { $activeGatewayProfile } from '@/store/profile'
import { $connection, $gatewayState, knownSessionOwner, ownerLookupSessionRows } from '@/store/session'
import { requestForSessionProfile } from '@/store/session-request-router'
import { sessionTileOwnerRoute } from '@/store/session-states'

export function useBusyInputMode({
  sessionId,
  storedSessionId,
  requestGateway,
  principalScoped = false
}: {
  sessionId: string | null
  storedSessionId: string | null
  requestGateway: GatewayRequester
  principalScoped?: boolean
}) {
  const config = useStore($busyInputConfig)
  const connection = useStore($connection)
  const profile = useStore($activeGatewayProfile)
  const gatewayState = useStore($gatewayState)

  const route = !principalScoped && storedSessionId
    ? (sessionTileOwnerRoute(storedSessionId) ?? knownSessionOwner(ownerLookupSessionRows(), storedSessionId))
    : undefined

  const connectionId = route && typeof route === 'object' ? route.connectionId : connection?.connectionId
  const ownerProfile = route && typeof route === 'object' ? route.profile : route || profile
  const targetProfile = route && typeof route === 'object' ? route.targetProfile : undefined
  const owner = busyInputOwnerKey(connectionId, targetProfile || ownerProfile)

  const requestSessionId = principalScoped ? null : sessionId
  const [loaded, setLoaded] = useState<{ owner: string; connection: typeof connection; sessionId: string | null; mode: BusyInputMode } | null>(
    null
  )

  const configured = config?.owner === owner && config.connection === connection ? config.mode : null
  const setMode = useCallback(async (mode: BusyInputMode) => {
    const ownerRoute = principalScoped ? undefined
      : connectionId ? { connectionId, profile: ownerProfile, targetProfile } : ownerProfile
    await requestForSessionProfile(ownerRoute, requestGateway, 'config.set', { key: 'busy', value: mode })

    if ($connection.get() === connection) {
      $busyInputConfig.set({ owner, connection, mode })
    }
  }, [connection, connectionId, owner, ownerProfile, principalScoped, requestGateway, targetProfile])

  useEffect(() => {
    if (configured !== null || (!requestSessionId && !principalScoped) || gatewayState !== 'open') {
      return
    }

    let cancelled = false
    const ownerRoute = principalScoped ? undefined
      : connectionId ? { connectionId, profile: ownerProfile, targetProfile } : ownerProfile
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    let attempts = 0

    const load = () => {
      attempts += 1
      void requestForSessionProfile<{ value?: unknown }>(ownerRoute, requestGateway, 'config.get', {
        key: 'busy',
        ...(requestSessionId && { session_id: requestSessionId })
      })
        .then(result => {
          if (!cancelled) {
            setLoaded({ owner, connection, sessionId: requestSessionId, mode: normalizeBusyInputMode(result?.value) })
          }
        })
        .catch(() => {
          // A failed read must not strand busy Enter on an otherwise healthy socket.
          if (!cancelled && attempts < 3) {
            retryTimer = setTimeout(load, 1000 * attempts)
          }
        })
    }

    load()

    return () => {
      cancelled = true
      clearTimeout(retryTimer)
    }
  }, [
    principalScoped,
    configured,
    connection,
    connectionId,
    gatewayState,
    owner,
    ownerProfile,
    requestGateway,
    requestSessionId,
    targetProfile
  ])

  const loadedMode = loaded?.owner === owner && loaded.connection === connection && loaded.sessionId === requestSessionId
    ? loaded.mode
    : 'steer'
  return { mode: gatewayState === 'open' ? configured ?? loadedMode : null, setMode }
}
