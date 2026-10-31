import { act, cleanup, render, renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { useSlashCompletions } from '../hermes/app/chat/composer/hooks/use-slash-completions'
import { useServerSessionSearch } from '../hermes/app/chat/sidebar/use-server-session-search'
import { useSubagentSnapshot } from '../hermes/app/chat/composer/status-stack/use-subagent-snapshot'
import { ChatView } from '../hermes/app/chat'
import { ChatSidebar } from '../hermes/app/chat/sidebar'
import { SidebarProvider } from '../hermes/components/ui/sidebar'
import { stubThreadEnvironment } from '../hermes/components/assistant-ui/test-utils'
import { HermesGateway } from '../hermes/hermes'
import { installWebApiBridge } from '../hermes/platform-bridge'
import { capabilityError, restCapabilityError } from './capability-gate'
import { $gatewayState, $activeSessionId, setSessions } from '../hermes/store/session'
import { $gateway } from '../hermes/store/gateway'

stubThreadEnvironment()
let release: (() => void) | undefined
const noop = () => {}
let fetchCalls: ReturnType<typeof vi.spyOn>
let socketCalls: ReturnType<typeof vi.spyOn>
let eventCalls: ReturnType<typeof vi.spyOn>
beforeEach(() => {
  fetchCalls = vi.spyOn(globalThis, 'fetch')
  socketCalls = vi.spyOn(globalThis, 'WebSocket')
  eventCalls = vi.spyOn(globalThis, 'EventSource')
})
afterEach(() => {
  expect(fetchCalls).not.toHaveBeenCalled()
  expect(socketCalls).not.toHaveBeenCalled()
  expect(eventCalls).not.toHaveBeenCalled()
  expect(document.querySelector('link[data-hermes-theme-font], style#hermes-desktop-custom-css')).toBeNull()
})
afterEach(() => { cleanup(); release?.(); release = undefined; $gatewayState.set('closed'); $activeSessionId.set(null); $gateway.set(null); setSessions([]); vi.useRealTimers(); vi.restoreAllMocks() })

it('does not warm unimplemented completion, search, or subagent RPCs after connection and timers', async () => {
  vi.useFakeTimers()
  const gateway = new HermesGateway()
  const request = vi.spyOn(gateway, 'request').mockRejectedValue(new Error('unexpected RPC'))
  const api = vi.fn(async (_request: import('../hermes/global').HermesApiRequest): Promise<never> => { throw new Error('unexpected REST') })
  release = installWebApiBridge({ api })
  renderHook(() => {
    useSlashCompletions({ gateway, sessionId: 'session-a' })
    useServerSessionSearch('remembered query', 'default')
    useSubagentSnapshot('session-a')
  })
  await act(async () => { $gatewayState.set('open'); await vi.advanceTimersByTimeAsync(11000) })
  expect(request).not.toHaveBeenCalled()
  expect(api).not.toHaveBeenCalled()
})

it('mounts the real ChatView, Thread and composer without transport or native calls through timers', async () => {
  vi.useFakeTimers()
  const api = vi.fn(async (_request: import('../hermes/global').HermesApiRequest): Promise<never> => { throw new Error('unexpected REST') })
  release = installWebApiBridge({ api })
  const gateway = new HermesGateway()
  const request = vi.spyOn(gateway, 'request').mockRejectedValue(new Error('unexpected RPC'))
  $gatewayState.set('open')
  $gateway.set(gateway)
  $activeSessionId.set('session-a')
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const { container } = render(<QueryClientProvider client={client}><MemoryRouter>
    <ChatView gateway={gateway} onToggleSelectedPin={noop} onDeleteSelectedSession={noop} onCancel={noop}
      onAddContextRef={noop} onAddUrl={noop} onAttachImageBlob={noop} onAttachDroppedItems={noop}
      onPasteClipboardImage={noop} onPickFiles={noop} onPickFolders={noop} onPickImages={noop}
      onRemoveAttachment={noop} onSteer={() => false} onSubmit={() => false} onThreadMessagesChange={noop}
      onEdit={async () => {}} onReload={async () => {}} onRetryResume={noop} />
  </MemoryRouter></QueryClientProvider>)
  await act(async () => { await vi.advanceTimersByTimeAsync(11000) })
  expect(container.querySelector('form')).toBeTruthy()
  for (const [call] of api.mock.calls) expect(restCapabilityError(call.path, call.method ?? 'GET')).toBeNull()
  for (const [method] of request.mock.calls) expect(capabilityError(method, 'legacy')).toBeNull()
  client.clear()
})

it('mounts the real session sidebar without transport', async () => {
  vi.useFakeTimers()
  $gatewayState.set('open')
  setSessions([{ id: 'stored-a', title: 'Saved session', started_at: 0, last_active: 0, ended_at: null, message_count: 1, tool_call_count: 0, input_tokens: 0, output_tokens: 0, model: null, source: 'cli', preview: null, is_active: true, git_repo_root: '/offline/repo' }])
  const api = vi.fn(async (_request: import('../hermes/global').HermesApiRequest): Promise<never> => { throw new Error('unexpected REST') })
  release = installWebApiBridge({ api })
  render(<MemoryRouter><SidebarProvider><ChatSidebar currentView="chat" onNavigate={noop}
    onLoadMoreSessions={noop} onRetrySessions={noop} onResumeSession={noop} onDeleteSession={noop}
    onArchiveSession={noop} onBranchSession={noop} onNewSessionInWorkspace={noop} onNewSessionSplit={noop}
    onManageCronJob={noop} onTriggerCronJob={async () => {}} /></SidebarProvider></MemoryRouter>)
  await act(async () => { await vi.advanceTimersByTimeAsync(11000) })
  expect(api).not.toHaveBeenCalled()
})
