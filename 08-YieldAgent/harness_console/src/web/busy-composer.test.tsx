import { AssistantRuntimeProvider, type ThreadMessageLike, useExternalStoreRuntime } from '@assistant-ui/react'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { ChatBar } from '@/app/chat/composer'
import { RICH_INPUT_SLOT } from '@/app/chat/composer/rich-editor'
import type { ChatBarProps, ChatBarState } from '@/app/chat/composer/types'
import { stubThreadEnvironment, stubThreadViewportSize } from '@/components/assistant-ui/test-utils'
import { I18nProvider } from '@/i18n'
import { $busyInputConfig } from '@/store/busy-input-mode'
import { clearSessionDraft } from '@/store/composer'
import { clearQueuedPrompts } from '@/store/composer-queue'
import { $gatewayState, setSessionsLoading } from '@/store/session'
import { HermesGateway } from '@/api/client'

import { WebPresentationProvider } from './presentation'
import { replaceBusyWebPrompt } from './use-web-session'

const sessionId = 'busy-web-session'
const state: ChatBarState = {
  model: { canSwitch: false, model: '', provider: '' },
  tools: { enabled: false, label: '' },
  voice: { enabled: false, active: false }
}

stubThreadEnvironment()
stubThreadViewportSize()

beforeEach(() => {
  $gatewayState.set('open')
  setSessionsLoading(false)
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  $busyInputConfig.set(null)
  $gatewayState.set('closed')
  setSessionsLoading(true)
  clearQueuedPrompts(sessionId)
  clearSessionDraft(sessionId)
})

function mountComposer(configured: 'steer' | 'queue' | 'interrupt' = 'steer') {
  const gateway = new HermesGateway()
  const request = vi.spyOn(gateway, 'request').mockResolvedValue({ value: configured })
  const onSteer = vi.fn<NonNullable<ChatBarProps['onSteer']>>().mockResolvedValue(true)
  const onSubmit = vi.fn<ChatBarProps['onSubmit']>().mockResolvedValue(true)

  function Harness({ busy }: { busy: boolean }) {
    const runtime = useExternalStoreRuntime({
      convertMessage: (message: ThreadMessageLike) => message,
      messages: [] as ThreadMessageLike[],
      isRunning: busy,
      onNew: async () => {}
    })
    return (
      <AssistantRuntimeProvider runtime={runtime}>
        <MemoryRouter>
          <I18nProvider configClient={null} initialLocale="en">
            <WebPresentationProvider>
              <ChatBar
                busy={busy}
                disabled={false}
                gateway={gateway}
                onCancel={vi.fn()}
                onSteer={onSteer}
                onSubmit={onSubmit}
                queueSessionKey={sessionId}
                sessionId={sessionId}
                state={state}
              />
            </WebPresentationProvider>
          </I18nProvider>
        </MemoryRouter>
      </AssistantRuntimeProvider>
    )
  }

  const view = render(<Harness busy />)
  const editor = view.container.querySelector<HTMLElement>(`[data-slot="${RICH_INPUT_SLOT}"]`)!
  const enter = (text: string, keys: { ctrlKey?: boolean; metaKey?: boolean } = {}) => {
    editor.textContent = text
    fireEvent.keyDown(editor, { key: 'Enter', ...keys })
  }

  return { editor, enter, onSteer, onSubmit, request, setBusy: (busy: boolean) => view.rerender(<Harness busy={busy} />) }
}

it('keeps the busy editor enabled and sends plain Enter as steer', async () => {
  const { editor, enter, onSteer, onSubmit } = mountComposer()
  expect(editor.getAttribute('contenteditable')).toBe('true')
  enter('Use the earlier date')
  await waitFor(() => expect(onSteer).toHaveBeenCalledExactlyOnceWith('Use the earlier date', 'steer'))
  expect(onSubmit).not.toHaveBeenCalled()
})

it('queues modified Enter, removes a visible row, and drains the rest in order', async () => {
  const { enter, onSteer, onSubmit, setBusy } = mountComposer()
  enter('first', { ctrlKey: true })
  enter('remove this', { metaKey: true })
  enter('last', { ctrlKey: true })

  const removeRow = (await screen.findByText('remove this')).closest('[data-slot="status-row"]')
  expect(removeRow).not.toBeNull()
  fireEvent.click(within(removeRow as HTMLElement).getByRole('button', { name: 'Delete' }))
  expect(screen.queryByText('remove this')).toBeNull()
  expect(onSubmit).not.toHaveBeenCalled()
  expect(onSteer).not.toHaveBeenCalled()

  setBusy(false)
  await waitFor(() => {
    expect(onSubmit.mock.calls.map(([text]) => text)).toEqual(['first'])
    expect(screen.queryByText('first')).toBeNull()
  })
  setBusy(true)
  setBusy(false)
  await waitFor(() => expect(onSubmit.mock.calls.map(([text]) => text)).toEqual(['first', 'last']))
  for (const [, options] of onSubmit.mock.calls) expect(options?.fromQueue).toBe(true)
})


it('keeps the draft when interrupt-and-send fails, without admitting it to the queue', async () => {
  const { editor, enter, onSteer, onSubmit, setBusy } = mountComposer('interrupt')
  onSteer.mockResolvedValue(false)
  await waitFor(() => expect(screen.getByRole<HTMLSelectElement>('combobox').value).toBe('interrupt'))
  enter('Keep these words')
  await waitFor(() => {
    expect(onSteer).toHaveBeenCalledExactlyOnceWith('Keep these words', 'interrupt')
    expect(editor.textContent).toBe('Keep these words')
  })
  setBusy(false)
  expect(onSubmit).not.toHaveBeenCalled()
  expect(screen.queryByRole('button', { name: /queued/i })).toBeNull()
})

it('waits for the existing Stop action and a server idle snapshot before submitting', async () => {
  vi.useFakeTimers()
  const request = vi.fn()
    .mockResolvedValueOnce({ running: true })
    .mockResolvedValueOnce({ running: false })
  const submit = vi.fn().mockResolvedValue(true)
  const stop = vi.fn(async (waitForIdle: (id: string, result: unknown) => Promise<void>) => {
    await waitForIdle('chat', { execution_state: 'running' })
    return true
  })
  const accepted = replaceBusyWebPrompt({ stop, request, stillSelected: () => true, submit })
  await vi.advanceTimersByTimeAsync(0)
  expect(stop).toHaveBeenCalledOnce()
  expect(request).toHaveBeenCalledExactlyOnceWith('session.resume', { session_id: 'chat', omit_messages: true }, 2_500)
  expect(submit).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(500)
  expect(await accepted).toBe(true)
  expect(submit).toHaveBeenCalledOnce()
})

it('does not submit to a chat that was left while stopping', async () => {
  const request = vi.fn()
  const submit = vi.fn()
  const stop = vi.fn(async (waitForIdle: (id: string, result: unknown) => Promise<void>) => {
    await waitForIdle('chat', { execution_state: 'idle' })
    return true
  })
  expect(await replaceBusyWebPrompt({ stop, request, stillSelected: () => false, submit })).toBe(false)
  expect(request).not.toHaveBeenCalled()
  expect(submit).not.toHaveBeenCalled()
})


it('does not submit when Stop fails', async () => {
  const request = vi.fn()
  const submit = vi.fn()
  const stop = vi.fn().mockResolvedValue(false)
  expect(await replaceBusyWebPrompt({ stop, request, stillSelected: () => true, submit })).toBe(false)
  expect(request).not.toHaveBeenCalled()
  expect(submit).not.toHaveBeenCalled()
})
