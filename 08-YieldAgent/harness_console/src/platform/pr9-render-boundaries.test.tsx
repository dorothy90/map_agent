import { SessionRefLink } from '../hermes/components/assistant-ui/directive-text'
import { useEmojiCompletions } from '../hermes/app/chat/composer/hooks/use-emoji-completions'
import { useContextSuggestions } from '../hermes/app/session/hooks/use-context-suggestions'
import { act, cleanup, render, renderHook } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { MarkdownTextContent } from '../hermes/components/assistant-ui/markdown-text'
import { UrlEmbed } from '../hermes/components/assistant-ui/embeds/url-embed'
import { PendingApprovalStack } from '../hermes/components/assistant-ui/tool/approval'
import { ThreadRuntime, stubThreadEnvironment } from '../hermes/components/assistant-ui/test-utils'
import { installWebApiBridge } from '../hermes/platform-bridge'
import { HermesGateway } from '../hermes/hermes'
import { $gateway } from '../hermes/store/gateway'
import { $embedAllowed, $embedMode } from '../hermes/store/embed-consent'
import { $activeSessionId } from '../hermes/store/session'
import { clearAllPrompts, setApprovalRequest } from '../hermes/store/prompts'
import { refreshProjects, refreshProjectTree, $projectsRpcAvailable } from '../hermes/store/projects'

let release: (() => void) | undefined
afterEach(() => {
  cleanup()
  release?.()
  clearAllPrompts()
  $gateway.set(null)
  $activeSessionId.set(null)
  $embedMode.set('ask')
  $embedAllowed.set([])
  vi.useRealTimers()
  vi.restoreAllMocks()
})

it('renders pending approval, text, table and code without mount or timer transport calls', async () => {
  stubThreadEnvironment()
  vi.useFakeTimers()
  const api = vi.fn(async (): Promise<never> => { throw new Error('Unexpected REST') })
  release = installWebApiBridge({ api })
  const gateway = new HermesGateway()
  const request = vi.spyOn(gateway, 'request').mockRejectedValue(new Error('Unexpected RPC'))
  const fetch = vi.spyOn(globalThis, 'fetch')
  $gateway.set(gateway)
  $activeSessionId.set('audit-session')
  setApprovalRequest({ sessionId: 'audit-session', requestId: 'audit-approval', command: 'example', description: 'Review example' })
  const { container } = render(<ThreadRuntime messages={[]}>
    <PendingApprovalStack />
    <MarkdownTextContent isRunning={false} text={'Hello\n\n| A | B |\n| - | - |\n| 1 | 2 |\n\n```text\nexample\n```'} />
  </ThreadRuntime>)
  await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
  expect(container.querySelector('[data-approval-stack]')).not.toBeNull()
  expect(container.querySelector('table')).not.toBeNull()
  expect(container.textContent).toContain('example')
  expect(api).not.toHaveBeenCalled()
  expect(request).not.toHaveBeenCalled()
  expect(fetch).not.toHaveBeenCalled()
})

it('keeps third-party embeds offline even with saved always-allow preferences', async () => {
  const api = vi.fn(async (): Promise<never> => { throw new Error('Unexpected REST') })
  release = installWebApiBridge({ api })
  const fetch = vi.spyOn(globalThis, 'fetch')
  $embedMode.set('always')
  $embedAllowed.set(['youtube'])
  const { container } = render(<UrlEmbed descriptor={{
    renderer: 'frame', provider: 'youtube', id: 'video', label: 'Video',
    sourceUrl: 'https://www.youtube.com/watch?v=video', embedUrl: 'https://www.youtube.com/embed/video'
  }} />)
  await act(async () => {})
  expect(container.querySelector('iframe')).toBeNull()
  expect(container.querySelector('a')?.textContent).toBeTruthy()
  expect(api).not.toHaveBeenCalled()
  expect(fetch).not.toHaveBeenCalled()
})

it('does not probe project RPCs on connected refresh', async () => {
  const gateway = new HermesGateway()
  const request = vi.spyOn(gateway, 'request').mockRejectedValue(new Error('Unexpected RPC'))
  $gateway.set(gateway)
  await refreshProjects()
  await refreshProjectTree()
  expect($projectsRpcAvailable.get()).toBe(false)
  expect(request).not.toHaveBeenCalled()
})

it('does not fetch missing emoji assets after completion debounce', async () => {
  vi.useFakeTimers()
  const fetch = vi.spyOn(globalThis, 'fetch')
  const { result } = renderHook(() => useEmojiCompletions())
  act(() => { result.current.adapter.search?.('joy') })
  await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
  expect(result.current.adapter.search?.('joy')).toEqual([])
  expect(fetch).not.toHaveBeenCalled()
})

it('does not request unsupported context suggestions on connection or session changes', () => {
  const requestGateway = vi.fn(async (): Promise<never> => { throw new Error('Unexpected RPC') })
  const { rerender } = renderHook(({ session }) => useContextSuggestions({
    activeSessionId: session, activeSessionIdRef: { current: session }, currentCwd: '',
    gatewayState: 'open', requestGateway
  }), { initialProps: { session: 'first' } })
  rerender({ session: 'second' })
  expect(requestGateway).not.toHaveBeenCalled()
})

it('does not look up an unknown session reference through stub REST', async () => {
  const api = vi.fn(async (): Promise<never> => { throw new Error('Unexpected REST') })
  release = installWebApiBridge({ api })
  render(<SessionRefLink value="default/unknown-session" />)
  await act(async () => {})
  expect(api).not.toHaveBeenCalled()
})
